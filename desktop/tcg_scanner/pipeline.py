from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import asdict, dataclass
from datetime import date
import json
import logging
import time
from urllib.parse import quote, urlsplit

from .api import FetchError
from .db import dumps
from .images import image_path, inspect_image, safe_id

log = logging.getLogger(__name__)


@dataclass
class Progress:
    total: int = 0
    processed: int = 0
    fetched: int = 0
    reused: int = 0
    embedded: int = 0
    skipped: int = 0
    missing_images: int = 0
    failed: int = 0
    metadata_fetched: int = 0
    metadata_cached: int = 0
    discovery_failures: int = 0

    def display(self):
        print(f"Cards {self.processed}/{self.total} | embedded={self.embedded} fetched={self.fetched} reused={self.reused} "
              f"skipped={self.skipped} missing_images={self.missing_images} "
              f"failed={self.failed} metadata_fetched={self.metadata_fetched} "
              f"metadata_cached={self.metadata_cached} discovery_failures={self.discovery_failures}",
              flush=True)


def eligible(data, since):
    series = (data.get("serie") or {}).get("id")
    if series == "tcgp":
        return False
    if series == "sv":
        return True
    released = data.get("releaseDate")
    if not released:
        log.warning("Set %s has no release date; excluded unless explicitly selected", data["id"])
        return False
    return date.fromisoformat(released) >= date.fromisoformat(since)


def discover(config, db, client, progress, *, sync=False, set_ids=()):
    force = sync
    if set_ids:
        briefs = [{"id": x} for x in dict.fromkeys(set_ids)]
    else:
        briefs = db.cached_json(client, client.url("sets"), config.catalog_ttl_hours, force)
        if not isinstance(briefs, list):
            raise ValueError("Expected an array from /sets")
    selected = []
    for index, brief in enumerate(briefs, 1):
        try:
            set_id = safe_id(brief["id"])
            data = db.cached_json(client, client.url("sets/" + quote(set_id, safe="")),
                                  config.catalog_ttl_hours, force)
            if not isinstance(data, dict) or data.get("id") != set_id or not isinstance(data.get("cards"), list):
                raise ValueError(f"Invalid set response for {set_id}")
            if (data.get("serie") or {}).get("id") == "tcgp":
                log.info("Excluding digital Pocket set %s", set_id)
                continue
            if set_ids or eligible(data, config.since):
                db.save_set(config.language, data)
                selected.append(data)
        except (FetchError, ValueError, KeyError, TypeError) as exc:
            progress.discovery_failures += 1
            log.error("Set discovery failed for %s: %s", brief, exc)
        if index % 20 == 0 or index == len(briefs):
            print(f"Catalog {index}/{len(briefs)} sets checked; {len(selected)} selected", flush=True)
    return sorted(selected, key=lambda s: (s.get("releaseDate") or "", s["id"]))


def process_card(config, client, row, brief, *, refresh_metadata=False, refresh_images=False, regenerate=False):
    """Worker returns DB changes; coordinator commits each completed card."""
    values = {}
    metadata_counter = "metadata_cached"
    changed = row["brief_json"] != dumps(brief)
    stale = time.time() - (row["metadata_updated_at"] or 0) >= config.metadata_ttl_hours * 3600
    if refresh_metadata or changed or stale or row["metadata_status"] != "ready":
        try:
            data, _, _ = client.json(client.card_url(row["card_id"]))
            if not isinstance(data, dict) or data.get("id") != row["card_id"]:
                raise ValueError("Card response ID does not match requested ID")
            card_set = data.get("set") or {}
            if card_set.get("id", row["set_id"]) != row["set_id"]:
                raise ValueError("Card response belongs to a different set")
            base_image = data.get("image")
            if base_image is not None and (not isinstance(base_image, str) or
                                          urlsplit(base_image).scheme != "https"):
                raise ValueError("Invalid card image URL")
            values.update(name=data.get("name", brief.get("name")),
                          collector_number=data.get("localId", brief.get("localId")),
                          rarity=data.get("rarity"), image_url=base_image,
                          variants_json=dumps(data.get("variants")),
                          variants_detailed_json=dumps(data.get("variants_detailed")),
                          raw_json=dumps(data), brief_json=dumps(brief),
                          metadata_status="ready", metadata_error=None,
                          metadata_updated_at=time.time())
            metadata_counter = "metadata_fetched"
        except (FetchError, ValueError, TypeError, AttributeError) as exc:
            values.update(metadata_status="failed", metadata_error=str(exc), last_attempt_at=time.time())
            return values, "failed", None
    else:
        base_image = row["image_url"]

    if not base_image:
        # Preserve all legacy image provenance for a later explicit cleanup.
        return values, "missing_images", metadata_counter

    url = base_image.rstrip("/") + "/high." + config.image_format
    embedding = row.get("_embedding")
    if (not refresh_images and not regenerate and embedding and
            embedding["status"] == "ready" and embedding["source_url"] == url):
        return values, "skipped", metadata_counter

    relative = row["local_image_path"] or image_path(config, row["set_id"], row["card_id"])
    path = (config.root / relative).resolve()
    if (not refresh_images and row["downloaded_url"] == url and
            path.is_relative_to(config.root / "images") and path.is_file()):
        try:
            body = path.read_bytes()
            info = inspect_image(body)
            if info["sha256"] == row["sha256"]:
                values["_image"] = (body, {"url": url, "sha256": info["sha256"], "kind": "local"})
                return values, "reused", metadata_counter
        except (OSError, ValueError, SyntaxError):
            pass

    values.update(last_attempt_at=time.time(), download_attempts=row["download_attempts"] + 1)
    try:
        def decode(body):
            try:
                return body, inspect_image(body)
            except (OSError, ValueError, SyntaxError) as exc:
                raise ValueError(f"Invalid image: {exc}") from exc

        (body, info), _, _ = client.get(url, decode=decode)
        # Bytes exist only in the bounded worker/batch queue. No image files are written.
        values["_image"] = (body, {"url": url, "sha256": info["sha256"], "kind": "remote"})
        return values, "fetched", metadata_counter
    except (FetchError, OSError, ValueError) as exc:
        values["_embedding_error"] = str(exc)
        return values, "failed", metadata_counter


def run(config, db, client, *, sync=False, set_ids=(), limit=None,
        refresh_metadata=False, refresh_images=False, regenerate=False, existing=False,
        embedder=None):
    from .embeddings import DinoEmbedder
    from .vectors import VectorStore
    if embedder is None:
        embedder = DinoEmbedder(config)
    store = VectorStore.register(config, db, embedder)
    progress = Progress()
    if existing:
        sets = []
        rows = db.conn.execute("SELECT * FROM cards WHERE language=? ORDER BY id", (config.language,))
        grouped = {}
        for row in rows:
            if not set_ids or row["set_id"] in set_ids:
                grouped.setdefault(row["set_id"], []).append(json.loads(row["brief_json"]))
        sets = [{"id": k, "cards": v} for k, v in grouped.items()]
    else:
        sets = discover(config, db, client, progress, sync=sync, set_ids=set_ids)
    jobs = []
    seen = set()
    for data in sets:
        for brief in data["cards"]:
            try:
                card_id = safe_id(brief["id"])
                if card_id in seen:
                    continue
                seen.add(card_id)
                jobs.append((data["id"], brief))
            except (KeyError, ValueError, TypeError) as exc:
                progress.discovery_failures += 1
                log.error("Invalid card brief in %s: %s", data["id"], exc)
    if limit is not None:
        jobs = jobs[:limit]
    progress.total = len(jobs)
    progress.display()
    iterator = iter(jobs)
    pool = ThreadPoolExecutor(max_workers=config.concurrency)
    pending = {}
    batch = []

    def flush_batch():
        if not batch:
            return
        try:
            vectors = embedder.encode([item[1] for item in batch])
            if len(vectors) != len(batch):
                raise ValueError("Model returned an incorrect batch length")
        except (RuntimeError, ValueError, OSError) as exc:
            for card_pk, _, _ in batch:
                store.fail(card_pk, exc)
                progress.failed += 1
            log.error("Embedding batch failed; retry with a smaller --batch-size if needed: %s", exc)
        else:
            for (card_pk, _, source), vector in zip(batch, vectors):
                store.save(card_pk, vector, source)
                progress.embedded += 1
        finally:
            batch.clear()


    def submit():
        try:
            set_id, brief = next(iterator)
        except StopIteration:
            return
        db.seed_card(config.language, set_id, brief)
        row = db.card(config.language, brief["id"])
        if row["set_id"] != set_id:
            raise ValueError(f"TCGdex ID {brief['id']} appeared in multiple sets")
        row["_embedding"] = store.record(row["id"])
        if refresh_images or regenerate:
            store.fail(row["id"], "Regeneration requested", status="pending")
        future = pool.submit(process_card, config, client, row, brief,
                             refresh_metadata=refresh_metadata, refresh_images=refresh_images, regenerate=regenerate)
        pending[future] = (brief["id"], row["id"])

    try:
        for _ in range(config.concurrency):
            submit()
        while pending:
            done, _ = wait(pending, timeout=1, return_when=FIRST_COMPLETED)
            for future in done:
                card_id, card_pk = pending.pop(future)
                values, outcome, metadata_counter = future.result()
                payload = values.pop("_image", None)
                error = values.pop("_embedding_error", None)
                if values:
                    db.update_card(config.language, card_id, values)
                if payload:
                    store.fail(card_pk, "Awaiting inference", status="pending")
                    batch.append((card_pk, *payload))
                    del payload
                    if len(batch) >= config.batch_size:
                        flush_batch()
                elif outcome in {"failed", "missing_images"}:
                    store.fail(card_pk, error or values.get("metadata_error") or "No reference image",
                               status="missing_image" if outcome == "missing_images" else "failed")
                progress.processed += 1
                setattr(progress, outcome, getattr(progress, outcome) + 1)
                if metadata_counter:
                    setattr(progress, metadata_counter, getattr(progress, metadata_counter) + 1)
                if outcome == "failed":
                    log.error("%s: %s", card_id, values.get("metadata_error") or error)
                if progress.processed % 25 == 0 or progress.processed == progress.total:
                    progress.display()
                submit()
    except BaseException:
        client.stop.set()
        raise
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
    flush_batch()
    store.ensure_index()
    progress.display()
    return asdict(progress)


def verify(config, db):
    result = {"checked": 0, "valid": 0, "invalid": 0}
    for row in db.conn.execute("SELECT * FROM cards WHERE download_status='ready' AND language=?",
                               (config.language,)):
        result["checked"] += 1
        try:
            path = (config.root / row["local_image_path"]).resolve()
            if not path.is_relative_to(config.root):
                raise ValueError("Image path escapes data directory")
            info = inspect_image(path.read_bytes())
            if any(info[k] != row[k] for k in info):
                raise ValueError("Image hash/dimensions/size differ from database")
            if row["metadata_status"] != "ready" or not json.loads(row["raw_json"]):
                raise ValueError("Image has no verified card metadata")
            result["valid"] += 1
        except (OSError, ValueError, TypeError, SyntaxError) as exc:
            result["invalid"] += 1
            log.error("%s: %s", row["card_id"], exc)
    return result

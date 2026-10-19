"""Offline integration tests: real image decoding/files/SQLite, simulated HTTP."""
from collections import Counter
from contextlib import closing, redirect_stdout
from dataclasses import replace
from io import BytesIO, StringIO
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from PIL import Image

from tcg_scanner.api import Client, FetchError, retry_after
from tcg_scanner.config import Config
from tcg_scanner.db import Database
from tcg_scanner.images import safe_id, inspect_image
from tcg_scanner.vectors import VectorStore
import numpy as np
import hashlib
from tcg_scanner.pipeline import eligible, process_card, run


class Response(BytesIO):
    status = 200

    def __init__(self, body, headers=None):
        super().__init__(body)
        self.headers = headers or {"Content-Length": str(len(body))}


class FakeHTTP:
    def __init__(self):
        self.routes = {}
        self.calls = Counter()
        self.lock = threading.Lock()

    def __call__(self, request, timeout):
        url = request.full_url
        with self.lock:
            self.calls[url] += 1
            value = self.routes.get(url)
            if isinstance(value, list):
                value = value.pop(0)
        if value is None:
            raise HTTPError(url, 404, "Not Found", {}, BytesIO())
        if isinstance(value, Exception):
            raise value
        if isinstance(value, dict):
            value = json.dumps(value).encode()
        return Response(value)


class FakeEmbedder:
    key = "test-model"
    dimension = 4
    spec = {"test_only": True}

    def __init__(self):
        self.batch_sizes = []

    def encode(self, bodies):
        self.batch_sizes.append(len(bodies))
        vectors = np.array([list(hashlib.sha256(b).digest()[:4]) for b in bodies], dtype="float32")
        return vectors / np.linalg.norm(vectors, axis=1, keepdims=True)


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config = Config(data_dir=self.tmp.name, requests_per_second=1000000, backoff=0, retries=1)
        self.db = Database(Path(self.tmp.name) / "metadata.sqlite3")
        self.addCleanup(lambda: self.db.close())
        self.embedder = FakeEmbedder()
        model_patch = patch("tcg_scanner.embeddings.DinoEmbedder", return_value=self.embedder)
        model_patch.start()
        self.addCleanup(model_patch.stop)
        self.http = FakeHTTP()
        self.patcher = patch("tcg_scanner.api.urlopen", self.http)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        buffer = BytesIO()
        Image.new("RGB", (600, 825), color="green").save(buffer, format="PNG")
        self.png = buffer.getvalue()
        self.cards = []
        self.add_card("001")
        self.add_card("002")
        self.publish_set()

    def add_card(self, number, image=True):
        card_id = "sv01-" + number
        base = "https://assets.tcgdex.net/en/sv/sv01/" + number
        brief = {"id": card_id, "localId": number, "name": "Same name"}
        if image:
            brief["image"] = base
        data = {**brief, "set": {"id": "sv01"}, "rarity": "Common",
                "variants": {"normal": True, "reverse": True, "holo": False},
                "variants_detailed": [{"type": "reverse", "customField": 123}]}
        self.cards.append(brief)
        self.http.routes[Client(self.config).card_url(card_id)] = data
        self.http.routes[base + "/high.png"] = self.png
        return data

    def publish_set(self):
        self.http.routes[Client(self.config).url("sets/sv01")] = {
            "id": "sv01", "name": "Scarlet & Violet", "serie": {"id": "sv"},
            "releaseDate": "2023-03-31", "cards": self.cards.copy()}

    def run_pipeline(self, **kwargs):
        with redirect_stdout(StringIO()):
            return run(self.config, self.db, Client(self.config), set_ids=["sv01"], **kwargs)

    def store(self):
        return VectorStore.selected(self.config, self.db)

    def legacy(self, card_id="sv01-001"):
        self.run_pipeline()
        row = self.db.card("en", card_id)
        relative = f"images/en/sv01/{card_id}.png"
        path = self.config.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.png)
        self.db.update_card("en", card_id, {**inspect_image(self.png), "local_image_path": relative,
                            "downloaded_url": row["image_url"] + "/high.png", "download_status": "ready"})
        return path

    def test_memory_only_download_metadata_and_resume(self):
        result = self.run_pipeline()
        self.assertEqual((result["embedded"], result["fetched"]), (2, 2))
        self.assertFalse((self.config.root / "images").exists())
        row = self.db.card("en", "sv01-001")
        self.assertEqual(row["collector_number"], "001")
        self.assertTrue(json.loads(row["variants_json"])["reverse"])
        self.assertEqual(json.loads(row["variants_detailed_json"])[0]["customField"], 123)
        calls = self.http.calls.copy()
        self.assertEqual(self.run_pipeline()["skipped"], 2)
        self.assertEqual(calls, self.http.calls)
        self.assertEqual(self.store().ensure_index().ntotal, 2)
        self.assertEqual(self.db.conn.execute("SELECT count(*) FROM embeddings").fetchone()[0], 2)

    def test_sync_new_card_and_metadata_changes(self):
        self.run_pipeline()
        self.add_card("003")
        self.publish_set()
        result = self.run_pipeline(sync=True)
        self.assertEqual((result["embedded"], result["skipped"]), (1, 2))
        base = "https://assets.tcgdex.net/updated"
        self.http.routes[Client(self.config).card_url("sv01-001")]["image"] = base
        self.http.routes[base + "/high.png"] = self.png
        self.db.update_card("en", "sv01-001", {"metadata_updated_at": 0})
        self.assertEqual(self.run_pipeline()["embedded"], 1)
        self.assertEqual(self.store().ensure_index().ntotal, 3)

    def test_local_image_reuse_and_no_automatic_delete(self):
        path = self.legacy()
        calls = self.http.calls.copy()
        result = self.run_pipeline(regenerate=True)
        self.assertEqual((result["reused"], result["fetched"]), (1, 1))
        url = self.db.card("en", "sv01-001")["downloaded_url"]
        self.assertEqual(calls[url], self.http.calls[url])
        self.assertEqual(path.read_bytes(), self.png)

    def test_corrupt_legacy_image_fetches_to_memory_without_replacing_file(self):
        path = self.legacy()
        path.write_bytes(b"bad")
        self.assertEqual(self.run_pipeline(regenerate=True)["fetched"], 2)
        self.assertEqual(path.read_bytes(), b"bad")
        self.assertEqual(self.store().cleanup_candidates(), [])

    def test_failed_image_keeps_metadata_and_retries(self):
        url = "https://assets.tcgdex.net/en/sv/sv01/001/high.png"
        self.http.routes[url] = None
        self.assertEqual(self.run_pipeline()["failed"], 1)
        row = self.db.card("en", "sv01-001")
        self.assertEqual(row["metadata_status"], "ready")
        self.assertEqual(self.store().record(row["id"])["status"], "failed")
        self.http.routes[url] = self.png
        result = self.run_pipeline()
        self.assertEqual((result["embedded"], result["metadata_fetched"]), (1, 0))

    def test_invalid_remote_image_retried(self):
        url = "https://assets.tcgdex.net/en/sv/sv01/001/high.png"
        self.http.routes[url] = [b"bad", self.png]
        self.assertEqual(self.run_pipeline()["embedded"], 2)
        self.assertEqual(self.http.calls[url], 2)
        self.assertFalse((self.config.root / "images").exists())

    def test_failed_metadata_and_missing_image_recover(self):
        url = Client(self.config).card_url("sv01-001")
        original = self.http.routes[url]
        self.http.routes[url] = {"id": "wrong"}
        data = self.add_card("003", image=False)
        self.publish_set()
        result = self.run_pipeline()
        self.assertEqual((result["failed"], result["missing_images"]), (1, 1))
        self.http.routes[url] = original
        data["image"] = "https://assets.tcgdex.net/en/sv/sv01/003"
        self.assertEqual(self.run_pipeline(refresh_metadata=True)["embedded"], 2)
        self.assertEqual(self.store().ensure_index().ntotal, 3)

    def test_inference_failure_retries_without_ready_rows(self):
        with patch.object(self.embedder, "encode", side_effect=RuntimeError("out of memory")):
            self.assertEqual(self.run_pipeline()["failed"], 2)
        self.assertEqual(self.store().ensure_index().ntotal, 0)
        self.assertEqual(self.run_pipeline()["embedded"], 2)

    def test_bounded_concurrency_and_batch_size(self):
        self.config = replace(self.config, concurrency=2, batch_size=3)
        for n in range(3, 12):
            self.add_card(f"{n:03}")
        self.publish_set()
        active = maximum = 0
        lock = threading.Lock()
        def tracked(*args, **kwargs):
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
            try:
                time.sleep(0.02)
                return process_card(*args, **kwargs)
            finally:
                with lock:
                    active -= 1
        with patch("tcg_scanner.pipeline.process_card", tracked):
            self.assertEqual(self.run_pipeline()["embedded"], 11)
        self.assertEqual(maximum, 2)
        self.assertEqual(self.embedder.batch_sizes, [3, 3, 3, 2])

    def test_interrupted_commit_resumes_completed_vectors(self):
        self.config = replace(self.config, concurrency=1, batch_size=1)
        original = VectorStore.save
        def interrupted(store, card_pk, vector, source):
            if card_pk == 2:
                raise KeyboardInterrupt
            original(store, card_pk, vector, source)
        with patch.object(VectorStore, "save", interrupted):
            with self.assertRaises(KeyboardInterrupt):
                self.run_pipeline()
        self.assertEqual(self.store().ensure_index().ntotal, 1)
        result = self.run_pipeline()
        self.assertEqual((result["embedded"], result["skipped"]), (1, 1))
        self.assertEqual(self.store().ensure_index().ntotal, 2)

    def test_interrupted_index_write_recovers_without_download(self):
        with patch("tcg_scanner.vectors.atomic_write", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.run_pipeline()
        calls = self.http.calls.copy()
        self.assertEqual(self.run_pipeline()["skipped"], 2)
        self.assertEqual(calls, self.http.calls)
        self.assertEqual(self.store().ensure_index().ntotal, 2)
        self.store().path.write_bytes(b"corrupt index")
        self.assertEqual(self.store().ensure_index().ntotal, 2)

    def test_corrupt_vector_is_regenerated(self):
        self.run_pipeline()
        with self.db.conn:
            self.db.conn.execute("UPDATE embeddings SET vector=? WHERE card_pk=1", (b"bad",))
        self.assertEqual(self.run_pipeline()["embedded"], 1)
        self.assertEqual(self.store().ensure_index().ntotal, 2)

    def test_model_change_preserves_old_vectors_and_reuses_local_images(self):
        path = self.legacy()
        old_key = self.embedder.key
        self.embedder.key = "other-test-model"
        self.assertEqual(self.run_pipeline()["reused"], 1)
        self.assertEqual(self.db.conn.execute("SELECT count(*) FROM embeddings").fetchone()[0], 4)
        self.assertEqual(self.db.conn.execute("SELECT count(*) FROM embeddings WHERE model_key=?",
                                             (old_key,)).fetchone()[0], 2)
        self.assertTrue(path.exists())

    def test_refresh_failure_excludes_old_vector(self):
        self.run_pipeline()
        self.http.routes["https://assets.tcgdex.net/en/sv/sv01/001/high.png"] = None
        self.assertEqual(self.run_pipeline(refresh_images=True)["failed"], 1)
        self.assertEqual(self.store().ensure_index().ntotal, 1)

    def test_cleanup_dry_run_confirmation_and_rebuild_without_images(self):
        from tcg_scanner.__main__ import main
        path = self.legacy()
        self.assertEqual(len(self.store().cleanup_candidates()), 1)
        self.db.close()
        with redirect_stdout(StringIO()), patch('tcg_scanner.__main__.Config.load', return_value=self.config):
            self.assertEqual(main(["cleanup-images", "--data-dir", self.tmp.name]), 0)
            self.assertTrue(path.exists())
            self.assertEqual(main(["cleanup-images", "--data-dir", self.tmp.name, "--confirm-delete"]), 0)
        self.assertFalse(path.exists())
        self.db = Database(self.config.root / "metadata.sqlite3")
        self.store().path.unlink()
        self.assertEqual(self.store().ensure_index().ntotal, 2)
        self.assertEqual(self.run_pipeline()["skipped"], 2)

    def test_cleanup_rejects_changed_hash_and_outside_paths(self):
        path = self.legacy()
        candidates = self.store().cleanup_candidates()
        path.write_bytes(b"changed")
        self.assertEqual(self.store().cleanup(candidates), 0)
        self.assertTrue(path.exists())
        path.write_bytes(self.png)
        self.db.update_card("en", "sv01-001", {"local_image_path": "../outside.png"})
        self.assertEqual(self.store().cleanup_candidates(), [])

    def test_search_returns_metadata_and_handles_partial_index(self):
        self.run_pipeline(limit=1)
        matches = self.store().search(self.embedder.encode([self.png])[0], 10)
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["card_id"], "sv01-001")
        self.assertEqual(matches[0]["set"], "Scarlet & Violet")
        self.assertEqual(matches[0]["collector_number"], "001")
        self.assertAlmostEqual(matches[0]["similarity"], 1, places=5)
        self.db.update_card("en", "sv01-001", {"image_url": None})
        self.assertEqual(self.store().search(self.embedder.encode([self.png])[0], 10), [])

    def test_catalog_failure_and_scope(self):
        self.http.routes[Client(self.config).url("sets/sv01")] = None
        self.assertEqual(self.run_pipeline()["discovery_failures"], 1)
        self.assertFalse(eligible({"id": "A1", "serie": {"id": "tcgp"}}, "2023-03-31"))

    def test_languages_have_distinct_ids_and_indexes(self):
        self.run_pipeline()
        self.db.save_set("fr", {"id": "sv01"})
        self.db.seed_card("fr", "sv01", {"id": "sv01-001"})
        fr = self.db.card("fr", "sv01-001")
        en = self.db.card("en", "sv01-001")
        self.assertNotEqual(fr["id"], en["id"])
        store = VectorStore.selected(replace(self.config, language="fr"), self.db)
        self.assertEqual(store.ensure_index().ntotal, 0)
        self.assertNotEqual(store.path, self.store().path)

    def test_existing_skips_catalog(self):
        self.run_pipeline()
        calls = self.http.calls.copy()
        self.assertEqual(self.run_pipeline(existing=True)["skipped"], 2)
        self.assertEqual(calls, self.http.calls)

    def test_conditional_catalog_requests(self):
        url = Client(self.config).url("sets/sv01")
        with patch("tcg_scanner.api.urlopen", return_value=Response(
                json.dumps(self.http.routes[url]).encode(), {"ETag": '"v1"'})):
            first = self.db.cached_json(Client(self.config), url, 0)
        def not_modified(request, timeout):
            self.assertEqual(request.get_header("If-none-match"), '"v1"')
            raise HTTPError(url, 304, "Not modified", {}, BytesIO())
        with patch("tcg_scanner.api.urlopen", not_modified):
            self.assertEqual(self.db.cached_json(Client(self.config), url, 0), first)


class MigrationTests(unittest.TestCase):
    def test_v1_migration_backup_metadata_and_lock(self):
        import sqlite3
        from tcg_scanner.db import SCHEMA
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "metadata.sqlite3"
            with closing(sqlite3.connect(path)) as c, c:
                c.executescript(SCHEMA + "PRAGMA user_version=1;")
                c.execute("INSERT INTO sets VALUES ('en','sv01','Set',NULL,NULL,'{}')")
                c.execute("""INSERT INTO cards(id,language,card_id,set_id,brief_json,local_image_path)
                    VALUES (42,'en','sv01-001','sv01','{}','images/en/sv01/a.png')""")
            db = Database(path)
            try:
                self.assertEqual(db.conn.execute("PRAGMA user_version").fetchone()[0], 2)
                self.assertEqual(db.card("en", "sv01-001")["id"], 42)
                self.assertEqual(db.card("en", "sv01-001")["local_image_path"], "images/en/sv01/a.png")
                with self.assertRaises(ValueError):
                    Database(path)
                with closing(sqlite3.connect(str(path) + ".v1.bak")) as backup:
                    self.assertEqual(backup.execute("PRAGMA user_version").fetchone()[0], 1)
                    self.assertEqual(backup.execute("SELECT id FROM cards").fetchone()[0], 42)
            finally:
                db.close()
            Database(path).close()

    def test_whole_card_padding_retains_all_edges_through_official_crop(self):
        from transformers import BitImageProcessor
        from tcg_scanner.embeddings import whole_card
        processor = BitImageProcessor(size={"shortest_edge": 256}, crop_size={"height": 224, "width": 224})
        for size in [(600, 825), (825, 600)]:
            with Image.new("RGB", size, "white") as im:
                canvas = whole_card(im, processor)
                pixels = processor(images=canvas, return_tensors="np", do_normalize=False)["pixel_values"][0]
                canvas.close()
                mask = pixels.min(axis=0) > 0.95
                yy, xx = np.where(mask)
                self.assertGreater(xx.min(), 0)
                self.assertLess(xx.max(), 223)
                self.assertGreater(yy.min(), 0)
                self.assertLess(yy.max(), 223)


class RequestTests(unittest.TestCase):
    def test_transient_errors_and_retry_after(self):
        url = "https://api.tcgdex.net/test"
        client = Client(Config(retries=3, backoff=1, requests_per_second=1000000))
        failures = [HTTPError(url, 429, "Rate limit", {"Retry-After": "5"}, BytesIO()),
                    HTTPError(url, 503, "Unavailable", {}, BytesIO()), URLError("offline"),
                    Response(b'{"ok": true}')]
        with patch("tcg_scanner.api.urlopen", side_effect=failures), \
                patch.object(client, "pause") as pause, patch("tcg_scanner.api.random.uniform", return_value=0):
            data, _, _ = client.json(url)
        self.assertTrue(data["ok"])
        self.assertEqual([c.args[0] for c in pause.call_args_list], [5, 2, 4])

    def test_nonretryable_and_exhausted_errors(self):
        for status, attempts in [(404, 1), (500, 3)]:
            client = Client(Config(retries=2, backoff=0, requests_per_second=1000000))
            with patch("tcg_scanner.api.urlopen", side_effect=HTTPError("https://x", status, "error", {}, BytesIO())) as mock:
                with self.assertRaises(FetchError):
                    client.get("https://x")
            self.assertEqual(mock.call_count, attempts)

    def test_retry_after_date_and_invalid_value(self):
        self.assertEqual(retry_after("Thu, 01 Jan 1970 00:00:00 GMT"), 0)
        self.assertEqual(retry_after("invalid"), 0)
        self.assertEqual(retry_after("5"), 5)

    def test_invalid_json_and_truncated_response_are_retried(self):
        client = Client(Config(backoff=0, requests_per_second=1000000))
        with patch("tcg_scanner.api.urlopen", side_effect=[Response(b"bad"),
                Response(b"{}", {"Content-Length": "20"}), Response(b"{}")]) as mock:
            self.assertEqual(client.json("https://x")[0], {})
        self.assertEqual(mock.call_count, 3)

    def test_safe_paths_and_scope(self):
        for value in ("../bad", "a/b", "..", "", "/absolute"):
            with self.assertRaises(ValueError):
                safe_id(value)
        since = "2023-03-31"
        self.assertTrue(eligible({"id": "svp", "serie": {"id": "sv"}}, since))
        self.assertTrue(eligible({"id": "future", "releaseDate": "2026-01-01"}, since))
        self.assertFalse(eligible({"id": "old", "releaseDate": "2022-01-01"}, since))
        self.assertFalse(eligible({"id": "A1", "serie": {"id": "tcgp"}, "releaseDate": "2024-01-01"}, since))

    def test_config_data_path_is_relative_to_config_but_overrides_use_cwd(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'desktop'
            folder.mkdir()
            path = folder / 'config.toml'
            path.write_text('data_dir = "../data"\n')
            with patch.dict("os.environ", {}, clear=True):
                self.assertEqual(Config.load(path, {}).root, Path(tmp).resolve() / 'data')
                self.assertEqual(Config.load(path, {'data_dir':'custom'}).root, Path('custom').resolve())
            with patch.dict("os.environ", {'TCG_DATA_DIR':'environment'}, clear=True):
                self.assertEqual(Config.load(path, {}).root, Path('environment').resolve())
                self.assertEqual(Config.load(path, {'data_dir':'custom'}).root, Path('custom').resolve())

    def test_configuration_precedence_and_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text('concurrency = 2\nlanguage = "en"\n')
            with patch.dict("os.environ", {"TCG_CONCURRENCY": "3"}):
                self.assertEqual(Config.load(path, {}).concurrency, 3)
                self.assertEqual(Config.load(path, {"concurrency": 4}).concurrency, 4)
        for kwargs in ({"concurrency": 0}, {"requests_per_second": 0}, {"timeout": float("nan")},
                       {"since": "invalid"}, {"language": "../en"}):
            with self.assertRaises(ValueError):
                Config(**kwargs)


if __name__ == "__main__":
    unittest.main()

"""SQLite is authoritative; persisted FAISS indexes are recoverable exact-search caches."""
import json
import logging
import time

import faiss
import numpy as np

from .db import dumps
from .images import atomic_write, inspect_image

log = logging.getLogger(__name__)


def normalized(vector, dimension):
    v = np.asarray(vector, dtype="float32")
    if v.shape != (dimension,) or not np.isfinite(v).all():
        raise ValueError("Invalid embedding dimensions or values")
    norm = np.linalg.norm(v)
    if not np.isclose(norm, 1, atol=1e-4):
        raise ValueError("Embedding is not L2 normalized")
    return v


class VectorStore:
    def __init__(self, config, db, model):
        self.config, self.db = config, db
        self.key, self.dimension = model["model_key"], model["dimension"]
        self.path = config.root / "indexes" / (self.key + "-" + config.language + ".faiss")

    @classmethod
    def register(cls, config, db, embedder):
        with db.conn:
            db.conn.execute("""INSERT INTO embedding_models VALUES (?,?,?,?,?,?)
                ON CONFLICT(model_key) DO UPDATE SET requested_revision=excluded.requested_revision""",
                (embedder.key, config.model_name, config.model_revision,
                 dumps(embedder.spec), embedder.dimension, time.time()))
        return cls(config, db, {"model_key": embedder.key, "dimension": embedder.dimension})

    @classmethod
    def selected(cls, config, db):
        row = db.conn.execute("""SELECT * FROM embedding_models
            WHERE model_name=? AND requested_revision=? ORDER BY created_at DESC LIMIT 1""",
            (config.model_name, config.model_revision)).fetchone()
        if row is None:
            raise ValueError("No stored embeddings for this model; run embed first")
        return cls(config, db, row)

    def record(self, card_pk):
        row = self.db.conn.execute("SELECT * FROM embeddings WHERE card_pk=? AND model_key=?",
                                   (card_pk, self.key)).fetchone()
        if row is None:
            return None
        row = dict(row)
        if row["status"] == "ready":
            try:
                normalized(np.frombuffer(row["vector"], dtype="<f4"), self.dimension)
                if row["dimension"] != self.dimension:
                    raise ValueError("Stored dimension mismatch")
            except (ValueError, TypeError):
                row["status"] = "failed"
        return row

    def save(self, card_pk, vector, source):
        v = normalized(vector, self.dimension)
        with self.db.conn:
            self.db.conn.execute("""INSERT INTO embeddings
                (card_pk,model_key,status,vector,dimension,source_url,source_sha256,source_kind,
                 attempted_at,processed_at,error) VALUES (?,?,'ready',?,?,?,?,?,?,?,NULL)
                ON CONFLICT(card_pk,model_key) DO UPDATE SET status='ready', vector=excluded.vector,
                dimension=excluded.dimension, source_url=excluded.source_url,
                source_sha256=excluded.source_sha256, source_kind=excluded.source_kind,
                attempted_at=excluded.attempted_at, processed_at=excluded.processed_at,error=NULL""",
                (card_pk, self.key, v.astype("<f4").tobytes(), self.dimension,
                 source["url"], source["sha256"], source["kind"], time.time(), time.time()))

    def fail(self, card_pk, error, status="failed"):
        with self.db.conn:
            self.db.conn.execute("""INSERT INTO embeddings(card_pk,model_key,status,attempted_at,error)
                VALUES (?,?,?,?,?) ON CONFLICT(card_pk,model_key) DO UPDATE SET
                status=excluded.status,attempted_at=excluded.attempted_at,error=excluded.error""",
                (card_pk, self.key, status, time.time(), str(error)))

    def rows(self):
        # Old vectors are retained for recovery, but stale URLs/metadata cannot be searched.
        return self.db.conn.execute("""SELECT e.*, c.card_id,c.name,c.collector_number,
            c.image_url,c.local_image_path,c.sha256,c.raw_json,c.set_id,s.name AS set_name
            FROM embeddings e JOIN cards c ON c.id=e.card_pk
            JOIN sets s ON s.language=c.language AND s.set_id=c.set_id
            WHERE e.model_key=? AND e.status='ready' AND c.language=?
            AND c.metadata_status='ready' AND c.image_url IS NOT NULL
            AND e.source_url=rtrim(c.image_url,'/') || '/high.' || ?
            ORDER BY e.embedding_id""", (self.key, self.config.language, self.config.image_format))

    def ensure_index(self, force=False):
        """Validate IDs AND vectors against a DB snapshot; repair interrupted/corrupt writes.

        The directory lock is held by Database for the entire command. Readers never
        trust a stale index, even after a process died between SQLite and FAISS writes.
        No model or reference images are needed to rebuild.
        """
        expected = faiss.IndexIDMap2(faiss.IndexFlatIP(self.dimension))
        ids, vectors = [], []
        for row in self.rows():
            try:
                v = normalized(np.frombuffer(row["vector"], dtype="<f4"), self.dimension)
                if row["dimension"] != self.dimension:
                    raise ValueError("Stored dimension mismatch")
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Corrupt embedding for {row['card_id']}; rerun embed: {exc}") from exc
            ids.append(row["embedding_id"])
            vectors.append(v)
            if len(ids) == 1024:
                expected.add_with_ids(np.array(vectors), np.array(ids, dtype="int64"))
                ids.clear(); vectors.clear()
        if ids:
            expected.add_with_ids(np.array(vectors), np.array(ids, dtype="int64"))
        payload = faiss.serialize_index(expected).tobytes()
        # Deterministic exact index serialization covers both IDs and vector content.
        if force or not self.path.exists() or self.path.read_bytes() != payload:
            atomic_write(self.path, payload)
        # Read back the actual persisted file before identification or cleanup.
        index = faiss.read_index(str(self.path))
        if faiss.serialize_index(index).tobytes() != payload:
            raise ValueError("Persisted FAISS index verification failed")
        return index

    def search(self, vector, top_k, *, index=None, metadata=None):
        # A caller may reuse an index already verified under this Database's lock.
        if index is None:
            index = self.ensure_index()
        if not index.ntotal:
            return []
        scores, ids = index.search(normalized(vector, self.dimension)[None, :],
                                   min(top_k, index.ntotal))
        rows = metadata if metadata is not None else self.search_metadata()
        return [{"card_id": rows[int(i)]["card_id"], "name": rows[int(i)]["name"],
                 "set": rows[int(i)]["set_name"] or rows[int(i)]["set_id"],
                 "collector_number": rows[int(i)]["collector_number"],
                 "similarity": float(score), "reference_image_url": rows[int(i)]["source_url"]}
                for i, score in zip(ids[0], scores[0]) if int(i) in rows]

    def search_metadata(self):
        # Retain just display fields, never all vector blobs/raw API responses.
        keys = ("card_id", "name", "set_name", "set_id", "collector_number", "source_url")
        return {row["embedding_id"]: {key: row[key] for key in keys} for row in self.rows()}

    def cleanup_candidates(self):
        index = self.ensure_index()
        candidates = []
        for row in self.rows():
            relative = row["local_image_path"]
            if not relative:
                continue
            path = self.config.root / relative
            resolved = path.resolve()
            if (resolved != path.absolute() or not resolved.is_relative_to(self.config.root / "images")
                    or not path.is_file()):
                continue
            try:
                info = inspect_image(path.read_bytes())
                if info["sha256"] != row["source_sha256"] or info["sha256"] != row["sha256"]:
                    continue
                if not json.loads(row["raw_json"] or "null"):
                    continue
                vector = np.frombuffer(row["vector"], dtype="<f4")
                if not np.array_equal(index.reconstruct(row["embedding_id"]), vector):
                    continue
            except (OSError, ValueError, RuntimeError, SyntaxError):
                continue
            candidates.append({"card_pk": row["card_pk"], "path": str(path),
                               "sha256": info["sha256"]})
        return candidates

    def cleanup(self, candidates):
        # Revalidate the persisted index, metadata, path and file hash after confirmation.
        allowed = {(r["card_pk"], r["path"], r["sha256"]) for r in self.cleanup_candidates()}
        removed = 0
        for candidate in candidates:
            key = (candidate["card_pk"], candidate["path"], candidate["sha256"])
            if key not in allowed:
                continue
            from pathlib import Path
            Path(candidate["path"]).unlink()
            with self.db.conn:
                self.db.conn.execute("""UPDATE cards SET local_image_path=NULL,
                    download_status='cleaned' WHERE id=?""", (candidate["card_pk"],))
            removed += 1
        return removed


class SearchIndex:
    """Resident exact IP search; persisted CPU index remains the recovery format.

    GPU Flat returns row offsets. Map them explicitly to the durable SQLite IDs
    so gaps/deletions never change the card associated with a result.
    """
    def __init__(self, cpu_index, device="cpu"):
        if device not in {"auto", "cpu", "cuda"}:
            raise ValueError("FAISS device must be auto, cpu, or cuda")
        self.index = cpu_index
        self.ntotal = cpu_index.ntotal
        self.device = "cpu"
        self.resources = None
        self.ids = None
        if device == "cpu":
            return
        try:
            if not hasattr(faiss, "StandardGpuResources") or faiss.get_num_gpus() < 1:
                raise RuntimeError("GPU-enabled FAISS and an accessible NVIDIA GPU are required")
            self.resources = faiss.StandardGpuResources()
            self.resources.setTempMemory(64 * 1024 * 1024)
            # Keep float32 exact inner products; no quantization or float16 storage.
            self.index = faiss.index_cpu_to_gpu(
                self.resources, 0, faiss.downcast_index(cpu_index.index))
            self.ids = faiss.vector_to_array(cpu_index.id_map).copy()
            self.device = "cuda"
        except RuntimeError as exc:
            self.index, self.resources, self.ids = cpu_index, None, None
            if device == "cuda":
                raise RuntimeError(f"FAISS CUDA unavailable: {exc}. Use --faiss-device cpu "
                                   "or install requirements-gpu.txt (see README).") from exc
            log.warning("FAISS CUDA unavailable; using CPU: %s", exc)

    def search(self, vectors, top_k):
        scores, ids = self.index.search(vectors, top_k)
        if self.ids is not None:
            valid = ids >= 0
            mapped = np.full_like(ids, -1)
            mapped[valid] = self.ids[ids[valid]]
            ids = mapped
        return scores, ids

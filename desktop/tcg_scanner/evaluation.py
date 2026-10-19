"""Shared identification and single-photo evaluation; query images are never stored."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from time import perf_counter
from uuid import uuid4

NOTE = "Similarity is not a probability. Collector-number OCR can support printing identity, never foil or finish. Unverified results retain embedding order."


class IdentificationSession:
    """Load once and reuse under the caller's Database lock until the session ends."""
    def __init__(self, config, db):
        self.config, self.db = config, db
        self.embedder = None
        self.cropper = None
        self.verifier = None
        self.query_count = 0
        self.startup_seconds = 0.0

    def load(self):
        if self.embedder is not None:
            return {"reference_setup": 0.0, "model_load": 0.0, "detector_load": 0.0}
        started = perf_counter()
        import faiss
        from .vectors import SearchIndex, VectorStore
        faiss.omp_set_num_threads(self.config.cpu_threads)
        store = VectorStore.selected(self.config, self.db)
        cpu_index = store.ensure_index()
        if not cpu_index.ntotal:
            raise ValueError("No valid reference embeddings; run embed or sync first")
        reference_done = perf_counter()
        from .embeddings import DinoEmbedder
        embedder = DinoEmbedder(self.config)
        model_done = perf_counter()
        if store.key != embedder.key:
            store = VectorStore(self.config, self.db,
                                {"model_key": embedder.key, "dimension": embedder.dimension})
            cpu_index = store.ensure_index()
            if not cpu_index.ntotal:
                raise ValueError("Model/preprocessing changed; run embed to generate compatible references")
        index = SearchIndex(cpu_index, self.config.faiss_device)
        metadata = store.search_metadata()
        self.store, self.cpu_index, self.index, self.metadata = store, cpu_index, index, metadata
        detector_started = perf_counter()
        if self.config.crop == "yolo":
            from .detection import CardCropper
            self.cropper = CardCropper(self.config, embedder.device)
        detector_loaded = perf_counter() - detector_started
        if self.config.ocr == "paddle":
            from .ocr import PrintingVerifier
            self.verifier = PrintingVerifier(self.config, self.db, metadata)
        self.embedder = embedder  # Mark loaded only after all initialization succeeds.
        self.startup_seconds = perf_counter() - started
        return {"reference_setup": self.startup_seconds - (model_done - reference_done) - detector_loaded,
                "model_load": model_done - reference_done, "detector_load": detector_loaded}

    def identify(self, image_path, top_k=5):
        if not 1 <= top_k <= 100:
            raise ValueError("top_k must be between 1 and 100")
        started = perf_counter()
        from .images import inspect_image
        image_path = Path(image_path).expanduser().resolve()
        body = image_path.read_bytes()
        inspect_image(body)  # Invalid photos do not load the model or end a session.
        image_hash = hashlib.sha256(body).hexdigest()
        input_done = perf_counter()
        setup = self.load()
        ready = perf_counter()
        original_body = body
        body, crop_info = self.prepare_image(body)
        detected = perf_counter()
        vector = self.embedder.encode([body])[0]  # Includes GPU -> CPU synchronization.
        del body
        embedded = perf_counter()
        candidate_count = max(top_k, 10) if self.verifier is not None else top_k
        visual_matches = self.store.search(vector, candidate_count, index=self.index, metadata=self.metadata)
        searched = perf_counter()
        ocr = (self.verifier.check(original_body, crop_info, visual_matches) if self.verifier is not None
               else {"status": "disabled", "selected_card_id": None})
        del original_body
        selected = ocr.get("selected_card_id") if ocr["status"] == "verified" else None
        matches = list(visual_matches)
        if selected:
            matches.sort(key=lambda match: match["card_id"] != selected)
        matches = [dict(match) for match in matches[:top_k]]
        if self.verifier is not None:
            ranks = {match["card_id"]: rank for rank, match in enumerate(visual_matches, 1)}
            for match in matches:
                match["embedding_rank"] = ranks[match["card_id"]]
        completed = perf_counter()
        self.query_count += 1
        return {
            "image_path": str(image_path), "image_sha256": image_hash,
            "language": self.config.language, "model_name": self.config.model_name,
            "model_key": self.embedder.key, "device": self.embedder.device,
            "faiss_device": self.index.device, "crop": crop_info, "matches": matches,
            "reference_count": self.index.ntotal, "session_query_number": self.query_count,
            "visual_matches": visual_matches, "ocr": ocr,
            "selection_method": "ocr_verified" if selected else "embedding",
            "printing_verified": bool(selected),
            "timing_seconds": {
                "read_and_validate": input_done - started, **setup,
                "detection_and_crop": detected - ready,
                "embedding": embedded - detected,
                "search_and_metadata": searched - embedded,
                "ocr": completed - searched if self.verifier is not None else 0.0,
                "query": completed - ready if self.verifier is not None else searched - ready,
                "total": completed - started,
            },
            "note": NOTE,
        }

    def close(self):
        if self.verifier is not None:
            self.verifier.close()

    def prepare_image(self, body):
        """Query-only preparation; reference preprocessing and fingerprints stay unchanged."""
        if self.cropper is None:
            return body, {"mode": "none"}
        return self.cropper.crop(body)


def identify_image(config, db, image_path, top_k=5):
    """One-shot wrapper using the same pipeline as persistent scanning."""
    session = IdentificationSession(config, db)
    try:
        return session.identify(image_path, top_k)
    finally:
        session.close()


def evaluate_image(config, db, image_path, *, expected_card=None, top_k=5, session=None):
    if not 5 <= top_k <= 100:
        raise ValueError("Evaluation requires --top-k between 5 and 100")
    if session is not None and (session.config != config or session.db is not db):
        raise ValueError("Session must use the same configuration and database")
    result = (session.identify(image_path, top_k) if session is not None
              else identify_image(config, db, image_path, top_k))
    matches = result["matches"]
    for rank, match in enumerate(matches, 1):
        match["rank"] = rank
    expected = db.card(config.language, expected_card) if expected_card else None
    expected_info = None
    in_index = None
    rank = None
    if expected_card:
        from .vectors import VectorStore
        model = db.conn.execute("SELECT * FROM embedding_models WHERE model_key=?",
                                (result["model_key"],)).fetchone()
        store = VectorStore(config, db, model)
        in_index = any(row["card_id"] == expected_card for row in store.rows())
        rank = next((match["rank"] for match in matches if match["card_id"] == expected_card), None)
        if expected:
            expected_info = {key: expected[key] for key in ("card_id", "name", "set_id", "collector_number")}
    visual = result["visual_matches"]
    result.update({
        "schema_version": 2, "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "requested_top_k": top_k,
        "top_two_score_gap": visual[0]["similarity"] - visual[1]["similarity"] if len(visual) >= 2 else None,
        "evaluation": {
            "expected_card_id": expected_card, "expected_card": expected_info,
            "expected_in_index": in_index,
            "rank_in_results": rank,
            "top1_correct": (rank == 1) if expected_card else None,
            "top5_correct": (rank is not None and rank <= 5) if expected_card else None,
        },
    })
    return result


def report_path(config, output=None):
    if output is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        output = config.root / "evaluations" / (stamp + "-" + uuid4().hex[:8] + ".json")
    path = Path(output).expanduser().resolve()
    if path.exists():
        raise ValueError(f"Report already exists; choose another --output path: {path}")
    return path


def save_report(result, path):
    """Exclusive creation prevents overwriting input photos, data, or prior evaluations."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    result["report_path"] = str(path)
    payload = json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    with path.open("x", encoding="utf-8") as output:
        output.write(payload)
        output.flush()
        os.fsync(output.fileno())

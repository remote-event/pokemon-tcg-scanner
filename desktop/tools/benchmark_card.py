#!/usr/bin/env python3
"""Compare resident CPU/GPU FAISS using one shared DINOv2 model and local photo."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from statistics import median
from time import perf_counter

from tcg_scanner.config import Config, default_config_path
from tcg_scanner.db import Database
from tcg_scanner.evaluation import IdentificationSession, report_path, save_report
from tcg_scanner.vectors import SearchIndex


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("--runs", type=int, default=10)
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--data-dir")
    parser.add_argument("--crop", choices=("none", "yolo"))
    args = parser.parse_args()
    if not 2 <= args.runs <= 100:
        parser.error("--runs must be between 2 and 100")
    config = Config.load(args.config or default_config_path(),
                         {"data_dir": args.data_dir, "faiss_device": "cuda", "crop": args.crop, "ocr": "none"})
    db = Database(config.root / "metadata.sqlite3")
    try:
        import faiss
        import numpy as np
        session = IdentificationSession(config, db)
        first = session.identify(args.image)
        gpu = session.index
        cpu = SearchIndex(session.cpu_index, "cpu")
        # Release encoded image bytes after this call. The microbenchmark reuses only the vector.
        body, _ = session.prepare_image(args.image.read_bytes())
        query = session.embedder.encode([body])[0][None, :]
        del body
        backends = {"cpu": cpu, "cuda": gpu}
        report = {"image_path": first["image_path"], "image_sha256": first["image_sha256"],
                  "evaluated_at": datetime.now(timezone.utc).isoformat(),
                  "model_key": session.embedder.key, "device": session.embedder.device,
                  "faiss_version": faiss.__version__, "cpu_threads": config.cpu_threads,
                  "reference_count": session.index.ntotal, "runs": args.runs,
                  "startup_seconds": session.startup_seconds, "first_query": first,
                  "backends": {}, "note": "Repeated measurements of one photo, not recognition accuracy."}
        scores_by_backend, ids_by_backend = {}, {}
        for name, index in backends.items():
            session.index = index
            session.identify(args.image)  # Warm up each backend before measured photo runs.
            timings = [session.identify(args.image)["timing_seconds"] for _ in range(args.runs)]
            for _ in range(5):
                index.search(query, 5)
            search_times = []
            for _ in range(100):
                started = perf_counter()
                scores, ids = index.search(query, 5)  # NumPy outputs synchronize GPU work.
                search_times.append(perf_counter() - started)
            scores_by_backend[name], ids_by_backend[name] = scores, ids
            report["backends"][name] = {
                "median_seconds": {key: median(row[key] for row in timings) for key in timings[0]},
                "raw_photo_timings": timings,
                "pure_search_median_seconds": median(search_times),
                "pure_search_runs": len(search_times),
                "matches": session.store.search(query[0], 5, index=index, metadata=session.metadata),
            }
        report["same_top5_ids"] = bool(np.array_equal(ids_by_backend["cpu"], ids_by_backend["cuda"]))
        report["maximum_score_difference"] = float(np.max(np.abs(
            scores_by_backend["cpu"] - scores_by_backend["cuda"])))
        report["scores_agree_at_1e_5"] = bool(np.allclose(
            scores_by_backend["cpu"], scores_by_backend["cuda"], rtol=0, atol=1e-5))
        output = report_path(config)
        output = output.with_name("benchmark-" + output.name)
        save_report(report, output)
        print(json.dumps({"report_path": str(output), "startup_seconds": session.startup_seconds,
                          "same_top5_ids": report["same_top5_ids"],
                          "maximum_score_difference": report["maximum_score_difference"],
                          "backends": {key: {k: v for k, v in value.items() if k != "raw_photo_timings"}
                                       for key, value in report["backends"].items()}}, indent=2))
        return int(not report["same_top5_ids"] or not report["scores_agree_at_1e_5"])
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

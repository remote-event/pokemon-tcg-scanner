import argparse
import json
import logging
from pathlib import Path
import sqlite3

from .api import Client, FetchError
from .config import Config, default_config_path
from .db import Database


def main(argv=None):
    parser = argparse.ArgumentParser(description="Embed, synchronize and identify TCGdex reference cards")
    parser.add_argument("command", choices=("embed", "download", "sync", "status", "verify", "verify-images",
                                               "rebuild-index", "identify", "evaluate", "scan", "cleanup-images"))
    parser.add_argument("--config", type=Path, help="TOML configuration (default: local config, then shared config)")
    parser.add_argument("--data-dir")
    parser.add_argument("--language")
    parser.add_argument("--concurrency", type=int)
    parser.add_argument("--requests-per-second", type=float)
    parser.add_argument("--set", dest="set_ids", action="append", default=[], help="Explicit set ID; repeatable")
    parser.add_argument("--limit", type=int, help="Process at most N cards, for a smoke test")
    parser.add_argument("--refresh-metadata", action="store_true")
    parser.add_argument("--refresh-images", action="store_true")
    parser.add_argument("image", nargs="?", type=Path, help="Local card image for identify/evaluate, or first image for scan")
    parser.add_argument("--batch-size", type=int)
    parser.add_argument("--cpu-threads", type=int)
    parser.add_argument("--model-name")
    parser.add_argument("--model-revision")
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"))
    parser.add_argument("--faiss-device", choices=("auto", "cpu", "cuda"))
    parser.add_argument("--crop", choices=("none", "yolo"), help="Query cropping; none for prepared scans")
    parser.add_argument("--ocr", choices=("none", "paddle"), help="Check printing identifiers with PaddleOCR")
    parser.add_argument("--ocr-device", choices=("cpu", "gpu:0"))
    parser.add_argument("--ocr-python", help="Python executable in the isolated PaddleOCR environment")
    parser.add_argument("--detector-confidence", type=float, help="YOLO detection threshold (default: 0.5)")
    parser.add_argument("--existing", action="store_true", help="Use cards already in SQLite; skip catalog discovery")
    parser.add_argument("--regenerate", action="store_true", help="Regenerate vectors, reusing local images")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--confirm-delete", action="store_true", help="Explicitly confirm deletion of verified legacy images")
    parser.add_argument("--expected-card", help="Known TCGdex ID for grading evaluate, e.g. sv01-001")
    parser.add_argument("--output", type=Path, help="Evaluation JSON report path (default: data/evaluations/<unique>.json)")
    args = parser.parse_args(argv)
    if args.command in {"identify", "evaluate"} and args.image is None:
        parser.error(f"{args.command} requires a local image path")
    if args.command not in {"scan", "evaluate", "identify"} and (args.crop is not None or args.detector_confidence is not None):
        parser.error("--crop and --detector-confidence apply only to scan/evaluate/identify")
    if args.command != "evaluate" and (args.expected_card is not None or args.output is not None):
        parser.error("--expected-card and --output apply only to evaluate")
    if args.command in {"evaluate", "scan"} and args.top_k < 5:
        parser.error("evaluate/scan requires --top-k of at least 5")
    if args.expected_card is not None and not args.expected_card.strip():
        parser.error("--expected-card cannot be empty")
    if not 1 <= args.top_k <= 100:
        parser.error("--top-k must be between 1 and 100")
    if args.confirm_delete and args.command != "cleanup-images":
        parser.error("--confirm-delete applies only to cleanup-images")
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    db = None
    try:
        path = args.config or default_config_path()
        config = Config.load(path, vars(args))
        db = Database(config.root / "metadata.sqlite3")
        if args.command == "status":
            result = {"cards": db.status(), "embeddings": [dict(row) for row in db.conn.execute(
                """SELECT c.language,e.model_key,e.status,COUNT(*) AS cards FROM embeddings e
                   JOIN cards c ON c.id=e.card_pk GROUP BY c.language,e.model_key,e.status""")]}
            code = 0
        elif args.command == "verify-images":
            from .pipeline import verify
            result = verify(config, db)
            code = int(result["invalid"] > 0 or result["checked"] == 0)
        elif args.command in {"verify", "rebuild-index", "cleanup-images"}:
            from .vectors import VectorStore
            store = VectorStore.selected(config, db)
            if args.command == "cleanup-images":
                candidates = store.cleanup_candidates()
                result = {"eligible": len(candidates), "files": [r["path"] for r in candidates],
                          "removed": store.cleanup(candidates) if args.confirm_delete else 0,
                          "dry_run": not args.confirm_delete}
                if not args.confirm_delete:
                    result["next_step"] = "Review files, then rerun with --confirm-delete to explicitly authorize removal"
                code = 0
            else:
                index = store.ensure_index(force=args.command == "rebuild-index")
                result = {"valid": index.ntotal, "index": str(store.path), "model_key": store.key}
                code = int(index.ntotal == 0)
        elif args.command == "scan":
            from .scanning import scan_loop
            return scan_loop(config, db, initial_image=args.image, top_k=args.top_k)
        elif args.command in {"identify", "evaluate"}:
            from .evaluation import evaluate_image, identify_image, report_path, save_report
            if args.command == "evaluate":
                output = report_path(config, args.output)
                result = evaluate_image(config, db, args.image,
                                        expected_card=args.expected_card, top_k=args.top_k)
                save_report(result, output)
            else:
                result = identify_image(config, db, args.image, args.top_k)
            # A wrong prediction is an evaluation result, not a failed command.
            code = int(not result["matches"])
        else:
            from .pipeline import run
            if args.command == "download":
                logging.warning("download is now an alias for embed; new reference images are kept in memory only")
            result = run(config, db, Client(config), sync=args.command == "sync",
                         set_ids=args.set_ids, limit=args.limit,
                         refresh_metadata=args.refresh_metadata, refresh_images=args.refresh_images,
                         regenerate=args.regenerate, existing=args.existing)
            code = int(bool(result["failed"] or result["discovery_failures"] or not result["total"]))
        print(json.dumps(result, indent=2))
        return code
    except KeyboardInterrupt:
        print("Interrupted. Completed cards are saved; repeat the command to resume.")
        return 130
    except ImportError as exc:
        logging.error("Missing dependency: %s. See README setup for CPU/GPU requirements and requirements/requirements-yolo.txt", exc)
        return 1
    except (FetchError, ValueError, OSError, RuntimeError, sqlite3.Error) as exc:
        logging.error("%s", exc)
        return 1
    finally:
        if db is not None:
            db.close()


if __name__ == "__main__":
    raise SystemExit(main())

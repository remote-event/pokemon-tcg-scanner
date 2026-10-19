"""Sequential local scan prompt; one model/index load and one report per photo."""
import json
import sys

from .evaluation import IdentificationSession, evaluate_image, report_path, save_report


def scan_loop(config, db, *, initial_image=None, top_k=5):
    print("Loading model and reference index...", file=sys.stderr, flush=True)
    session = IdentificationSession(config, db)
    session.load()
    print(f"Ready: {session.index.ntotal} references, DINOv2={session.embedder.device}, "
          f"FAISS={session.index.device}, crop={config.crop}, OCR={config.ocr}; startup {session.startup_seconds:.2f}s.\n"
          "Enter an image path, or :quit to exit. Stop this session before syncing references.",
          file=sys.stderr, flush=True)
    pending = str(initial_image) if initial_image is not None else None
    try:
        while True:
            if pending is not None:
                path, pending = pending, None
            else:
                if sys.stdin.isatty():
                    print("Image path> ", end="", file=sys.stderr, flush=True)
                line = sys.stdin.readline()
                if not line:
                    break
                path = line.strip()
            if not path:
                continue
            if path == ":quit":
                break
            # Accept a pasted quoted path with spaces, without shell interpretation.
            if len(path) >= 2 and path[0] == path[-1] and path[0] in ("'", '"'):
                path = path[1:-1]
            try:
                result = evaluate_image(config, db, path, top_k=top_k, session=session)
                save_report(result, report_path(config))
                print(json.dumps(result, ensure_ascii=False, allow_nan=False), flush=True)
            except (OSError, ValueError, RuntimeError) as exc:
                print(json.dumps({"image_path": path, "error": str(exc)}), flush=True)
    except KeyboardInterrupt:
        print("\nScan session stopped.", file=sys.stderr)
        return 130
    finally:
        session.close()
    return 0

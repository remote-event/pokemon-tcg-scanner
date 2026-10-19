"""Persistent recognition-only PaddleOCR worker; input is an already located text crop."""
import base64
from contextlib import redirect_stdout
from io import BytesIO
import json
import os
from pathlib import Path
import sys
from time import perf_counter


def main():
    # Reserve stdout for the protocol, including when native libraries print logs.
    protocol = os.fdopen(os.dup(sys.stdout.fileno()), "w", buffering=1)
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    def send(value):
        protocol.write(json.dumps(value, allow_nan=False) + "\n")
        protocol.flush()
    try:
        os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
        with redirect_stdout(sys.stderr):
            import cv2
            import numpy as np
            import paddle
            import paddleocr
            from PIL import Image
            from paddleocr import TextRecognition
            device = sys.argv[1]
            model_name = sys.argv[2]
            if device.startswith("gpu") and not paddle.is_compiled_with_cuda():
                raise RuntimeError("PaddlePaddle has no CUDA support")
            paddle.set_device(device)
            probe = paddle.ones([2, 2]) @ paddle.ones([2, 2])
            if float(probe.numpy()[0, 0]) != 2:
                raise RuntimeError("OCR device check failed")
            local = {}
            if os.environ.get("TCG_OFFLINE") == "1":
                cache = Path("~/.paddlex/official_models").expanduser()
                directory = cache / model_name
                if not (directory / "inference.json").is_file():
                    raise RuntimeError(f"Offline OCR recognition model missing: {directory}")
                local['model_dir'] = str(directory)
            ocr = TextRecognition(model_name=model_name, device=device, **local)
            list(ocr.predict(np.full((48, 192, 3), 255, np.uint8)))
        send({"ready": True, "device": paddle.device.get_device(),
              "paddle_version": paddle.__version__, "paddleocr_version": paddleocr.__version__,
              "models": [model_name], "recognition_only": True})
    except Exception as exc:
        send({"error": f"{type(exc).__name__}: {exc}"})
        return 1
    for request in sys.stdin:
        try:
            payload = json.loads(request)
            with Image.open(BytesIO(base64.b64decode(payload["image"], validate=True))) as image:
                rgb = np.array(image.convert("RGB"))
            del payload, request
            started = perf_counter()
            with redirect_stdout(sys.stderr):
                results = list(ocr.predict(cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)))
            lines = [{"text": result['rec_text'], "score": float(result['rec_score'])} for result in results]
            elapsed = perf_counter() - started
            del rgb, results
            send({"lines": lines, "inference_seconds": elapsed})
        except Exception as exc:
            send({"error": f"{type(exc).__name__}: {exc}"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

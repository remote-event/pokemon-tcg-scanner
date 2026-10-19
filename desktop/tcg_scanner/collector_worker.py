"""Persistent RF-DETR collector-number detector in its own dependency environment."""
import base64
from contextlib import redirect_stdout
from io import BytesIO
import json
import os
import sys
from time import perf_counter


def main():
    protocol = os.fdopen(os.dup(sys.stdout.fileno()), 'w', buffering=1)
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    def send(value):
        protocol.write(json.dumps(value, allow_nan=False) + '\n')
    try:
        with redirect_stdout(sys.stderr):
            import torch
            from PIL import Image, ImageOps
            from rfdetr import RFDETR
            torch.set_num_threads(4)
            weights, device, threshold = sys.argv[1], sys.argv[2], float(sys.argv[3])
            model = RFDETR.from_checkpoint(weights, device=device)
            model.inference(compile=False, dtype=torch.float16 if device == 'cuda' else torch.float32)
            model.predict(Image.new('RGB', (512, 512)), threshold=threshold)
        send(dict(ready=True, device=device, model='RF-DETR Small', resolution=512))
    except Exception as exc:
        send({'error': f'{type(exc).__name__}: {exc}'})
        return 1
    for line in sys.stdin:
        try:
            payload = json.loads(line)
            started = perf_counter()
            with Image.open(BytesIO(base64.b64decode(payload['image'], validate=True))) as image:
                width, height = image.size
                # Match the confirmed training preprocessing; OCR receives original pixels.
                prepared = ImageOps.grayscale(image).convert('RGB').resize((512, 512), Image.Resampling.LANCZOS)
            with redirect_stdout(sys.stderr):
                result = model.predict(prepared, threshold=threshold)
            boxes = [dict(box=[float(x1)*width/512, float(y1)*height/512,
                               float(x2)*width/512, float(y2)*height/512], confidence=float(score))
                     for (x1, y1, x2, y2), score in zip(result.xyxy, result.confidence)]
            send(dict(boxes=boxes, detection_ms=(perf_counter()-started)*1000))
        except Exception as exc:
            send({'error': f'{type(exc).__name__}: {exc}'})
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

# Models and local assets

Inference models are initialized once and reused. Initial setup explicitly
downloads public models; server startup uses local caches.

| Asset | Source / preparation |
| --- | --- |
| DINOv2 Small | `facebook/dinov2-small`, revision `ed25f3a31f01632728cabb09d1542f84ab7b0056` |
| Card-corner YOLO | `duclvQ/tcg-card-detector`, revision `419633c3986296bf2e3df93ce94d28b1a3fb965d`, `card_pose_yolov8n.pt` |
| Android card detector | ONNX export of the above checkpoint at 480 × 480, opset 17 |
| Collector-number detector | User-supplied fine-tuned RF-DETR Small checkpoint, 512 × 512 grayscale/stretch preprocessing |
| Text recognition | PaddleOCR `en_PP-OCRv5_mobile_rec`; recognition only |

Run `.venv/bin/python scripts/prepare_models.py` to cache the configured public
models. Add `--android` after installing `requirements-export.txt` to export the
phone detector. Hugging Face and Paddle use their normal per-user caches. The
Android model goes in `mobile/android/app/src/main/assets/`; its generated JSON
records source/output hashes and preprocessing.

For OCR, set `collector_weights` in your local config. Relative checkpoint paths
resolve under `data_dir`; absolute paths are also accepted. No retraining is
necessary when a compatible trained checkpoint is available. The project's
checkpoint currently has no public download URL. A future release must include
its checksum, training-data provenance, supported labels and distribution terms.
Using the upstream generic RF-DETR checkpoint will not detect collector numbers.

Without that checkpoint, install with `ocr = "none"` and use visual matching.
Missing or failed OCR workers in an OCR-enabled installation are reported as
degraded in `/health`, with visual fallback. This is not equivalent to full OCR
accuracy. Do not advertise an OCR-enabled release until the trained asset is
available to installers.

Public source bundles omit weights, private photos and catalog databases. A
working Android APK includes the ONNX model and must include its notices. See
[third-party notices](../THIRD_PARTY_NOTICES.md).

# Models and local assets

Inference models are initialized once and reused. Git LFS supplies the project
checkpoint with the repository; model preparation caches the upstream models.

| Asset | Source / preparation |
| --- | --- |
| DINOv2 Small | `facebook/dinov2-small`, revision `ed25f3a31f01632728cabb09d1542f84ab7b0056` |
| Card-corner YOLO | `duclvQ/tcg-card-detector`, revision `419633c3986296bf2e3df93ce94d28b1a3fb965d`, `card_pose_yolov8n.pt` |
| Android card detector | ONNX export of the above checkpoint at 480 × 480, opset 17 |
| Collector-number detector | Included `models/collector-number/rfdetr-small.pth`, fine-tuned RF-DETR Small; 512 × 512 grayscale/stretch |
| Text recognition | PaddleOCR `en_PP-OCRv5_mobile_rec`; recognition only |

Run `python scanner.py models` to cache the configured public
models. Add `--android` after installing `requirements-export.txt` to export the
phone detector. Hugging Face and Paddle use their normal per-user caches. The
Android model goes in `mobile/android/app/src/main/assets/`; its generated JSON
records source/output hashes and preprocessing.

## Included collector-number model

The normal pipeline combines visual matching and collector-number recognition.
The deployed RF-DETR Small checkpoint is versioned in
[`models/collector-number/`](../README.md#included-collector-number-checkpoint) through Git LFS.
Its metadata records the SHA-256; setup verifies it before installing dependencies.

```bash
git lfs install
git lfs pull
python scanner.py setup
python scanner.py models
```

Setup uses the included checkpoint and creates persistent RF-DETR and PaddleOCR
worker environments. It does not retrain the model. `--collector-weights PATH`
can select a replacement trained checkpoint. For an existing installation, follow
[worker setup](../README.md#add-collector-number-recognition-to-an-existing-installation)
instead of rerunning the installer.

If a checkpoint is only a small text file beginning with `version
https://git-lfs.github.com/spec/v1`, Git LFS has not downloaded the actual model.
Run `git lfs pull` in a Git clone. Source-only ZIPs from `scripts/package_source.py`
exclude binary assets; clone with Git LFS for a complete installation.

The original 500 training images, COCO bounding boxes, grouping and split metadata
are in [`training/collector_numbers/dataset/`](../README.md#included-training-dataset).
The [training guide](../README.md#training-your-own-detector) reproduces preprocessing
and the original 350/100/50 split. Training runs and optimizer checkpoints remain local.

## Runtime behavior

`collector_weights` in the local config can be absolute or relative to `data_dir`.
The shared default `../models/collector-number/rfdetr-small.pth` resolves to the
repository's model. Existing local configs continue to take precedence.

Unreadable or low-confidence OCR falls back to visual matching when its confidence
checks pass. A failed worker is reported as degraded in `/health`. For deliberate
visual-only installations, pass `--visual-only` to setup. This is a reduced mode,
not the standard scanning pipeline.

DINOv2, YOLO and PaddleOCR remain pinned upstream downloads, rather than duplicate
copies in this repository. Android uses an ONNX export of the YOLO detector;
prepare it with `scripts/prepare_models.py --android`. A working APK includes that
model and its notices. See [third-party notices](../THIRD_PARTY_NOTICES.md).

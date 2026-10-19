# Optional collector-number training

Fine-tune RF-DETR Small using the included COCO dataset or your own labels. This is
separate from installing the scanner: normal setup uses the included trained
checkpoint for inference. Training does not deploy or replace server models automatically.

## Environment

From the repository root, on Linux/WSL with a working NVIDIA driver, C compiler
and Python development headers:

```bash
python3 -m venv .venvs/training
.venvs/training/bin/python -m pip install torch==2.14.1 torchvision==0.29.1 --index-url https://download.pytorch.org/whl/cu130
.venvs/training/bin/python -m pip install -r training/collector_numbers/requirements.txt
```

RF-DETR downloads pretrained weights on first use. Match your Python headers to
your interpreter version; custom header installations can use `C_INCLUDE_PATH`.
Do not install training extras into the inference server or Paddle environment.

## Prepare data

The repository includes 500 original card images and 501 collector-number boxes
in `training/collector_numbers/dataset/`. Run `git lfs pull` to obtain the images.
The data
preparation script performs EXIF auto-orientation, grayscale conversion into three
RGB channels, and 512 × 512 stretch, with a seeded 70/20/10 train/validation/test
split. Bounding boxes receive the same transforms.

```bash
.venvs/training/bin/python training/collector_numbers/prepare.py \
  --source training/collector_numbers/dataset \
  --collection training/collector_numbers/dataset/grouping.json \
  --output artifacts/collector-number-training/dataset \
  --resolution 512 --mode stretch
```

The optional collection JSON groups scans of the same card across the split;
exact image duplicates also stay together. Omit `--collection` only if identities
are unavailable. The script validates image/annotation references and boxes,
refuses an existing output directory, and preserves originals. The generated
manifest records hashes, preprocessing and split assignments. With the included
images and grouping file, this reproduces the saved `dataset/splits.json`: 350
training images, 100 validation images and 50 test images. Preprocessed copies
and new training outputs stay under ignored `artifacts/`. To use your own data,
replace the source and collection paths above.

## Train and inspect

```bash
bash training/collector_numbers/train.sh
```

The Bash launcher uses `.venvs/training`, the prepared dataset above, and
`artifacts/collector-number-training/run-001` by default. Override with
`TCG_TRAINING_PYTHON`, `TCG_TRAINING_DATASET` and `TCG_TRAINING_RUN`. It resumes a
saved `last.ckpt`, keeps live terminal stats, and saves terminal transcripts.
A lock prevents concurrent writers to a run. Ctrl+C stops training; resuming
repeats any unfinished epoch.

Defaults: RF-DETR Small, batch 2 × 8 accumulation, up to 100 epochs, early stopping,
EMA and gradient checkpointing. Multi-scale and random augmentations are disabled
for the baseline. The held-out test split is evaluated after training using the
best validation checkpoint. Training competes with inference for GPU resources.

Matplotlib plots and JSON summaries live in `<run>/graphs/`. To regenerate them:

```bash
.venvs/training/bin/python training/collector_numbers/report.py --run /path/to/run
```

Plots include total/box/classification losses and available validation metrics.
Precision/recall values across epochs are not a confidence-threshold PR curve.
`launch.json` records options. `completed.json` is written after training and the
final evaluation succeed. Use `checkpoint_best_total.pth` for inference; resume
training from the full Lightning checkpoint, not stripped inference weights.

```bash
.venvs/training/bin/python -B -m unittest discover -s training/collector_numbers -p 'test_*.py'
```

Tests cover grouped splits, coordinate transforms and resumed metric histories.
See [RF-DETR](https://github.com/roboflow/rf-detr) for upstream model documentation.

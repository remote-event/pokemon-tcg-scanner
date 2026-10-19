# Collector-number detector

RF-DETR Small fine-tuned locally to locate the printed collector-number region on
Pokémon cards. The server crops this region and sends it to PaddleOCR for character
recognition. This model predicts a bounding box, not the card identity or text.

## Included checkpoint

- File: `rfdetr-small.pth` (Git LFS), 127,363,541 bytes.
- SHA-256: `c5e4a19c65c6b5daa15007f504691553092f076ef1110a6b75263ef8948df79e`.
- Architecture: RF-DETR Small, one class (`collection_number`), RF-DETR 1.11.2.
- Input: EXIF auto-orientation, grayscale copied into RGB channels, 512 × 512 stretch.
- Source: the project's `checkpoint_best_total.pth`, selected from its local training run.
- Export: inference tensors and architecture metadata; training state and absolute
  workstation paths removed. All model tensors match the deployed checkpoint exactly.

The source checkpoint's checksum and export details are in `rfdetr-small.json`.
`SHA256SUMS` verifies the published file. It is not a training-resume checkpoint.

## Training data and reproduction

[Dataset](../../training/collector_numbers/dataset/README.md): 500 original card
images with 501 COCO bounding boxes. The original split is 350 train / 100 validation /
50 test, seeded at 42 and grouped by card identity and duplicate-image hash.
`training.json` records the run's hyperparameters. The
[training guide](../../training/collector_numbers/README.md) explains reproduction.

The dataset is small and does not establish accuracy across every Pokémon set,
language, printing, lighting condition or camera. OCR and visual matching apply
separate confidence checks; ambiguous cards remain unsaved for review.

## Installation

Run `git lfs install && git lfs pull` in the repository, then the normal setup
command. Setup verifies the SHA-256 and enables RF-DETR plus PaddleOCR automatically.
You do not need to retrain or locate a separate checkpoint download.

## Provenance and terms

Based on [Roboflow RF-DETR Small](https://github.com/roboflow/rf-detr),
under Apache-2.0; see `LICENSE-APACHE.txt`. This export preserves the trained
architecture and weights without quantization or retraining. Card artwork and
trademarks remain the property of their respective owners; the project's MIT code
license does not relicense them. See [third-party notices](../../THIRD_PARTY_NOTICES.md).

# Collector-number dataset

500 original 630 × 880 card images and 501 bounding boxes from the project's
collector-number labeling dataset. The labels cover the full printed number,
including its denominator where present. There is one class: `collection_number`.
These are detection labels, not character-recognition transcripts.

## Files

- `*.jpg`: original images, stored through Git LFS.
- `_annotations.coco.json`: COCO image records and bounding-box annotations.
- `grouping.json`: only image filenames and card IDs, used to keep repeated cards together.
- `splits.json`: original image hashes, split assignments, seed and preprocessing.

Use `git lfs install && git lfs pull` to obtain the images. The
[training guide](../README.md#prepare-data) converts them to 512 × 512 grayscale RGB
images with auto-orientation and stretch resizing. It reproduces the original
350 train / 100 validation / 50 test split, with seed 42, keeping repeated card
identities and exact image duplicates in one split.

The original filenames and bytes are retained so the saved hashes remain
verifiable. Prepared images, new checkpoints and run logs go under `artifacts/`.
Personal collection databases and unrelated scan photos are not part of this dataset.

## Provenance and limits

This is the project dataset previously exported for collector-number training,
now published alongside the trained model. The COCO source identifies its boxes
as visually reviewed. It contains a limited selection of cards and is not a
comprehensive benchmark of all sets, languages or capture conditions.

Pokémon card names, artwork and trademarks belong to their respective owners.
The project's MIT code license does not relicense that artwork. See
[third-party notices](../../../THIRD_PARTY_NOTICES.md).

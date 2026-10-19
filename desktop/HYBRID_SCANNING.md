# Collector-number recognition

Android runs the existing four-corner YOLO model using ONNX Runtime and sends
its upright 630 × 880 JPEG crop. The server negotiates `card_crop_v1`; older
clients can continue sending whole frames. Whole-frame YOLO remains available
on the server for compatibility and saved-photo evaluation.

The server initializes DINOv2, GPU FAISS, the existing RF-DETR Small checkpoint,
and PaddleOCR `TextRecognition` once at startup. RF-DETR and Paddle run in
separate persistent Python processes because their dependency versions differ
from the embedding server. Neither worker writes temporary image files.

Two branches run concurrently on the RTX 4070: DINOv2 → existing FAISS top 10,
and RF-DETR → padded number crop → `en_PP-OCRv5_mobile_rec`. The Paddle entry
point is **TextRecognition**, never the OCR/document pipeline. It loads no text
detector, orientation classifier or document model. See the upstream
[recognition API](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/module_usage/text_recognition.en.md).

RF-DETR keeps its trained preprocessing: grayscale, RGB channels, 512 × 512
stretch. Coordinates are scaled back to the original card crop; recognition
uses those original pixels with 12% padding (minimum 2 pixels). Recognition
performs its own height normalization. Up to three distinct number boxes are
allowed; conflicting confident readings remain unresolved while fresh frames are tried.

## Matching and confirmation

Number lookup covers **all catalog metadata**, including cards without an
embedding. Digits, letters, prefixes/suffixes, slashes, hyphens and leading-zero
variants are normalized without guessing that O means 0. Numeric denominators
filter known set sizes; unknown or alphanumeric set sizes retain candidates.
The lightweight English model's character vocabulary still limits which rare
symbols it can actually read; syntax support does not guarantee OCR accuracy.

FAISS scores/order are retained in `visual_matches`. For number matches outside
the FAISS top 10, the server calculates exact inner-product scores from the
same stored normalized vectors on the GPU. It does not replace or rebuild the
FAISS algorithm. Candidates with no embedding retain a null visual score.

Defaults in `config.toml`: detector confidence 0.5, OCR confidence 0.85, visual
score 0.80, visual margin 0.015, OCR-supported visual tolerance 0.03. These are
heuristics, not calibrated probabilities. Strong agreement can select a card;
ties, unknown confident numbers and substantial contradictions trigger a fresh-frame retry.
Unreadable/failed OCR falls back to FAISS only when its score and margin pass.
OCR runtime failure disables that branch until restart and keeps FAISS usable.

Uncertain scans remain unsaved while the camera continues. The server retries
up to three stable views, with a 1.2-second cooldown on unchanged views. A clear
result saves automatically. After three uncertain attempts, a small tilt hint
asks for a changed view; improved sharpness, exposure or glare also rearms the
scan without removing the card. Scanning another card remains available immediately.
There is no Android confirmation dialog. The web retains optional candidate
review. Manual confirmation is idempotent, bound to the collection at scan time,
and expires after three minutes. Changing the active collection invalidates it.
Skip leaves the card unsaved. Existing exports, edits, moves and collection
storage continue through the same services. Verification records distinguish
`ocr_supported`, `identified` and `user_confirmed`.

The presentation gate prevents repeated AI work on an unchanged accepted card
and bounds uncertain-card retries to three attempts. A bounded cache also
reuses byte-identical crops. Empty/uncertain
camera signals preserve removal and dark/covered-lens behavior. One frame stays
in flight per phone; the server serializes FAISS access and bounds OCR work to
one branch, preventing an unbounded GPU queue.

## Installation and validation

See [backend setup](README.md) for the isolated worker environments and
[model setup](../docs/MODELS.md) for required local assets. Existing installations
can point at their trained checkpoint without moving or retraining it.

The maintained benchmarks accept explicit image manifests and configuration
paths. They snapshot the catalog and report detector, embedding, FAISS, crop,
recognition, matching and total timings separately. Serial/concurrent runs clear
inference caches. Startup is excluded from warmed timings. Phone capture, card
stabilization, network and persistence are not included in the inference benchmark.

No private benchmark outputs or accuracy claims are part of the source release.
Use independently labeled photos, including similar artwork across sets, when
assessing a new model/catalog. Similarity thresholds are heuristics rather than
calibrated probabilities. Report hardware, dependency versions and test-set size
alongside performance or accuracy numbers.

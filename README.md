# pokemon-tcg-scanner

Scan Pokémon cards with your Android phone and organize them into collections.
Your computer identifies each card over your local network.

- **Scan and save** cards directly to a collection.
- **Manage your collection** in a browser: search, edit, and review uncertain matches.
- **Export your cards** as a ZIP with JSON metadata and the original scanned images.

## How a scan works

1. **Find the card.** YOLOv8n detects the card's corners on your Android phone.
   The app straightens and crops the card, then sends it to your local server.
2. **Compare the artwork.** DINOv2 Small creates an image embedding—a numerical
   representation of the card. FAISS compares it with the reference catalog
   to find visually similar cards.
3. **Read the collector number.** In parallel, a trained RF-DETR Small
   model finds the number region. PaddleOCR (`en_PP-OCRv5_mobile_rec`) reads only
   that small crop, without running another text detector.
4. **Combine the evidence.** The number is checked against the entire card
   catalog, including cards outside the top visual matches. Candidates supported
   by both paths are preferred, helping distinguish similar artwork and numbers
   shared across sets. If OCR is unavailable or unreliable, visual matching is
   used on its own.
5. **Save a confident result.** The card is added to your selected collection.
   Ambiguous results stay unsaved while the app tries another view; you can also
   review candidates in the browser. An unchanged accepted card is not repeatedly
   processed or saved.

All AI after the initial card crop runs on your computer. The trained collector-number
model is included in the repository, and setup enables both matching paths.
[Model setup](docs/MODELS.md) · [Detailed matching process](desktop/HYBRID_SCANNING.md)

## What you need

- **Computer:** Linux or WSL2, Python 3.11/3.12, and an NVIDIA GPU recommended
  for real-time scanning. CPU mode is also available.
- **Git LFS:** required to clone the included model and training images.
- **Phone:** Android 8.0+ on the same private network as your computer.
- **Android build tools:** JDK 17, Android SDK 36, and ADB. There is no
  downloadable APK yet; you build the app yourself.

Internet is needed for setup and card catalog downloads. Identification runs
locally; card artwork and price lookups may still use the internet.

## Quick start

### 1. Set up the server

Install [Git LFS](https://git-lfs.com/) first, then run in a Linux or WSL terminal:

```bash
git lfs install
git clone https://github.com/remote-event/pokemon-tcg-scanner.git
cd pokemon-tcg-scanner
git lfs pull

bash setup.sh --device cuda
.venv/bin/python scripts/prepare_models.py
(cd desktop && ../.venv/bin/python -m tcg_scanner sync)

bash start-server.sh
```

Use `--device cpu` instead if you do not have a supported NVIDIA GPU. GPU setup
requires a driver compatible with CUDA 13.0. The first catalog sync can take a
while and includes Scarlet & Violet onward by default.

Open the address printed by the server (`http://<computer-ip>:8765`) in your
browser and create a collection. Keep this terminal running.

[Setup prerequisites and configuration](desktop/README.md) ·
[WSL and connection help](docs/TROUBLESHOOTING.md)

### 2. Install the Android app

In a second terminal, from the repository folder, with your phone connected by
USB and USB debugging enabled:

```bash
.venv/bin/python -m pip install -r desktop/requirements/requirements-export.txt
.venv/bin/python scripts/prepare_models.py --android
bash mobile/build.sh
adb install -r mobile/android/app/build/outputs/apk/debug/app-debug.apk
```

[Android SDK setup and Windows tools from WSL](mobile/README.md#prepare-and-build)

### 3. Scan a card

Open the app, allow camera access, and select your collection at the bottom.
The app finds the server automatically; you can also enter its IP in settings.
Hold one card steady in view. Confident matches save automatically; uncertain
matches can be reviewed in the web interface.

Next time, just run `bash start-server.sh` and open the app.

## Training your own detector

Training is separate from using the scanner. The repository includes the trained
checkpoint, 500 labeled images, COCO annotations, and the original 70/20/10 split
information. [Training instructions](training/collector_numbers/README.md) cover
preparing the data, retraining RF-DETR Small, and saving graphs and metrics.

## More information

- [Backend configuration, storage, and commands](desktop/README.md)
- [Android app](mobile/README.md) · [Web interface](web/README.md)
- [Troubleshooting](docs/TROUBLESHOOTING.md) · [How matching works](desktop/HYBRID_SCANNING.md)
- [Contributing](CONTRIBUTING.md) · [Release guide](docs/RELEASING.md)

Original code is [MIT licensed](LICENSE). Models and dependencies retain their
[own licenses](THIRD_PARTY_NOTICES.md), including the AGPL-3.0 card detector.
This is an independent community project, not an official Pokémon product.

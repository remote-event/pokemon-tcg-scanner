# pokemon-tcg-scanner

Scan Pokémon cards with your Android phone and organize them into collections.
Your computer identifies each card over your local network.

- **Scan and save** cards directly to a collection.
- **Manage your collection** in a browser: search, edit, and review uncertain matches.
- **Export your cards** as a ZIP with JSON metadata and the original scanned images.

**Android camera → local server → card identification → collection**

## What you need

- **Computer:** Linux or WSL2, Python 3.11/3.12, and an NVIDIA GPU recommended
  for real-time scanning. CPU mode is also available.
- **Phone:** Android 8.0+ on the same private network as your computer.
- **Android build tools:** JDK 17, Android SDK 36, and ADB. There is no
  downloadable APK yet; you build the app yourself.

Internet is needed for setup and card catalog downloads. Identification runs
locally; card artwork and price lookups may still use the internet.

## Quick start

### 1. Set up the server

Run in a Linux or WSL terminal:

```bash
git clone https://github.com/remote-event/pokemon-tcg-scanner.git
cd pokemon-tcg-scanner

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

## Optional: collector-number recognition

The quick start identifies cards by visual similarity using DINOv2 and FAISS.
Collector-number recognition adds RF-DETR and recognition-only PaddleOCR to
help distinguish cards with similar artwork across sets.

**This requires a trained collector-number checkpoint, which is not currently
available as a public download.** Visual matching works without it.
[Enable collector-number recognition](docs/MODELS.md#enable-collector-number-recognition).

## Models and technologies

| Technology | What it does | Runs on |
| --- | --- | --- |
| YOLOv8n card detector + ONNX Runtime | Finds and crops the card before sending it | Android |
| DINOv2 Small + PyTorch | Turns card artwork into a vector for visual matching | Server |
| FAISS | Searches the reference catalog for visually similar cards | Server |
| RF-DETR Small · optional | Locates the collector-number region | Server |
| PaddleOCR `en_PP-OCRv5_mobile_rec` · optional | Reads characters from that crop; recognition only | Server |
| Kotlin + Jetpack Compose + CameraX | Native app interface and camera capture | Android |
| Python + FastAPI + WebSockets | Serves the API and handles live scans | Server |
| SQLite | Stores catalog data and collections | Server |
| HTML, CSS, and JavaScript | Collection management and exports | Browser |
| TCGdex API | Supplies card metadata, reference artwork, and prices | External service |

Only card detection runs on the phone; visual matching and optional OCR run on
your computer. See [model details](docs/MODELS.md) for checkpoints and preparation.

## More information

- [Backend configuration, storage, and commands](desktop/README.md)
- [Android app](mobile/README.md) · [Web interface](web/README.md)
- [Troubleshooting](docs/TROUBLESHOOTING.md) · [How matching works](desktop/HYBRID_SCANNING.md)
- [Contributing](CONTRIBUTING.md) · [Release guide](docs/RELEASING.md)

Original code is [MIT licensed](LICENSE). Models and dependencies retain their
[own licenses](THIRD_PARTY_NOTICES.md), including the AGPL-3.0 card detector.
This is an independent community project, not an official Pokémon product.

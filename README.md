# pokemon-tcg-scanner

Scan Pokémon cards with an Android phone, identify them on your own computer,
and organize the results into collections. Images travel over your local network.
The web interface supports search, corrections, collection management, and ZIP
exports containing JSON metadata and the original scanned images.

The phone detects and crops the card with YOLO. The backend combines DINOv2
image embeddings and FAISS search with optional collector-number detection and
PaddleOCR character recognition. Uncertain matches stay unsaved until resolved;
the web interface lets you review candidates.

## Requirements

- Linux x86_64 or WSL2, Python 3.11 or 3.12, and Python's `venv` support.
- NVIDIA GPU for real-time use. The GPU installer uses PyTorch CUDA 13.0 wheels;
  your NVIDIA driver must support that runtime. `nvidia-smi` must work in Linux/WSL.
  CPU mode is available but is not a real-time performance target.
- Android 8.0+ phone and a computer on the same trusted private network.
- Internet access during dependency installation, model preparation, and catalog
  synchronization. Scanning uses cached inference models; reference artwork and
  market-price lookups can still contact TCGdex.
- JDK 17 and Android SDK 36 to build the Android client from source.

On minimal Ubuntu installations, install `python3-venv`, `libgl1` and
`libglib2.0-0` using your system package manager before setup (OpenCV needs the
shared libraries). Training additionally needs Python development headers and a C compiler.

The reference catalog, downloaded models, collector-number checkpoint and personal
scans are **not included** in source releases. Do not copy another installation's
collection database to get started.

## Install the backend

Clone the repository and enter its directory:

```bash
git clone https://github.com/remote-event/pokemon-tcg-scanner.git
cd pokemon-tcg-scanner
```

From the root of your checkout:

```bash
# Choose cuda or cpu. The installer creates .venv and desktop/config.local.toml.
bash setup.sh --device cuda

# Download the DINOv2 and card-detector models for subsequent offline startup.
.venv/bin/python scripts/prepare_models.py

# Populate metadata, embeddings and the FAISS index. This can take a while.
(cd desktop && ../.venv/bin/python -m tcg_scanner sync)

# Check dependencies, caches, catalog and selected compute backend.
(cd desktop && ../.venv/bin/python -m tcg_scanner.doctor)

bash start-server.sh
```

The server prints its private address. Open `http://<computer-ip>:8765/`, create a
collection, and select it for scanning. For the initial catalog, `--set sv01`
limits synchronization to one set; omit it to populate the default set range.
The default catalog starts with Scarlet & Violet. Configure `since` or supply
explicit `--set` options if you want other sets.

For collector-number OCR, supply the existing **trained RF-DETR Small checkpoint**
at installation time:

```bash
bash setup.sh --device cuda --collector-weights /path/to/rfdetr-small.pth
.venv/bin/python scripts/prepare_models.py
```

This creates isolated RF-DETR and PaddleOCR environments. It does not train a
model. There is currently no hosted download for this project's trained
checkpoint; without it, install the visual-matching mode above. The upstream
RF-DETR pretrained model is not a replacement for collector-number training.
See [model setup](docs/MODELS.md) and [manual installation](desktop/README.md).

For WSL, use mirrored networking and allow the selected TCP port and UDP 5353
from your private subnet. Setup does not change firewall rules. See
[network troubleshooting](docs/TROUBLESHOOTING.md).

## Install the Android app

Prepare the phone detector and build the APK:

```bash
.venv/bin/python -m pip install -r desktop/requirements/requirements-export.txt
.venv/bin/python scripts/prepare_models.py --android
bash mobile/build.sh
adb install -r mobile/android/app/build/outputs/apk/debug/app-debug.apk
```

This is a local development build; each developer uses their own signing key.
Open **pokemon-tcg-scanner**, allow camera access, and select the collection using
the bottom selector. Discovery is automatic; the gear menu also accepts a private
server IP. Keep one card visible and reasonably steady until the result appears.

[Android setup and WSL builds](mobile/README.md) · [Web collections](web/README.md)

## Configuration and data

`desktop/config.toml` contains portable defaults. `desktop/config.local.toml` is
ignored by Git and takes precedence. `--config` or `TCG_CONFIG` selects another
file; `TCG_<SETTING>` environment variables override settings within it, and CLI
options override those. Data and worker paths in TOML resolve relative to that
file. Collector weights resolve relative to `data_dir`.

- `data/metadata.sqlite3`: catalog metadata and reference vectors.
- `data/indexes/`: rebuildable FAISS index.
- `data/scan-history/`: your collections, scanned photos and related caches.
- `artifacts/`: optional developer reports; never required at server startup.

Back up the entire scan-history directory while the server is stopped. Export ZIP
in the web interface is a portable collection export, not a full server backup.

## Repository layout

| Path | Purpose |
| --- | --- |
| `desktop/tcg_scanner/` | Backend runtime, APIs, inference and storage |
| `desktop/tests/` | Offline regression tests |
| `desktop/tools/` | Explicit benchmarks, model export and discovery diagnostics |
| `web/` | Static collection UI; no frontend build step |
| `mobile/` | Native Kotlin/Compose Android app and Bash build/test entry points |
| `training/collector_numbers/` | Optional training utilities, separate from inference |
| `scripts/` | Installation, model preparation and source packaging |
| `docs/` | Models, troubleshooting and release procedure |

## Development and releases

```bash
.venv/bin/python -m pip install -r desktop/requirements/requirements-dev.txt
(cd desktop && ../.venv/bin/python -B -m unittest discover -s tests)
.venv/bin/python -m ruff check desktop scripts training
python3 scripts/package_source.py
```

The source-bundle command creates `dist/pokemon-tcg-scanner-source.zip` using an
allowlist, excluding personal data, model weights, local settings and build outputs.
It refuses to overwrite an existing archive. CI checks the backend, training
utilities and Android compilation without using a private catalog or GPU.
See [contributing](CONTRIBUTING.md) and [releasing](docs/RELEASING.md).

Original project code is [MIT licensed](LICENSE). Third-party dependencies and
models retain their own licenses, including the AGPL-3.0 card detector; see
[third-party notices](THIRD_PARTY_NOTICES.md). This is an independent community
project, not an official Pokémon product.

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

- **Computer:** Windows x64, Linux x64, or macOS 14+ on Apple silicon,
  with Python 3.11/3.12. NVIDIA GPU acceleration is available on Windows and Linux x64;
  CPU mode runs the same identification pipeline.
- **Git LFS:** required to clone the included model and training images.
- **Phone:** Android 8.0+ on the same private network as your computer.
- **Android app:** download the signed APK from [Android APK builds](https://github.com/remote-event/pokemon-tcg-scanner/actions/workflows/android-apk.yml).
  JDK/Android SDK are only needed if you choose to build it yourself.

Internet is needed for setup and card catalog downloads. Identification runs
locally; card artwork and price lookups may still use the internet.

## Quick start

### 1. Set up the server

Install [Git LFS](https://git-lfs.com/) first, then run in PowerShell, Command Prompt,
or your Linux/macOS terminal. Use `python3` if your system has no `python` command:

```bash
git lfs install
git clone https://github.com/remote-event/pokemon-tcg-scanner.git
cd pokemon-tcg-scanner
git lfs pull

python scanner.py setup
python scanner.py models
python scanner.py sync

python scanner.py server
```

Setup selects NVIDIA CUDA when available, otherwise CPU. Pass `--device cpu`
to force CPU mode or `--device cuda` to require NVIDIA acceleration. CUDA setup
requires a compatible driver. The first catalog sync can take a while and includes
Scarlet & Violet onward by default. [Platform details](docs/PLATFORMS.md).

Open the address printed by the server (`http://<computer-ip>:8765`) in your
browser and create a collection. Keep this terminal running.

[Setup prerequisites and configuration](desktop/README.md) ·
[WSL and connection help](docs/TROUBLESHOOTING.md)

### 2. Install the Android app

Open [Android APK builds](https://github.com/remote-event/pokemon-tcg-scanner/actions/workflows/android-apk.yml),
select the latest successful run, and download its **pokemon-tcg-scanner-android**
artifact. Extract the ZIP and open `pokemon-tcg-scanner.apk` on your phone.
Allow installation from your browser or file manager when Android asks.
GitHub requires you to sign in to download Actions artifacts.

The APK includes the phone detector. GitHub builds it automatically when Android
code changes; maintainers can also select **Run workflow**. The same release key
is used for updates. Existing local debug installs use a different signing key;
see [installation and build details](mobile/README.md#install-the-github-build).

[Build the app yourself](mobile/README.md#prepare-and-build)

### 3. Scan a card

Open the app, allow camera access, and select your collection at the bottom.
The app finds the server automatically; you can also enter its IP in settings.
Hold one card steady in view. Confident matches save automatically; uncertain
matches can be reviewed in the web interface.

Next time, just run `python scanner.py server` and open the app.

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

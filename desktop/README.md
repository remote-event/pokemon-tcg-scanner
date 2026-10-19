# Backend

The resident server owns model initialization, matching, collections, images and
pricing. Android sends cropped cards over a bounded WebSocket stream; the browser
uses the same collection store through the HTTP API. See the root
[quick start](../README.md) for a new installation.

See [platform support](../docs/PLATFORMS.md) for native Windows, Linux and macOS.
`python scanner.py setup` selects platform-compatible dependencies and interpreter paths.

## Setup prerequisites

On minimal Ubuntu installations, install `python3-venv`, `libgl1` and
`libglib2.0-0` before running setup. OpenCV needs these shared libraries.
Training additionally needs Python development headers and a C compiler.
For GPU setup, `nvidia-smi` must work inside Linux/WSL and your NVIDIA driver
must support the installer's CUDA 13.0 runtime.

Setup downloads dependencies; model preparation and catalog sync download the
remaining assets. The collector checkpoint and training images are included through Git LFS;
reference databases and personal collection scans stay local. For a smaller first catalog, use
`(cd desktop && ../.venv/bin/python -m tcg_scanner sync --set sv01)`.
The default catalog starts with Scarlet & Violet; configure `since` or pass
explicit `--set` options for other sets.

## Configuration and storage

`desktop/config.toml` contains portable defaults. The installer creates
`desktop/config.local.toml`, which is ignored by Git and takes precedence.
`--config` or `TCG_CONFIG` selects another file. `TCG_<SETTING>` environment
variables override file settings, and CLI options override those.
Data and worker paths in TOML resolve relative to the configuration file;
collector weights resolve relative to `data_dir`.

| Path | Contents |
| --- | --- |
| `data/metadata.sqlite3` | Catalog metadata and reference vectors |
| `data/indexes/` | Rebuildable FAISS index |
| `data/scan-history/` | Collections, scanned photos and related caches |
| `artifacts/` | Optional developer reports |

Back up the entire scan-history directory while the server is stopped. The
web interface's ZIP export is a portable collection export, not a full backup.

## Manual Linux installation or recovery

Use Python 3.11/3.12 on Linux x86_64 or WSL2. These commands are also the recovery
path if the setup script stopped during dependency downloads. They do not overwrite
configuration or personal data. Choose one FAISS backend per environment.

```bash
python3 -m venv .venv
# GPU; use /whl/cpu instead for a CPU installation.
.venv/bin/python -m pip install torch==2.14.1 torchvision==0.29.1 --index-url https://download.pytorch.org/whl/cu130
.venv/bin/python -m pip install -r desktop/requirements/requirements-gpu.txt -r desktop/requirements/requirements-yolo.txt -r desktop/requirements/requirements-server.txt
# Use requirements.txt instead of requirements-gpu.txt for CPU FAISS.
# Only on a new installation; preserve an existing config.local.toml:
cp -n desktop/config.toml desktop/config.local.toml
```

On Windows, use the portable installer; its CUDA mode uses CPU FAISS. On macOS,
use the CPU installer with PyPI wheels. Do not apply the Linux GPU dependency file
to those platforms.

Set `device = "cuda"` and `faiss_device = "cuda"` for GPU use, or `"cpu"` for both.
Shared defaults enable collector recognition; complete the worker setup below
for a manual installation. `python scanner.py setup --device cuda` performs both steps
automatically on a new checkout.
Model-preparation and catalog commands are in the root README. Dependency upgrades
that alter DINOv2 preprocessing can require rebuilding reference embeddings;
Pillow, Transformers and the model revision are pinned for that reason.

### Add collector-number recognition to an existing installation

```bash
python3 -m venv .venvs/collector
.venvs/collector/bin/python -m pip install torch==2.14.1 torchvision==0.29.1 --index-url https://download.pytorch.org/whl/cu130
.venvs/collector/bin/python -m pip install -r desktop/requirements/requirements-collector.txt
python3 -m venv .venvs/paddle
.venvs/paddle/bin/python -m pip install paddlepaddle-gpu==3.2.0 --index-url https://www.paddlepaddle.org.cn/packages/stable/cu126/
.venvs/paddle/bin/python -m pip install -r desktop/requirements/requirements-paddle-rec.txt
```

For CPU, choose PyTorch's `/whl/cpu` index, install `paddlepaddle==3.2.0` from
Paddle's `/packages/stable/cpu/` index, and set `ocr_device = "cpu"`.
In `desktop/config.local.toml` set:

```toml
ocr = "paddle"
ocr_device = "gpu:0"
ocr_python = "../.venvs/paddle/bin/python"
collector_python = "../.venvs/collector/bin/python"
collector_weights = "../models/collector-number/rfdetr-small.pth"
```

Then run `.venv/bin/python scripts/prepare_models.py`. RF-DETR inference dependencies
are separate from training extras and from the embedding and Paddle environments.
The [architecture](HYBRID_SCANNING.md) explains why.

To deliberately install only visual matching, use `--visual-only` on a new setup
or set `ocr = "none"` in an existing local config. The normal pipeline uses both
visual matching and collector-number recognition.

## Run and inspect

```bash
python scanner.py server --host <private-ip> --port 8765
# Detailed troubleshooting only:
python scanner.py server --log-level debug --access-log
# Installation diagnostics, without modifying databases or downloading models:
python scanner.py doctor
```

The normal entry point on every platform is `python scanner.py server`.

Normal logs include startup, connections, accepted scans and errors. HTTP access
logging is off by default. `/health` reports the selected compute devices, model
readiness and bounded inference counters. An unavailable OCR worker is reported
as degraded; visual matching remains available. Model reloads require a restart.

The server deliberately rejects wildcard/public binds. It has no user accounts
or internet-facing authentication. Use it only on a trusted private network.

## Catalog CLI

Run from `desktop/` using `../.venv/bin/python -m tcg_scanner`:

| Command | Purpose |
| --- | --- |
| `sync [--set sv01]` | Discover catalog cards and generate missing embeddings |
| `embed --existing` | Process existing metadata without catalog discovery |
| `status` | Inspect stored metadata and embedding counts |
| `verify` / `rebuild-index` | Validate or rebuild the FAISS index from stored vectors |
| `identify /path/card.jpg` | Identify one saved photo |
| `evaluate /path/card.jpg --expected-card sv01-001` | Produce a labeled evaluation report |
| `scan` | Reuse a resident model for interactive photo identification |

`--help` lists all options. Legacy image cleanup is retained for migration and
requires an explicit `--confirm-delete`; it is never part of setup or startup.

## Module responsibilities

- `server.py`: FastAPI routes, LAN validation, WebSocket transport and lifecycle.
- `runtime.py`: client sessions, serialized inference, durable saves and manual review.
- `live.py`, `live_gate.py`, `quality.py`: frame quality, stability and hybrid matching.
- `collector.py`: collector-number crops and catalog-wide candidate matching.
- `ocr.py`, `collector_worker.py`, `ocr_worker.py`: persistent isolated workers and recognition-only OCR.
- `collections.py`, `history.py`, `pricing.py`: collection storage, exports, images and price cache.
- `api.py`, `db.py`, `pipeline.py`, `vectors.py`, `embeddings.py`: catalog and visual search.
- `evaluation.py`, `scanning.py`: reusable command-line identification and evaluation.

Stored-library migrations remain supported. Old model-verification queues and
fixed-footer OCR matching have been removed; live results use the current hybrid
pipeline before saving.

## Tests and benchmarks

```bash
(cd desktop && ../.venv/bin/python -B -m unittest discover -s tests)
.venv/bin/python -m ruff check desktop scripts training
.venv/bin/python desktop/tools/benchmark_hybrid.py --manifest /path/cards.json
.venv/bin/python desktop/tools/check_live_quality.py --manifest /path/cards.json
.venv/bin/python desktop/tools/benchmark_card.py /path/card.jpg
```

The manifest is a JSON list; image paths are relative to its location:

```json
[{"file": "card.jpg", "expected": "sv01-001", "cropped": false}]
```

Hybrid and quality benchmarks use a temporary catalog snapshot. Personal sample
photos are not distributed. `report_hybrid.py <report.json>` produces plots when
Matplotlib is installed. The transport benchmark requires `--server`, `--images`
and an **isolated test server**: it temporarily selects its own collection, then
cleans up and restores the previous selection. `test_discovery.py <private-ip>`
checks mDNS and health. These tools run only when explicitly invoked.

# Supported platforms

WSL is one way to run the scanner on Windows; native Windows also has its own
installation path. The Python launcher uses the same commands on every platform.
All platforms use the included collector-number detector and recognition-only
PaddleOCR. Users do not need to train a model.

| Platform | DINOv2 / RF-DETR / PaddleOCR | FAISS |
| --- | --- | --- |
| Windows x64 | NVIDIA CUDA or CPU | CPU |
| Linux x64, including WSL2 | NVIDIA CUDA or CPU | GPU with CUDA, otherwise CPU |
| Linux ARM64 | CPU | CPU |
| macOS 14+ on Apple silicon | CPU | CPU |

Use native ARM64 Python on Apple silicon. The pinned PyTorch wheels do not support
Intel macOS or Windows ARM64, so these are not advertised as native installation
targets. Apple MPS and AMD/Intel GPU acceleration are not enabled by this installer.
CPU mode keeps both recognition paths but is slower; live scanning performance
depends on the computer. Training remains an independent NVIDIA CUDA workflow.

## Common commands

Use Python 3.11 or 3.12. On Windows, `py -3.12` can replace `python`; on Linux/macOS,
`python3` can replace it. Run these from the repository root:

```text
python scanner.py setup
python scanner.py models
python scanner.py sync
python scanner.py doctor
python scanner.py server
```

Setup defaults to `--device auto`. `--device cpu` works without an NVIDIA GPU;
`--device cuda` requires a supported NVIDIA driver on Linux x64 or Windows x64.
CUDA PyTorch uses the CUDA 13.0 wheel index and Paddle uses CUDA 12.6 in its own
environment. The selected packages include runtime libraries; the GPU driver
must already be installed. `nvidia-smi` should work before choosing CUDA.

Windows uses `.venv/Scripts/python.exe` and `.venvs/<worker>/Scripts/python.exe`.
Linux/macOS use `bin/python`. The installer writes the correct paths into ignored
`desktop/config.local.toml`. Existing local configuration is never overwritten.
Worker communication uses bounded background I/O threads, so it does not require
Unix-only pipe polling. CPU workers receive a longer inference timeout.

`python scanner.py pip ...` installs into the server environment.
`python scanner.py python ...` runs a Python script or module in that environment.
The existing Bash launchers remain available on Linux/WSL/macOS.

## OS prerequisites

- **Windows:** use 64-bit Python and allow the server through Windows Firewall
  on your private network. Some binary packages require the Microsoft Visual C++
  Redistributable. WSL is not needed for native installation.
- **Linux:** install Python's `venv` support. Minimal Ubuntu installations also
  need `libgl1` and `libglib2.0-0` for OpenCV. Linux ARM64 uses CPU packages.
- **macOS:** use macOS 14+ and ARM64 Python on Apple silicon. Dependencies come
  from PyPI without CUDA libraries. Allow incoming local-network connections.

The Android app still targets Android 8.0+; it is not an iOS app. Build it with
JDK 17 and Android SDK 36 using `python scanner.py android`. On Windows this calls
`gradlew.bat`; Linux/macOS use the Unix wrapper. For an existing WSL setup using
Windows Android Studio, the older `mobile/build.sh` wrapper remains available.

## Validation

CI installs the full CPU pipeline, runs backend tests and starts both real model
workers on Windows x64, Linux x64/ARM64 and macOS ARM64. These checks validate
installation and inference; they do not measure real-time performance or test all
GPU/driver combinations. Local CUDA regression checks remain separate.

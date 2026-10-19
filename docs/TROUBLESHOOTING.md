# Troubleshooting

Start with:

```bash
python scanner.py doctor
```

The checker is read-only: it reports dependency, model-cache, catalog and CUDA
problems without downloading anything or changing your library. Startup performs
the final model and kernel validation. `/health` reflects the running server.

| Symptom | Check |
| --- | --- |
| No server Python environment | Run `python scanner.py setup --device cuda` or follow manual setup if `.venv` already exists |
| Offline model cache miss | Run `python scanner.py models` with internet access |
| No reference embeddings | Run `tcg_scanner sync`; choose `--set` for a small initial catalog |
| Model/preprocessing mismatch | Use the pinned dependency versions or regenerate references with `embed --existing --regenerate` |
| CUDA unavailable | Check `nvidia-smi` inside the same Linux/WSL environment; the selected wheels require a compatible NVIDIA driver |
| GPU FAISS unavailable | Install `requirements-gpu.txt`; do not mix it with `faiss-cpu` |
| Missing collector checkpoint or Git LFS pointer | Install Git LFS and run `git lfs pull` in the repository before setup |
| OCR degraded | Check both configured worker executables, the trained checkpoint, Paddle cache, and startup logs |
| Android build lacks detector | Run `scripts/prepare_models.py --android` before packaging |
| Phone cannot discover server | Enter the server's private IPv4 in settings and check firewall/network isolation |
| Chime/vibration absent | App toggles, system volume and phone Silent mode all affect scan feedback |

## Native Windows and macOS

WSL is not required for native Windows setup. Use `python scanner.py setup` and
the commands in [platform support](PLATFORMS.md). Windows CUDA installations use
CPU FAISS alongside GPU model inference. On macOS, use native ARM64 Python on
macOS 14+; the current dependency set does not support Intel Macs.

## WSL networking

Prefer WSL2 mirrored networking. The server must bind a private address reachable
from the phone. With NAT networking, WSL's address may not be reachable from other
LAN devices; do not assume the printed WSL interface is your Wi-Fi interface.
Keep the phone and computer on the same non-isolated network.

Allow TCP 8765 (or your selected server port) and UDP 5353 for mDNS only from the
intended private subnet. The optional Windows administrator helper
`desktop/tools/allow-local-server.ps1 -DesktopIp <ip> -Subnet <cidr>` applies the
WSL Hyper-V rules for the default port. It is not run by setup; inspect it before
using it. Existing Windows firewall/network policy may need separate configuration.
Training, builds and inference otherwise use Bash/Python entry points.

## Diagnostics

Use `python scanner.py server --log-level debug --access-log` only while investigating
an issue. Redact private paths, network details and scan content from public logs.
`desktop/tools/test_discovery.py <private-ip>` checks mDNS and health. Keep optional
reports under `artifacts/`, which is ignored by Git and excluded from source bundles.

"""Install a Linux/WSL checkout; model downloads and catalog building are explicit steps."""
import argparse
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def command(*args):
    print('+ ' + ' '.join(map(str, args)), flush=True)
    subprocess.run(list(map(str, args)), check=True)


def environment(directory):
    python = directory / 'bin/python'
    if not python.exists():
        command(sys.executable, '-m', 'venv', directory)
    command(python, '-m', 'pip', 'install', '--upgrade', 'pip')
    return python


def configuration(device, weights=None):
    values = tomllib.loads((ROOT / 'desktop/config.toml').read_text())
    values.update(device=device, faiss_device=device, ocr_device='gpu:0' if device == 'cuda' else 'cpu')
    if weights:
        values.update(ocr='paddle', collector_weights=str(weights.resolve()))
    return values


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--device', choices=('cpu', 'cuda'), required=True)
    parser.add_argument('--collector-weights', type=Path, help='Existing trusted RF-DETR Small collector-number checkpoint; no training')
    parser.add_argument('--dry-run', action='store_true', help='Show the install plan without downloading or changing files')
    args = parser.parse_args(argv)
    if sys.version_info[:2] not in ((3, 11), (3, 12)):
        parser.error('Use Python 3.11 or 3.12 for the pinned inference dependencies.')
    if platform.system() != 'Linux' or platform.machine() not in ('x86_64', 'AMD64'):
        parser.error('This installer supports Linux x86_64, including WSL2.')
    local = ROOT / 'desktop/config.local.toml'
    if local.exists():
        parser.error(f'{local} already exists. It was preserved. Use the dependency commands in desktop/README.md to update an existing installation.')
    weights = args.collector_weights.expanduser() if args.collector_weights else None
    if weights and not weights.is_file():
        parser.error(f'Collector checkpoint not found: {weights}')
    if args.device == 'cuda' and not args.dry_run:
        if not shutil.which('nvidia-smi'):
            parser.error('nvidia-smi is unavailable. Install a working NVIDIA driver first, or choose --device cpu.')
        command('nvidia-smi', '--query-gpu=name,driver_version', '--format=csv,noheader')
    values = configuration(args.device, weights)
    if args.dry_run:
        print(json.dumps({'device': args.device, 'server_env': str(ROOT / '.venv'),
                          'ocr': values['ocr'], 'config': str(local),
                          'next': ['prepare models', 'build catalog', 'start server']}, indent=2))
        return 0
    # Never replace a differently configured environment accidentally.
    if (ROOT / '.venv').exists() or (ROOT / '.venvs').exists():
        parser.error('A Python environment already exists. Use the manual setup instructions; existing environments were preserved.')
    req = ROOT / 'desktop/requirements'
    python = environment(ROOT / '.venv')
    torch_index = 'https://download.pytorch.org/whl/' + ('cu130' if args.device == 'cuda' else 'cpu')
    command(python, '-m', 'pip', 'install', 'torch==2.14.1', 'torchvision==0.29.1', '--index-url', torch_index)
    command(python, '-m', 'pip', 'install', '-r', req / ('requirements-gpu.txt' if args.device == 'cuda' else 'requirements.txt'),
            '-r', req / 'requirements-yolo.txt', '-r', req / 'requirements-server.txt')
    if weights:
        collector = environment(ROOT / '.venvs/collector')
        command(collector, '-m', 'pip', 'install', 'torch==2.14.1', 'torchvision==0.29.1', '--index-url', torch_index)
        command(collector, '-m', 'pip', 'install', '-r', req / 'requirements-collector.txt')
        paddle = environment(ROOT / '.venvs/paddle')
        command(paddle, '-m', 'pip', 'install', 'paddlepaddle-gpu==3.2.0' if args.device == 'cuda' else 'paddlepaddle==3.2.0',
                '--index-url', 'https://www.paddlepaddle.org.cn/packages/stable/' + ('cu126/' if args.device == 'cuda' else 'cpu/'))
        command(paddle, '-m', 'pip', 'install', '-r', req / 'requirements-paddle-rec.txt')
    # Simple flat TOML: JSON strings/numbers are also valid TOML values here.
    with local.open('x') as output:
        output.write('# Local installation settings; do not commit.\n')
        for key, value in values.items():
            output.write(f'{key} = {json.dumps(value)}\n')
    print('Installed. Next: .venv/bin/python scripts/prepare_models.py')
    print('Then build references: (cd desktop && ../.venv/bin/python -m tcg_scanner sync)')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f'Setup failed: {exc}\nExisting files were retained. See desktop/README.md for manual recovery.', file=sys.stderr)
        raise SystemExit(1)

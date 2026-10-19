"""Install the scanner on Windows, Linux or macOS with compatible compute backends."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'desktop'))
from tcg_scanner.platforms import runtime_plan, venv_python


def command(*args):
    print('+ ' + ' '.join(map(str, args)), flush=True)
    subprocess.run(list(map(str, args)), check=True)


def environment(directory):
    python = venv_python(directory)
    if not python.exists():
        command(sys.executable, '-m', 'venv', directory)
    command(python, '-m', 'pip', 'install', '--upgrade', 'pip')
    return python


def configuration(device, weights=None, visual_only=False, plan=None):
    plan = plan or runtime_plan(device)
    values = tomllib.loads((ROOT / 'desktop/config.toml').read_text())
    values.update({key: plan[key] for key in ('device', 'faiss_device', 'ocr_device')})
    values.update(ocr_python=str(venv_python(ROOT / '.venvs/paddle', plan['system'])),
                  collector_python=str(venv_python(ROOT / '.venvs/collector', plan['system'])))
    weights = weights or ROOT / 'models/collector-number/rfdetr-small.pth'
    values.update(ocr='none' if visual_only else 'paddle', collector_weights=str(weights.resolve()))
    return values


def validate_checkpoint(path, bundled=False):
    if not path.is_file():
        raise ValueError(f'Collector checkpoint not found: {path}. Run git lfs install && git lfs pull from the repository, or supply --collector-weights PATH.')
    with path.open('rb') as stream:
        if stream.read(128).startswith(b'version https://git-lfs.github.com/spec/v1'):
            raise ValueError(f'{path} is a Git LFS pointer, not model weights. Run git lfs install && git lfs pull.')
    if bundled:
        metadata = json.loads(path.with_suffix('.json').read_text())
        with path.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        if digest != metadata['sha256']:
            raise ValueError('Bundled collector checkpoint checksum mismatch. Restore it with git lfs pull before setup.')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--device', choices=('auto', 'cpu', 'cuda'), default='auto')
    models = parser.add_mutually_exclusive_group()
    models.add_argument('--collector-weights', type=Path, help='Override the included RF-DETR Small collector-number checkpoint; no training')
    models.add_argument('--visual-only', action='store_true', help='Explicitly disable collector-number recognition and install visual matching only')
    parser.add_argument('--dry-run', action='store_true', help='Show the install plan without downloading or changing files')
    args = parser.parse_args(argv)
    if sys.version_info[:2] not in ((3, 11), (3, 12)):
        parser.error('Use Python 3.11 or 3.12 for the pinned inference dependencies.')
    try:
        plan = runtime_plan(args.device)
    except ValueError as exc:
        parser.error(str(exc))
    local = ROOT / 'desktop/config.local.toml'
    if local.exists():
        parser.error(f'{local} already exists. It was preserved. Use the dependency commands in README.md#advanced-setup-and-recovery to update an existing installation.')
    weights = None if args.visual_only else (args.collector_weights.expanduser() if args.collector_weights
                                            else ROOT / 'models/collector-number/rfdetr-small.pth')
    if weights:
        try:
            validate_checkpoint(weights, bundled=args.collector_weights is None)
        except (ValueError, OSError, KeyError) as exc:
            parser.error(str(exc))
    if plan['device'] == 'cuda' and not args.dry_run:
        if not shutil.which('nvidia-smi'):
            parser.error('nvidia-smi is unavailable. Install a working NVIDIA driver first, or choose --device cpu.')
        command('nvidia-smi', '--query-gpu=name,driver_version', '--format=csv,noheader')
    values = configuration(plan['device'], weights, visual_only=args.visual_only, plan=plan)
    if args.dry_run:
        print(json.dumps({**plan, 'server_env': str(ROOT / '.venv'),
                          'ocr': values['ocr'], 'config': str(local),
                          'next': ['prepare models', 'build catalog', 'start server']}, indent=2))
        return 0
    # Never replace a differently configured environment accidentally.
    if (ROOT / '.venv').exists() or (ROOT / '.venvs').exists():
        parser.error('A Python environment already exists. Use the manual setup instructions; existing environments were preserved.')
    req = ROOT / 'desktop/requirements'
    python = environment(ROOT / '.venv')
    torch_args = ['torch==2.14.1', 'torchvision==0.29.1']
    if plan['torch_index']:
        torch_args += ['--index-url', plan['torch_index']]
    command(python, '-m', 'pip', 'install', *torch_args)
    command(python, '-m', 'pip', 'install', '-r', req / ('requirements-gpu.txt' if plan['faiss_device'] == 'cuda' else 'requirements.txt'),
            '-r', req / 'requirements-yolo.txt', '-r', req / 'requirements-server.txt')
    if plan['system'] == 'Darwin':
        command(python, ROOT / 'scripts/fix_macos_openmp.py')
    if weights:
        collector = environment(ROOT / '.venvs/collector')
        command(collector, '-m', 'pip', 'install', *torch_args)
        command(collector, '-m', 'pip', 'install', '-r', req / 'requirements-collector.txt')
        paddle = environment(ROOT / '.venvs/paddle')
        paddle_args = ['paddlepaddle-gpu==3.2.0', '--index-url', plan['paddle_index']] if plan['paddle_index'] else ['paddlepaddle==3.2.2']
        command(paddle, '-m', 'pip', 'install', *paddle_args)
        command(paddle, '-m', 'pip', 'install', '-r', req / 'requirements-paddle-rec.txt')
    # Simple flat TOML: JSON strings/numbers are also valid TOML values here.
    with local.open('x') as output:
        output.write('# Local installation settings; do not commit.\n')
        for key, value in values.items():
            output.write(f'{key} = {json.dumps(value)}\n')
    print('Installed. Next: python scanner.py models')
    print('Then: python scanner.py sync && python scanner.py server')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f'Setup failed: {exc}\nExisting files were retained. See README.md#advanced-setup-and-recovery for manual recovery.', file=sys.stderr)
        raise SystemExit(1)

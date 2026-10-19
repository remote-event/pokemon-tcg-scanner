"""Portable entry point for setup, model preparation, scanning and Android builds."""
import argparse
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'desktop'))
from tcg_scanner.platforms import venv_python


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('setup', 'models', 'sync', 'status', 'doctor', 'server', 'android', 'cli', 'pip', 'python'))
    parser.add_argument('arguments', nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    python = venv_python(ROOT / '.venv')
    if args.command in {'setup', 'android'}:
        script = ROOT / ('scripts/setup.py' if args.command == 'setup' else 'mobile/build.py')
        cmd = [sys.executable, str(script)]
    else:
        python = Path(os.environ.get('TCG_PYTHON', python))
        if not python.is_file():
            parser.error('Server environment missing. Run: python scanner.py setup')
        commands = {'models': [str(ROOT / 'scripts/prepare_models.py')],
                    'sync': ['-m', 'tcg_scanner', 'sync'], 'status': ['-m', 'tcg_scanner', 'status'],
                    'doctor': ['-m', 'tcg_scanner.doctor'], 'server': ['-m', 'tcg_scanner.server'],
                    'cli': ['-m', 'tcg_scanner'], 'pip': ['-m', 'pip'], 'python': []}
        cmd = [str(python), '-B', *commands[args.command]]
    env = os.environ.copy()
    env['PYTHONPATH'] = str(ROOT / 'desktop') + (os.pathsep + env['PYTHONPATH'] if env.get('PYTHONPATH') else '')
    env.setdefault('PYTHONUTF8', '1')
    process = subprocess.Popen([*cmd, *args.arguments], cwd=ROOT, env=env)
    try:
        return process.wait()
    except KeyboardInterrupt:
        # Ctrl+C reaches the child in the same terminal process group as well.
        try:
            return process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                return process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
                return 130


if __name__ == '__main__':
    raise SystemExit(main())

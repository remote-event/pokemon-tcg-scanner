"""Make the macOS FAISS wheel reuse PyTorch's OpenMP runtime in this venv."""
import importlib.util
import os
from pathlib import Path
import sys


def main():
    if sys.platform != 'darwin':
        return
    if sys.prefix == sys.base_prefix:
        raise RuntimeError('Run this only inside the scanner virtual environment')
    # Locate packages without loading either native runtime into this process.
    torch = Path(importlib.util.find_spec('torch').origin).parent
    faiss = Path(importlib.util.find_spec('faiss').origin).parent
    if any(not package.resolve().is_relative_to(Path(sys.prefix).resolve()) for package in (torch, faiss)):
        raise RuntimeError('Both packages must be installed inside this virtual environment')
    shared = torch / 'lib/libomp.dylib'
    bundled = faiss / '.dylibs/libomp.dylib'
    if not shared.is_file() or not bundled.is_file():
        raise RuntimeError('Unexpected PyTorch/FAISS wheel layout; cannot share OpenMP safely')
    if bundled.resolve() == shared.resolve():
        return
    backup = bundled.with_suffix('.dylib.original')
    if backup.exists():
        raise RuntimeError(f'OpenMP backup already exists: {backup}; inspect the environment before repair')
    bundled.rename(backup)
    try:
        bundled.symlink_to(os.path.relpath(shared, bundled.parent))
    except BaseException:
        backup.rename(bundled)
        raise
    print('FAISS and PyTorch now share one OpenMP runtime.')


if __name__ == '__main__':
    main()

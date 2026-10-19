"""Read-only installation checks. Run before starting the offline inference server."""
import argparse
from contextlib import closing
import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess

from .config import Config, default_config_path


def check(config):
    checks = []

    def record(name, ok, detail):
        checks.append(dict(check=name, ok=bool(ok), detail=str(detail)))

    for package in ('torch', 'torchvision', 'transformers', 'faiss', 'ultralytics', 'fastapi', 'uvicorn', 'websockets', 'zeroconf'):
        record(package, importlib.util.find_spec(package) is not None, 'Python dependency')
    try:
        import torch
        needs_cuda = config.device == 'cuda' or config.faiss_device == 'cuda'
        record('CUDA', not needs_cuda or torch.cuda.is_available(),
               torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU only')
        from .vectors import faiss_cuda_available
        record('FAISS GPU', config.faiss_device != 'cuda' or faiss_cuda_available(), config.faiss_device)
    except (ImportError, OSError, RuntimeError) as exc:
        record('compute runtime', False, exc)
    database = config.root / 'metadata.sqlite3'
    if database.is_file():
        try:
            with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as db:
                count = db.execute("SELECT COUNT(*) FROM embeddings WHERE status='ready'").fetchone()[0]
            record('reference catalog', count > 0, f'{count} ready embeddings')
        except sqlite3.Error as exc:
            record('reference catalog', False, exc)
    else:
        record('reference catalog', False, 'Run python -m tcg_scanner sync')
    try:
        from huggingface_hub import hf_hub_download
        from .detection import MODEL_REPO, MODEL_FILE, MODEL_REVISION
        for repo, revision, files in ((config.model_name, config.model_revision,
                                      ('config.json', 'preprocessor_config.json', 'model.safetensors')),
                                     (MODEL_REPO, MODEL_REVISION, (MODEL_FILE,))):
            for file in files:
                try:
                    hf_hub_download(repo, file, revision=revision, local_files_only=True)
                    record(f'cached {repo}/{file}', True, revision)
                except (OSError, ValueError) as exc:
                    record(f'cached {repo}/{file}', False, 'Run scripts/prepare_models.py')
    except ImportError as exc:
        record('model cache', False, exc)
    if config.ocr == 'paddle':
        for key, packages in (('collector_python', ('torch', 'rfdetr')), ('ocr_python', ('paddle', 'paddleocr'))):
            path = Path(getattr(config, key)).expanduser()
            try:
                result = subprocess.run([str(path), '-I', '-c',
                    'import importlib.util,json,sys; print(json.dumps({p:importlib.util.find_spec(p) is not None for p in sys.argv[1:]}))',
                    *packages], capture_output=True, text=True, timeout=10, check=True)
                available = json.loads(result.stdout)
                record(key, all(available.values()), f'{path}: {available}')
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                record(key, False, exc)
        weights = Path(config.collector_weights).expanduser()
        if not weights.is_absolute():
            weights = config.root / weights
        record('collector weights', weights.is_file(), weights)
        recognition = Path('~/.paddlex/official_models').expanduser() / config.ocr_model / 'inference.json'
        record('recognition model', recognition.is_file(), recognition)
    return dict(ready=all(item['ok'] for item in checks), ocr=config.ocr, checks=checks)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path)
    args = parser.parse_args(argv)
    try:
        result = check(Config.load(args.config or default_config_path(), {}))
    except (OSError, ValueError) as exc:
        result = dict(ready=False, error=str(exc))
    print(json.dumps(result, indent=2))
    return 0 if result['ready'] else 1


if __name__ == '__main__':
    raise SystemExit(main())

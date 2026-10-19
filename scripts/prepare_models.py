"""Download/cache inference models explicitly before offline server startup."""
import argparse
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'desktop'))
from tcg_scanner.config import Config, default_config_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path)
    parser.add_argument('--android', action='store_true', help='Also export the phone detector (requires requirements-export.txt)')
    args = parser.parse_args()
    config = Config.load(args.config or default_config_path(), {})
    if any(os.getenv(key) == '1' for key in ('HF_HUB_OFFLINE', 'TRANSFORMERS_OFFLINE', 'TCG_OFFLINE')):
        parser.error('Model preparation needs network access. Unset offline environment overrides for this command.')
    from tcg_scanner.embeddings import DinoEmbedder
    from tcg_scanner.detection import CardCropper, MODEL_REPO, MODEL_FILE, MODEL_REVISION
    print('Preparing DINOv2 and the card detector…', flush=True)
    embedder = DinoEmbedder(config)
    CardCropper(config, embedder.device)
    if config.ocr == 'paddle':
        weights = Path(config.collector_weights).expanduser()
        if not weights.is_absolute():
            weights = config.root / weights
        if not weights.is_file():
            parser.error(f'Collector-number weights are missing: {weights}')
        # Recognition only: no text detection or document pipeline is downloaded.
        subprocess.run([config.ocr_python, '-c',
                        'import sys; from paddleocr import TextRecognition; TextRecognition(model_name=sys.argv[1], device=sys.argv[2])',
                        config.ocr_model, config.ocr_device], check=True)
    if args.android:
        from huggingface_hub import hf_hub_download
        weights = hf_hub_download(MODEL_REPO, MODEL_FILE, revision=MODEL_REVISION)
        subprocess.run([sys.executable, str(ROOT / 'desktop/tools/export_phone_detector.py'), weights], check=True)
    print('Models cached. Build your reference catalog with tcg_scanner sync before starting the server.')


if __name__ == '__main__':
    main()

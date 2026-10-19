"""Opt-in real-model check of both isolated inference workers; no collection writes."""
import argparse
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tcg_scanner.collector import CollectorReader
from tcg_scanner.config import Config, default_config_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path)
    parser.add_argument('--download', action='store_true', help='Cache the recognition model before testing')
    args = parser.parse_args()
    config = Config.load(args.config or default_config_path(), {})
    if args.download:
        subprocess.run([config.ocr_python, '-c',
                        'import sys; from paddleocr import TextRecognition; TextRecognition(model_name=sys.argv[1], device=sys.argv[2])',
                        config.ocr_model, config.ocr_device], check=True)
    os.environ['TCG_OFFLINE'] = '1'
    from PIL import Image, ImageDraw
    with Image.new('RGB', (256, 48), 'white') as image, BytesIO() as output:
        ImageDraw.Draw(image).text((8, 10), '123/456', fill='black', font_size=24)
        image.save(output, format='PNG')
        crop = output.getvalue()
    reader = CollectorReader(config)
    try:
        ready = reader.start()
        detection = reader.detector.recognize(crop)
        recognition = reader.recognizer.recognize(crop)
        if 'boxes' not in detection or 'lines' not in recognition:
            raise RuntimeError('Inference worker returned an unexpected response')
        if not any(line['text'] == '123/456' and line['score'] >= 0.8 for line in recognition['lines']):
            raise RuntimeError(f'Recognition check failed: {recognition["lines"]}')
        print(json.dumps({'ready': ready, 'recognition': recognition['lines'],
                          'detection_ms': detection['detection_ms']}))
    finally:
        reader.close()


if __name__ == '__main__':
    main()

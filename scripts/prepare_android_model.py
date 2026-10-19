"""Download, export and validate the pinned phone detector without server setup."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'desktop'))
from tcg_scanner.detection import MODEL_FILE, MODEL_REPO, MODEL_REVISION


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-output', type=Path, help='Also copy the original checkpoint for source distribution')
    args = parser.parse_args()
    from huggingface_hub import hf_hub_download
    import numpy as np
    import onnxruntime as ort
    assets = ROOT / 'mobile/android/app/src/main/assets'
    spec_path = assets / 'card_pose_yolov8n.json'
    expected = json.loads(spec_path.read_text())
    checkpoint = Path(hf_hub_download(MODEL_REPO, MODEL_FILE, revision=MODEL_REVISION))
    if hashlib.sha256(checkpoint.read_bytes()).hexdigest() != expected['source_sha256']:
        raise RuntimeError('Phone detector checkpoint checksum mismatch')
    subprocess.run([sys.executable, str(ROOT / 'desktop/tools/export_phone_detector.py'), str(checkpoint)], check=True)
    spec = json.loads(spec_path.read_text())
    spec['revision'] = MODEL_REVISION
    spec_path.write_text(json.dumps(spec, indent=2) + '\n')
    session = ort.InferenceSession(str(assets / 'card_pose_yolov8n.onnx'), providers=['CPUExecutionProvider'])
    model_input = session.get_inputs()[0]
    if model_input.shape != [1, 3, 480, 480]:
        raise RuntimeError(f'Unexpected detector input: {model_input.shape}')
    output = session.run(None, {model_input.name: np.zeros(model_input.shape, dtype=np.float32)})[0]
    if output.shape != (1, 17, 4725) or not np.isfinite(output).all():
        raise RuntimeError(f'Unexpected detector output: {output.shape}')
    if args.source_output:
        args.source_output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(checkpoint, args.source_output)
    print('Phone detector exported and verified with ONNX Runtime.')


if __name__ == '__main__':
    main()

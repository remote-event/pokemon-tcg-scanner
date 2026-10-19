"""Export the existing card-corner YOLO checkpoint to Android ONNX; no training."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('checkpoint', type=Path)
    args = parser.parse_args()
    from ultralytics import YOLO
    root = Path(__file__).resolve().parents[2]
    assets = root / 'mobile/android/app/src/main/assets'
    assets.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='tcg-phone-export-') as temporary:
        source = Path(temporary) / 'card_pose_yolov8n.pt'
        shutil.copyfile(args.checkpoint, source)
        exported = YOLO(str(source)).export(format='onnx', imgsz=480, opset=17,
                                           simplify=False, dynamic=False, device='cpu')
        target = assets / 'card_pose_yolov8n.onnx'
        shutil.copyfile(exported, target)
    spec = dict(source='duclvQ/tcg-card-detector', checkpoint=args.checkpoint.name,
                source_sha256=hashlib.sha256(args.checkpoint.read_bytes()).hexdigest(),
                onnx_sha256=hashlib.sha256(target.read_bytes()).hexdigest(),
                input=[1,3,480,480], color='RGB / 255', letterbox=114, opset=17,
                corners='top-left, top-right, bottom-right, bottom-left', crop=[630,880], border=8)
    (assets / 'card_pose_yolov8n.json').write_text(json.dumps(spec, indent=2)+'\n')
    print(target)


if __name__ == '__main__':
    main()

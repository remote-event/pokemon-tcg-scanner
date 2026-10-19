"""Build the Android client using the native Gradle wrapper on any supported desktop."""
import os
from pathlib import Path
import re
import subprocess
import sys


def main(argv=None):
    root = Path(__file__).resolve().parent / 'android'
    if not (root / 'app/src/main/assets/card_pose_yolov8n.onnx').is_file():
        raise SystemExit('Phone detector missing. Run python scanner.py models --android first.')
    tasks = argv or ['testDebugUnitTest', 'assembleDebug']
    if os.name == 'nt':
        # cmd.exe is required for .bat files. Only Gradle task names/options enter its command string.
        if any(not re.fullmatch(r'[a-zA-Z0-9_:=.\-]+', task) for task in tasks):
            raise SystemExit('Invalid Gradle argument')
        cmd = ['cmd.exe', '/d', '/c', 'gradlew.bat', *tasks]
    else:
        cmd = ['bash', './gradlew', *tasks]
    return subprocess.call(cmd, cwd=root)


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))

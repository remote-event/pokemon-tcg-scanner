"""Verify the signed APK's detector and assemble its public notices/source links."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def validate_apk(path):
    with zipfile.ZipFile(path) as archive:
        spec = json.loads(archive.read('assets/card_pose_yolov8n.json'))
        model = archive.read('assets/card_pose_yolov8n.onnx')
        if not model or hashlib.sha256(model).hexdigest() != spec['onnx_sha256']:
            raise ValueError('APK detector is missing or does not match its metadata')
        for notice in ('MODEL_NOTICES.txt', 'licenses/AGPL-3.0.txt', 'licenses/MIT-ONNX-Runtime.txt',
                       'licenses/Apache-2.0.txt', 'licenses/MIT-project.txt'):
            if not archive.read('assets/' + notice).strip():
                raise ValueError(f'Empty APK notice: {notice}')
    return spec


def package_release(dist, spec):
    """Use an explicit file list so no signing keys or build files can be published."""
    checkpoint = dist / 'source-model/card_pose_yolov8n.pt'
    if hashlib.sha256(checkpoint.read_bytes()).hexdigest() != spec['source_sha256']:
        raise ValueError('Source checkpoint does not match APK metadata')
    release = dist / 'release'
    release.mkdir(exist_ok=True)
    source_files = ['pokemon-tcg-scanner-source.zip', 'SOURCES.md', 'MODEL_NOTICES.txt',
                    'THIRD_PARTY_NOTICES.md', 'source-model/card_pose_yolov8n.pt',
                    'licenses/AGPL-3.0.txt', 'licenses/Apache-2.0.txt',
                    'licenses/MIT-ONNX-Runtime.txt', 'licenses/MIT-project.txt']
    with zipfile.ZipFile(release / 'pokemon-tcg-scanner-sources.zip', 'w',
                         compression=zipfile.ZIP_DEFLATED) as archive:
        for name in source_files:
            archive.write(dist / name, name)
    for name in ('pokemon-tcg-scanner.apk', 'signing-certificate.txt'):
        shutil.copyfile(dist / name, release / name)
    checksums = []
    for name in ('pokemon-tcg-scanner.apk', 'pokemon-tcg-scanner-sources.zip', 'signing-certificate.txt'):
        with (release / name).open('rb') as stream:
            checksums.append(f'{hashlib.file_digest(stream, "sha256").hexdigest()}  {name}\n')
    (release / 'SHA256SUMS').write_text(''.join(checksums), encoding='utf-8')


def main():
    dist = ROOT / 'dist'
    spec = validate_apk(dist / 'pokemon-tcg-scanner.apk')
    source_url = os.environ['SOURCE_URL']
    assets = ROOT / 'mobile/android/app/src/main/assets'
    shutil.copytree(assets / 'licenses', dist / 'licenses', dirs_exist_ok=True)
    shutil.copyfile(assets / 'MODEL_NOTICES.txt', dist / 'MODEL_NOTICES.txt')
    shutil.copyfile(ROOT / 'THIRD_PARTY_NOTICES.md', dist / 'THIRD_PARTY_NOTICES.md')
    (dist / 'SOURCES.md').write_text(f'''# APK and corresponding source

Install `pokemon-tcg-scanner.apk` on Android 8.0 or later. The card detector is
already included. The computer still needs the backend described in the README.

App source for this build: {source_url}
An accompanying source ZIP includes the Android code and build/export scripts.

The original, unmodified phone checkpoint is in `source-model/card_pose_yolov8n.pt`.
Upstream model: https://huggingface.co/{spec['source']}/tree/{spec['revision']}
Checkpoint SHA-256: `{spec['source_sha256']}`
Exported ONNX SHA-256: `{spec['onnx_sha256']}`
Export implementation: https://github.com/ultralytics/ultralytics/tree/v8.4.178
Rebuild instructions: `mobile/README.md` and `.github/workflows/android-apk.yml`.

The packaged detector is an ONNX conversion of the upstream weights, without
retraining. Its AGPL-3.0 terms remain applicable; see `licenses/AGPL-3.0.txt` and
the third-party notices. Original project source retains its MIT license.
No warranty is provided. Preserve the license and source information when sharing.

The APK uses the maintainer's persistent release key. Subsequent workflow builds
can update it. A locally built debug APK uses a different key and cannot be
updated by this APK in place; do not uninstall an existing app without preserving
any device-only data. This download does not modify existing installations.
''', encoding='utf-8')
    package_release(dist, spec)
    print('APK contains the verified phone model and license notices.')


if __name__ == '__main__':
    main()

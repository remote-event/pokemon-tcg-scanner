"""A downloadable APK must contain its detector, matching hash and notices."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

spec = importlib.util.spec_from_file_location('android_distribution', Path(__file__).resolve().parents[2] / 'scripts/package_android_distribution.py')
distribution = importlib.util.module_from_spec(spec)
spec.loader.exec_module(distribution)


class AndroidDistributionTests(unittest.TestCase):
    def test_missing_corrupt_model_or_notice_prevents_distribution(self):
        body = b'test model bytes'
        entries = {
            'assets/card_pose_yolov8n.onnx': body,
            'assets/card_pose_yolov8n.json': json.dumps({'onnx_sha256': hashlib.sha256(body).hexdigest()}),
        }
        for name in ('MODEL_NOTICES.txt', 'licenses/AGPL-3.0.txt', 'licenses/MIT-ONNX-Runtime.txt',
                     'licenses/Apache-2.0.txt', 'licenses/MIT-project.txt'):
            entries['assets/' + name] = 'Test notice'
        for change in ('valid', 'missing model', 'wrong hash', 'missing notice'):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as directory:
                contents = entries.copy()
                if change == 'missing model':
                    del contents['assets/card_pose_yolov8n.onnx']
                elif change == 'wrong hash':
                    contents['assets/card_pose_yolov8n.onnx'] = b'changed'
                elif change == 'missing notice':
                    del contents['assets/licenses/AGPL-3.0.txt']
                path = Path(directory) / 'app.apk'
                with zipfile.ZipFile(path, 'w') as archive:
                    for name, content in contents.items():
                        archive.writestr(name, content)
                if change == 'valid':
                    self.assertEqual(distribution.validate_apk(path)['onnx_sha256'], hashlib.sha256(body).hexdigest())
                else:
                    with self.assertRaises((KeyError, ValueError)):
                        distribution.validate_apk(path)

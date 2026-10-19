"""Portable configuration and release boundaries; no downloads or GPU required."""
from contextlib import redirect_stdout, redirect_stderr
import importlib.util
import hashlib
from io import StringIO
import os
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tcg_scanner.config import Config, default_config_path

ROOT = Path(__file__).resolve().parents[2]


def script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class InstallationTests(unittest.TestCase):
    def test_worker_paths_follow_config_not_current_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / 'config.toml'
            config.write_text('data_dir="data"\nocr_python="env/bin/python"\ncollector_python="detector/bin/python"\n')
            with patch.dict(os.environ, {}, clear=True):
                result = Config.load(config, {})
            self.assertEqual(result.root, Path(folder) / 'data')
            self.assertEqual(result.ocr_python, str(Path(folder) / 'env/bin/python'))
            self.assertEqual(result.collector_python, str(Path(folder) / 'detector/bin/python'))

    def test_explicit_environment_config_is_selected(self):
        with patch.dict(os.environ, {'TCG_CONFIG': '/tmp/custom-scanner.toml'}):
            self.assertEqual(default_config_path(), Path('/tmp/custom-scanner.toml'))

    def test_virtual_environment_python_symlink_is_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            python = root / 'worker/bin/python'
            python.parent.mkdir(parents=True)
            python.symlink_to('/usr/bin/python3')
            config = root / 'config.toml'
            config.write_text('ocr_python="worker/bin/python"\ncollector_python="worker/bin/python"\n')
            with patch.dict(os.environ, {}, clear=True):
                result = Config.load(config, {})
            self.assertEqual(result.ocr_python, str(python))
            self.assertEqual(result.collector_python, str(python))

    def test_install_plan_defaults_to_included_checkpoint_on_cpu_and_cuda(self):
        setup = script('setup')
        cpu = setup.configuration('cpu')
        self.assertEqual((cpu['device'], cpu['faiss_device'], cpu['ocr']), ('cpu', 'cpu', 'paddle'))
        self.assertEqual(cpu['collector_weights'], str(ROOT / 'models/collector-number/rfdetr-small.pth'))
        gpu = setup.configuration('cuda', Path('/tmp/number-model.pth'))
        self.assertEqual((gpu['ocr'], gpu['ocr_device']), ('paddle', 'gpu:0'))
        self.assertEqual(gpu['collector_weights'], '/tmp/number-model.pth')
        self.assertEqual(setup.configuration('cpu', visual_only=True)['ocr'], 'none')

    def test_checkpoint_validation_rejects_missing_lfs_pointers_and_corruption(self):
        setup = script('setup')
        with tempfile.TemporaryDirectory() as folder:
            weights = Path(folder) / 'rfdetr-small.pth'
            with self.assertRaisesRegex(ValueError, 'git lfs'):
                setup.validate_checkpoint(weights)
            weights.write_bytes(b'version https://git-lfs.github.com/spec/v1\noid sha256:abc\nsize 1024\n')
            with self.assertRaisesRegex(ValueError, 'Git LFS pointer'):
                setup.validate_checkpoint(weights)
            weights.write_bytes(b'checkpoint fixture')
            weights.with_suffix('.json').write_text(json.dumps({'sha256': hashlib.sha256(weights.read_bytes()).hexdigest()}))
            setup.validate_checkpoint(weights, bundled=True)
            weights.write_bytes(b'damaged checkpoint')
            with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                setup.validate_checkpoint(weights, bundled=True)

    def test_installer_preserves_existing_local_configuration(self):
        setup = script('setup')
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'desktop').mkdir()
            config = root / 'desktop/config.local.toml'
            config.write_text('device="cpu"\n')
            with patch.object(setup, 'ROOT', root), patch.object(setup, 'command') as command:
                with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
                    setup.main(['--device', 'cpu'])
                command.assert_not_called()
            self.assertEqual(config.read_text(), 'device="cpu"\n')

    def test_dry_run_does_not_create_environments_or_download(self):
        setup = script('setup')
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'desktop').mkdir()
            (root / 'desktop/config.toml').write_text((ROOT / 'desktop/config.toml').read_text())
            weights = root / 'models/collector-number/rfdetr-small.pth'
            weights.parent.mkdir(parents=True)
            weights.write_bytes(b'checkpoint fixture')
            weights.with_suffix('.json').write_text(json.dumps({'sha256': hashlib.sha256(weights.read_bytes()).hexdigest()}))
            with patch.object(setup, 'ROOT', root), patch.object(setup, 'command') as command, redirect_stdout(StringIO()):
                self.assertEqual(setup.main(['--device', 'cpu', '--dry-run']), 0)
                command.assert_not_called()
            self.assertFalse((root / '.venv').exists())
            self.assertFalse((root / 'desktop/config.local.toml').exists())

    def test_source_bundle_excludes_personal_data_weights_and_signing_material(self):
        pack = script('package_source')
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            allowed = ['README.md', 'LICENSE', '.gitattributes', 'models/collector-number/rfdetr-small.json',
                       'models/collector-number/SHA256SUMS', 'desktop/config.toml', 'desktop/tcg_scanner/server.py',
                       'mobile/android/gradle/wrapper/gradle-wrapper.jar']
            forbidden = ['data/scan-history/history.sqlite3', 'artifacts/log.txt', 'desktop/config.local.toml',
                         'mobile/android/local.properties', 'mobile/android/build/output.json',
                         'mobile/android/app/src/main/assets/detector.onnx', 'mobile/android/key.jks',
                         'mobile/android/app/src/androidTest/assets/card-a.jpg', '.env', 'desktop/.env.local']
            forbidden += ['models/collector-number/rfdetr-small.pth', 'training/collector_numbers/dataset/image.jpg']
            for name in allowed + forbidden:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'fixture')
            (root / 'desktop/linked.py').symlink_to(root / 'README.md')
            actual = {path.relative_to(root).as_posix() for path in pack.sources(root)}
            self.assertEqual(actual, set(allowed))


if __name__ == '__main__':
    unittest.main()

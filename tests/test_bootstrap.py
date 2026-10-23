"""Bootstrap trust boundaries using tiny local fixtures, with no network or VM."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('bootstrap', Path(__file__).resolve().parents[1] / 'scripts/bootstrap.py')
bootstrap = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bootstrap)


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir='/private/tmp', prefix='lvm-bootstrap-')
        self.root = Path(self.tmp.name).resolve()
        self.mac = patch.object(bootstrap.platform, 'system', return_value='Darwin'); self.mac.start()
        self.arm = patch.object(bootstrap.platform, 'machine', return_value='arm64'); self.arm.start()
        (self.root / 'tools/lima/bin').mkdir(parents=True)
        (self.root / 'tools/lima/bin/limactl').write_bytes(b'lima-fixture')
        (self.root / 'cache').mkdir(); (self.root / 'cache/debian.raw').write_bytes(b'image-fixture')
        self.settings = {'lima_version': 'pinned', 'lima_archive_sha256': 'archive', 'image_url': 'https://example.invalid/image',
                         'image_archive_sha512': 'archive', 'image_sha256': hashlib.sha256(b'image-fixture').hexdigest(),
                         'limactl_sha256': hashlib.sha256(b'lima-fixture').hexdigest(), 'max_concurrent': 1}
        (self.root / 'settings.example.json').write_text(json.dumps(self.settings))

    def tearDown(self):
        self.arm.stop(); self.mac.stop(); self.tmp.cleanup()

    def test_verified_cached_dependencies_create_local_settings_without_download(self):
        with patch.object(bootstrap, 'download') as download:
            report = bootstrap.bootstrap(self.root)
            download.assert_not_called()
        self.assertEqual(report['root'], str(self.root))
        self.assertEqual(json.loads((self.root / 'settings.json').read_text()), self.settings)
        self.assertFalse((self.root / 'runtime').exists())

    def test_changed_image_is_preserved_and_rejected(self):
        image = self.root / 'cache/debian.raw'; image.write_bytes(b'changed')
        with self.assertRaisesRegex(RuntimeError, 'Base image checksum'):
            bootstrap.bootstrap(self.root)
        self.assertEqual(image.read_bytes(), b'changed')
        self.assertFalse((self.root / 'settings.json').exists())

    def test_changed_local_dependency_pins_are_rejected_before_download(self):
        path = self.root / 'settings.json'; data = {**self.settings, 'image_url': 'https://example.invalid/different'}
        path.write_text(json.dumps(data)); original = path.read_bytes()
        with patch.object(bootstrap, 'download') as download, self.assertRaisesRegex(RuntimeError, 'pinned dependency'):
            bootstrap.bootstrap(self.root)
        download.assert_not_called(); self.assertEqual(path.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()

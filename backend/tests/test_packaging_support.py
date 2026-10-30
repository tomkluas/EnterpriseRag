import tempfile
from pathlib import Path
import unittest

from scripts.packaging_support import pin_windows_runtime


class PackagingRuntimeTests(unittest.TestCase):
    def test_replaces_unrelated_path_runtime_with_matching_system_set(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ['msvcp140.dll', 'vcruntime140.dll', 'vcruntime140_1.dll', 'msvcp140d.dll']:
                (root / name).write_bytes(b'test fixture')
            entries = [('MSVCP140.dll', 'unrelated-jdk/msvcp140.dll', 'BINARY'),
                       ('vcruntime140.dll', 'unrelated-app/vcruntime140.dll', 'BINARY'),
                       ('module.pyd', 'module.pyd', 'EXTENSION')]
            result = pin_windows_runtime(entries, root)
            runtimes = {name.lower(): source for name, source, kind in result if name.lower().endswith('.dll')}
            self.assertEqual(runtimes['msvcp140.dll'], str(root / 'msvcp140.dll'))
            self.assertEqual(runtimes['vcruntime140.dll'], str(root / 'vcruntime140.dll'))
            self.assertIn('vcruntime140_1.dll', runtimes)
            self.assertNotIn('msvcp140d.dll', runtimes)
            self.assertIn(('module.pyd', 'module.pyd', 'EXTENSION'), result)

    def test_missing_runtime_fails_build_instead_of_shipping_unrelated_dll(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError, 'Visual C\+\+'):
                pin_windows_runtime([], Path(directory))

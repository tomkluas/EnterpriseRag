import tempfile
import unittest
from app.v3.storage import OriginalStore


class StorageTests(unittest.TestCase):
    def test_original_is_immutable_and_keys_cannot_escape_store(self):
        with tempfile.TemporaryDirectory() as root:
            store=OriginalStore(root);store.put('tenant/version',b'original')
            with self.assertRaises(FileExistsError):store.put('tenant/version',b'changed')
            self.assertEqual(store.read('tenant/version'),b'original')
            for key in ('../outside','C:/outside','/outside','tenant\\outside'):
                with self.assertRaises(ValueError):store.read(key)

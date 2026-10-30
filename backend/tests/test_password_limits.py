import unittest

from app.core.security import hash_password, verify_password


class PasswordLimitTests(unittest.TestCase):
    def test_bcrypt_does_not_accept_a_truncated_long_password(self):
        hashed = hash_password("a" * 72)
        self.assertTrue(verify_password("a" * 72, hashed))
        self.assertFalse(verify_password("a" * 72 + "different", hashed))

    def test_hash_rejects_password_over_72_utf8_bytes(self):
        with self.assertRaises(ValueError):
            hash_password("中" * 25)

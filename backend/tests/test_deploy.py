"""Deployment helpers: which address is used for rate limits when the app runs behind a reverse proxy."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
_tmp = tempfile.TemporaryDirectory()
os.environ.setdefault("DATABASE_PATH", str(Path(_tmp.name) / "deploy-test.db"))

import server  # noqa: E402  (imported after the temporary database path is set)


class RealClientIp(unittest.TestCase):
    def test_proxy_not_trusted_ignores_the_header(self):
        self.assertEqual(server.real_client_ip("127.0.0.1", "203.0.113.9", trust_proxy=False), "127.0.0.1")

    def test_trusted_proxy_on_this_machine_uses_x_real_ip(self):
        self.assertEqual(server.real_client_ip("127.0.0.1", "203.0.113.9", trust_proxy=True), "203.0.113.9")
        self.assertEqual(server.real_client_ip("::1", " 2001:db8::5 ", trust_proxy=True), "2001:db8::5")

    def test_header_from_another_machine_is_never_believed(self):
        # someone reaching the port directly must not be able to pick their own address
        self.assertEqual(server.real_client_ip("198.51.100.7", "203.0.113.9", trust_proxy=True), "198.51.100.7")

    def test_garbage_or_missing_header_falls_back_to_the_connection_address(self):
        self.assertEqual(server.real_client_ip("127.0.0.1", "not-an-ip", trust_proxy=True), "127.0.0.1")
        self.assertEqual(server.real_client_ip("127.0.0.1", "", trust_proxy=True), "127.0.0.1")


if __name__ == "__main__":
    unittest.main()

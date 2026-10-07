"""Run all regression tests with credentials removed and network disabled."""
import os
from pathlib import Path
import socket
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def deny_network(*args, **kwargs):
    raise AssertionError("Offline validation attempted a network connection")


def main():
    with patch.dict(os.environ):
        for name in ("GOOGLE_SERVICE_ACCOUNT_JSON", "SEC_CONTACT_EMAIL", "GOOGLE_APPLICATION_CREDENTIALS", "GH_SIGNAL_TOKEN"):
            os.environ.pop(name, None)
        with patch.object(socket.socket, "connect", deny_network), \
                patch.object(socket.socket, "connect_ex", deny_network), \
                patch.object(socket, "create_connection", deny_network):
            suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"))
            result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    sys.exit(main())

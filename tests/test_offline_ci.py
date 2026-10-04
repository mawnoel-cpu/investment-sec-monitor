import os
from pathlib import Path
import socket
import unittest
from unittest.mock import patch

import run_offline

ROOT = Path(__file__).resolve().parents[1]


class OfflineValidationTests(unittest.TestCase):
    def test_runner_removes_credentials_and_blocks_network(self):
        class Probe(unittest.TestCase):
            def runTest(self):
                for name in ('GOOGLE_SERVICE_ACCOUNT_JSON', 'SEC_CONTACT_EMAIL', 'GOOGLE_APPLICATION_CREDENTIALS'):
                    self.assertNotIn(name, os.environ)
                with self.assertRaisesRegex(AssertionError, 'Offline validation'):
                    socket.create_connection(('example.invalid', 443))
                # Call the patched methods without allocating a real socket;
                # some workspaces disallow even socket construction.
                with self.assertRaisesRegex(AssertionError, 'Offline validation'):
                    socket.socket.connect(None, ('127.0.0.1', 1))
                with self.assertRaisesRegex(AssertionError, 'Offline validation'):
                    socket.socket.connect_ex(None, ('127.0.0.1', 1))
        with patch.dict(os.environ, {'GOOGLE_SERVICE_ACCOUNT_JSON': 'test-only', 'SEC_CONTACT_EMAIL': 'test-only'}), \
                patch.object(unittest.defaultTestLoader, 'discover', return_value=unittest.TestSuite([Probe()])):
            self.assertEqual(run_offline.main(), 0)
            self.assertEqual(os.environ['GOOGLE_SERVICE_ACCOUNT_JSON'], 'test-only')

    def test_pr_workflow_has_full_suite_and_no_secrets_or_writers(self):
        text = (ROOT / '.github/workflows/validate.yml').read_text()
        self.assertIn('  pull_request:', text)
        self.assertIn('"**.py"', text)
        self.assertIn('"requirements.txt"', text)
        self.assertIn('pip install -r requirements.txt', text)
        self.assertIn('python tests/run_offline.py', text)
        self.assertNotIn('secrets.', text)
        for module in ('ft_sec_pipeline.py', 'ft_macro_coordinator.py', 'ft_macro_pipeline.py', 'feed_validation.py'):
            self.assertIn(module, text)
            self.assertNotIn('run: python ' + module, text)

    def test_live_steps_are_gated_and_follow_offline_tests(self):
        guard = "if: github.event_name == 'schedule' || github.event_name == 'workflow_dispatch'"
        for filename, writer in (('sec_pipeline.yml', 'ft_sec_pipeline.py'),
                                 ('macro_pipeline.yml', 'ft_macro_coordinator.py')):
            text = (ROOT / '.github/workflows' / filename).read_text()
            self.assertIn('"feed_validation.py"', text)
            self.assertIn(guard, text)
            self.assertLess(text.index('python tests/run_offline.py'), text.index('run: python ' + writer))
            step = text[text.rfind('      - name:', 0, text.index('run: python ' + writer)):]
            self.assertIn(guard, step)

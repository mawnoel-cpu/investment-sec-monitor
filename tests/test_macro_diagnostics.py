import os
import unittest
from unittest.mock import patch

import requests
import ft_macro_pipeline as pipeline


class DiagnosticsTests(unittest.TestCase):
    def test_rejection_classification_never_echoes_server_text(self):
        response = requests.Response()
        response.status_code = 400
        response._content = b'{"error_message":"Bad Request. The value for variable api_key is not registered. PRIVATE https://example.invalid"}'
        result = pipeline.fred_api_rejection(requests.HTTPError(response=response))
        self.assertEqual(result, "FRED rejects API key as invalid or unregistered")
        self.assertNotIn("PRIVATE", result)

    def test_key_format_and_parameter_rejections(self):
        for message, expected in [
            ("api_key must be a 32 character alpha-numeric string", "API key format"),
            ("series_id does not exist", "parameter series_id"),
            ("unrecognized PRIVATE", "raw response withheld"),
        ]:
            response = requests.Response()
            response._content = ('{"error_message":"' + message + '"}').encode()
            self.assertIn(expected, pipeline.fred_api_rejection(requests.HTTPError(response=response)))

    def test_nested_errors_exclude_credentials_and_urls(self):
        inner = OSError(111, "api_key=PRIVATE https://example.invalid")
        outer = requests.ConnectionError(inner)
        result = pipeline.safe_transport_error(outer)
        self.assertIn("errno=111", result)
        self.assertNotIn("PRIVATE", result)
        self.assertNotIn("https", result)

    def test_api_failure_is_retained_after_both_fallbacks_fail(self):
        response = requests.Response()
        response.status_code = 403
        api_error = requests.HTTPError("secret", response=response)
        with patch.dict(os.environ, {"FRED_API_KEY": "PRIVATE"}), patch.object(
            pipeline, "http_get", side_effect=[api_error, requests.ConnectionError(), requests.Timeout()] * 6
        ):
            rows, errors = pipeline.fred_rows("2026-10-06T20:00:00-04:00")
        self.assertEqual(rows, [])
        self.assertEqual(len(errors), 6)
        self.assertIn("API HTTPError(HTTP=403)", errors[0])
        self.assertIn("page Timeout", errors[0])
        self.assertNotIn("PRIVATE", " ".join(errors))

    def test_absent_key_is_explicit(self):
        with patch.dict(os.environ, {"FRED_API_KEY": ""}), patch.object(
            pipeline, "http_get", side_effect=requests.ConnectionError("secret")
        ):
            _, errors = pipeline.fred_rows("2026-10-06T20:00:00-04:00")
        self.assertIn("API skipped (key absent)", errors[0])
        self.assertNotIn("secret", " ".join(errors))

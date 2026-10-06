import unittest
from types import SimpleNamespace
from unittest.mock import patch
import ft_macro_pipeline as pipeline


class HTTPBudgetTests(unittest.TestCase):
    def route(self, url):
        session = SimpleNamespace(mount=lambda *args: None)
        response = SimpleNamespace(raise_for_status=lambda: None)
        captured = {}
        def mount(prefix, adapter):
            captured["retry"] = adapter.max_retries
        def get(url, **kwargs):
            captured["timeout"] = kwargs["timeout"]
            return response
        session.mount, session.get = mount, get
        with patch("requests.Session", return_value=session):
            pipeline.http_get(url)
        return captured, session

    def test_fred_fallback_routes_have_short_budget(self):
        for url in (pipeline.FRED_API_URL, pipeline.FRED_CSV_URL,
                    "https://fred.stlouisfed.org/series/NFCI"):
            captured, session = self.route(url)
            self.assertEqual(captured["retry"].total, 1)
            self.assertEqual(captured["timeout"], (10, 20))
            self.assertEqual(session.max_redirects, 3)

    def test_long_retry_after_cannot_consume_job_deadline(self):
        captured, _ = self.route(pipeline.FRED_CSV_URL)
        retry = captured["retry"]
        response = SimpleNamespace(headers={"Retry-After": "3600"})
        self.assertEqual(retry.get_retry_after(response), 15)
        self.assertEqual(retry.new().get_retry_after(response), 15)
        response.headers["Retry-After"] = "5"
        self.assertEqual(retry.get_retry_after(response), 5)

    def test_healthy_source_routes_keep_their_retry_budget(self):
        captured, _ = self.route(pipeline.CFTC_TFF_URL)
        self.assertEqual(captured["retry"].total, 4)
        self.assertEqual(captured["timeout"], (15, 30))

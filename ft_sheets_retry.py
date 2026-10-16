"""Bounded Sheets quota recovery for the FT price/rotation/health pipeline."""
import time

from gspread.http_client import HTTPClient
from gspread.exceptions import APIError


class FTSheetsHTTPClient(HTTPClient):
    def _pace(self):
        # A complete price -> rotation -> health run can exceed Sheets' 60
        # reads/minute even with zero new bars. Keep each process below that
        # ceiling instead of relying solely on repeated rejected requests.
        wait = 1.3 - (time.monotonic() - getattr(self, "_last_request", 0))
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()

    def request(self, *args, **kwargs):
        # Quota rejection means the request was not applied. Repeating exact
        # cell updates is safe. Do not retry ambiguous network/write failures
        # or permission errors, and never turn a retry into a fresh timestamp.
        for attempt in range(4):
            self._pace()
            try:
                return super().request(*args, **kwargs)
            except APIError as exc:
                quota = exc.code == 429 or (exc.code == 403 and any(
                    e.get("domain") == "usageLimits" for e in exc.error.get("errors", [])))
                if not quota or attempt == 3:
                    raise
                time.sleep((15, 30, 45)[attempt])

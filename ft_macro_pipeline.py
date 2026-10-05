"""FRED / EIA / CFTC -> FT Game Intelligence Macro Feed.

Discovery/context only. This collector writes raw macro observations to Macro Feed and
never promotes them directly into eligible Evidence or trading signals.
"""

from __future__ import annotations

import csv
import io
import json
import os
import math
import re
from html.parser import HTMLParser
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo


SPREADSHEET_ID = "1ItUi3uYPK4AvuHQ6PHIZTRvQnVhMiLONefTpVLEzGGE"
MACRO_SHEET = "Macro Feed"
GAME_END = datetime(2026, 11, 27, 23, 59, 59, tzinfo=ZoneInfo("America/Toronto"))

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
]

FRED_SERIES = {
    "BAMLH0A0HYM2": ("US high-yield spread", "Percent", "Daily"),
    "BAMLC0A0CM": ("US investment-grade corporate spread", "Percent", "Daily"),
    "DFII10": ("10-year real Treasury yield", "Percent", "Daily"),
    "T10Y2Y": ("10y minus 2y Treasury spread", "Percent", "Daily"),
    "NFCI": ("Chicago Fed National Financial Conditions Index", "Index", "Weekly"),
    "DRTSCILM": ("Banks tightening C&I lending standards", "Percent", "Quarterly"),
}

EIA_SERIES = {
    "NG.NW2_EPG0_SWO_R48_BCF.W": (
        "Lower-48 natural gas working underground storage", "BCF", "Weekly", "Official EIA history workbook; billion cubic feet", True
    ),
    "PET.WCESTUS1.W": (
        "US crude oil stocks excluding SPR", "Thousand barrels", "Weekly", "Official EIA history workbook; excludes strategic reserves", True
    ),
    "NG.RNGWHHD.D": (
        "Henry Hub natural gas spot price", "USD per MMBtu", "Daily", "Official EIA Henry Hub history workbook", True
    ),
    # Electricity remains excluded until independently validated.
    "ELEC.GEN.ALL-US-99.M": (
        "US electricity generation", "", "Monthly",
        "Disabled pending verification of EIA v2 facet/value mapping", False
    ),
}

CFTC_COMMODITIES = {
    "023651": ("Natural gas", "10,000 MMBtu contracts"),
    "067651": ("WTI crude oil", "1,000-barrel contracts"),
    "085692": ("Copper", "25,000-pound contracts"),
    "088691": ("Gold", "100-troy-ounce contracts"),
}
CFTC_TFF = {
    "043602": ("10-year US Treasury", "$100,000-face-value contracts"),
}

CFTC_DISAGG_URL = "https://publicreporting.cftc.gov/resource/kh3c-gbw2.json"
CFTC_TFF_URL = "https://publicreporting.cftc.gov/resource/yw9f-hn96.json"


def google_client():
    import gspread
    from google.oauth2.service_account import Credentials

    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    if not raw:
        raise RuntimeError("Missing GOOGLE_SERVICE_ACCOUNT_JSON secret")
    credentials = Credentials.from_service_account_info(json.loads(raw), scopes=SCOPES)
    return gspread.authorize(credentials)


def http_get(url: str, *, params: dict[str, Any] | None = None):
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry

    session = requests.Session()
    retry = Retry(
        total=4,
        connect=4,
        read=4,
        status=4,
        backoff_factor=1.5,
        status_forcelist=(408, 425, 429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    response = session.get(
        url,
        params=params,
        timeout=(15, 30),
        headers={
            "User-Agent": "FT Game Intelligence Macro Monitor/1.1",
            "Accept": "text/csv,text/plain,text/html,application/json;q=0.9,*/*;q=0.8",
        },
    )
    response.raise_for_status()
    return response


def existing_keys(ws: Any) -> set[str]:
    values = ws.get(f"L8:L{ws.row_count}")
    return {str(row[0]).strip() for row in values if row and str(row[0]).strip()}


def append_rows_dedup(ws: Any, candidate_rows: list[list[Any]], keys: set[str]) -> int:
    rows: list[list[Any]] = []
    for row in candidate_rows:
        key = str(row[11]).strip()
        if not key or key in keys:
            continue
        rows.append(row)
        keys.add(key)
    if rows:
        ws.append_rows(rows, value_input_option="USER_ENTERED", table_range="A8:O")
    return len(rows)


def first_empty_log_row(ws: Any) -> int:
    values = ws.get(f"R8:R{ws.row_count}")
    for offset, row in enumerate(values):
        if not row or not str(row[0]).strip():
            return 8 + offset
    old_rows = ws.row_count
    ws.add_rows(50)
    return old_rows + 1


def append_run_log(ws: Any, source: str, status: str, series_checked: int, new_rows: int) -> None:
    row = first_empty_log_row(ws)
    ws.update(
        range_name=f"R{row}:V{row}",
        values=[[
            datetime.now(ZoneInfo("America/Toronto")).isoformat(),
            source,
            status,
            series_checked,
            new_rows,
        ]],
        value_input_option="USER_ENTERED",
    )


class OfficialPage(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.text, self.rows, self.row, self.cell = [], [], None, None
        self.ignored = 0
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.ignored += 1
        if tag == 'tr':
            self.row = []
        if tag in ('td', 'th') and self.row is not None:
            self.cell = []

    def handle_data(self, data):
        if not self.ignored:
            self.text.append(data)
            if self.cell is not None:
                self.cell.append(data)

    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            self.ignored = max(0, self.ignored - 1)
        if tag in ('td', 'th') and self.cell is not None:
            self.row.append(' '.join(' '.join(self.cell).split()))
            self.cell = None
        if tag == 'tr' and self.row is not None:
            self.rows.append(self.row)
            self.row = None


FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"


def fred_csv_observations(text, series_id):
    """Parse FRED's official CSV export and require the requested series ID."""
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames or series_id not in reader.fieldnames:
        raise ValueError("Official FRED CSV series identity mismatch")

    date_field = next(
        (name for name in reader.fieldnames if str(name).strip().lower() in {"date", "observation_date"}),
        reader.fieldnames[0],
    )
    observations = {}
    for row in reader:
        period = str(row.get(date_field, "") or "").strip()
        raw_value = str(row.get(series_id, "") or "").strip()
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", period):
            continue
        if raw_value in {"", "."}:
            continue
        try:
            value = float(raw_value)
        except ValueError:
            continue
        if math.isfinite(value):
            observations[period] = value

    if not observations:
        raise ValueError("No dated official FRED CSV observations")
    return sorted(observations.items())[-5:]


def fred_page_observations(text, series_id):
    page = OfficialPage(text)
    text = ' '.join(' '.join(page.text).split())
    _, units, frequency = FRED_SERIES[series_id]
    if not re.search(r'\(' + re.escape(series_id) + r'\)', text) or not re.search(r'Units\s*:\s*' + re.escape(units) + r'\b', text) or not re.search(r'Frequency\s*:\s*' + re.escape(frequency) + r'\b', text):
        raise ValueError('Official FRED series identity, units or frequency mismatch')
    observations = {}
    for period, value in re.findall(r'(\d{4}-\d{2}-\d{2})\s*:\s*(-?\d+(?:\.\d+)?)', text):
        observations[datetime.fromisoformat(period).date().isoformat()] = float(value)
    # FRED's quarterly display uses Qn YYYY, corresponding to quarter start.
    if frequency == 'Quarterly':
        for quarter, year, value in re.findall(r'Q([1-4])\s+(\d{4})\s*:\s*(-?\d+(?:\.\d+)?)', text):
            observations[f'{year}-{(int(quarter)-1)*3+1:02d}-01'] = float(value)
    if not observations:
        raise ValueError('No dated official FRED observations')
    return sorted(observations.items())[-5:]


def fred_rows(captured: str) -> tuple[list[list[Any]], list[str]]:
    rows: list[list[Any]] = []
    errors: list[str] = []
    for series_id, (label, units, frequency) in FRED_SERIES.items():
        source = f"https://fred.stlouisfed.org/series/{series_id}"
        csv_source = FRED_CSV_URL + "?id=" + series_id
        try:
            method = "official CSV"
            try:
                observations = fred_csv_observations(http_get(csv_source).text, series_id)
            except Exception as csv_exc:  # noqa: BLE001
                method = "series-page fallback"
                try:
                    observations = fred_page_observations(http_get(source).text, series_id)
                except Exception as page_exc:  # noqa: BLE001
                    raise RuntimeError(
                        f"CSV {type(csv_exc).__name__}; page {type(page_exc).__name__}"
                    ) from page_exc

            for date, value in observations:
                key = f"FRED:{series_id}:{date}:Observation"
                rows.append([
                    captured, "FRED", series_id, label, date, float(value), units, frequency,
                    "Observation", "US macro/financial conditions",
                    source, key,
                    captured[:10], "Verified data",
                    f"Official FRED {method}; exact series ID and dated values checked. "
                    "Configured units/frequency retained. Context only, not a trade signal.",
                ])
        except Exception as exc:  # noqa: BLE001
            errors.append(f"FRED {series_id}: {type(exc).__name__}")
    return rows, errors


EIA_DOWNLOADS = {
    "NG.NW2_EPG0_SWO_R48_BCF.W": (
        "https://www.eia.gov/dnav/ng/xls/NG.NW2_EPG0_SWO_R48_BCF.W.xls",
        "https://www.eia.gov/dnav/ng/hist/nw2_epg0_swo_r48_bcfw.htm",
        ("lower 48", "working underground storage", "billion cubic feet"),
    ),
    "PET.WCESTUS1.W": (
        "https://www.eia.gov/dnav/pet/xls/PET.WCESTUS1.W.xls",
        "https://www.eia.gov/dnav/pet/hist/LeafHandler.ashx?n=PET&s=WCESTUS1&f=W",
        ("excluding spr", "crude oil", "thousand barrels"),
    ),
    "NG.RNGWHHD.D": (
        "https://www.eia.gov/dnav/ng/xls/NG.RNGWHHD.D.xls",
        "https://www.eia.gov/dnav/ng/hist/rngwhhdd.htm",
        ("henry hub", "spot price", "dollars per million btu"),
    ),
}


def eia_workbook_observations(book, series_id: str) -> list[tuple[str, float]]:
    """Accept only the exact official history series, category and units."""
    import xlrd
    tokens = EIA_DOWNLOADS[series_id][2]
    for sheet in book.sheets():
        if sheet.ncols < 2:
            continue
        header = " ".join(
            str(sheet.cell_value(r, c)).lower()
            for r in range(min(12, sheet.nrows))
            for c in range(min(4, sheet.ncols))
            if sheet.cell_type(r, c) == xlrd.XL_CELL_TEXT
        )
        if not all(token in header for token in tokens):
            continue
        observations = []
        for r in range(sheet.nrows):
            if sheet.cell_type(r, 0) != xlrd.XL_CELL_DATE:
                continue
            if sheet.cell_type(r, 1) != xlrd.XL_CELL_NUMBER:
                continue
            date = xlrd.xldate_as_datetime(sheet.cell_value(r, 0), book.datemode).date().isoformat()
            value = float(sheet.cell_value(r, 1))
            if not math.isfinite(value):
                raise ValueError("Non-finite EIA value")
            observations.append((date, value))
        if observations:
            return sorted(observations)[-5:]
    raise ValueError("EIA series identity, units or dated observations could not be verified")


def eia_page_observations(text, series_id):
    page = OfficialPage(text)
    identity = ' '.join(' '.join(page.text).lower().split())
    if not all(token in identity for token in EIA_DOWNLOADS[series_id][2]):
        raise ValueError('EIA official history category or units mismatch')
    observations = {}
    for row in page.rows:
        if not row:
            continue
        label = row[0]
        if series_id == 'NG.RNGWHHD.D':
            match = re.match(r'(\d{4})\s+([A-Za-z]{3})-\s*(\d{1,2})\s+to\b', label)
            if not match:
                continue
            year, month, day = match.groups()
            start = datetime.strptime(f'{year} {month} {day}', '%Y %b %d').date()
            for offset, value in enumerate(row[1:6]):
                if re.fullmatch(r'-?\d+(?:\.\d+)?', value):
                    observations[(start+timedelta(days=offset)).isoformat()] = float(value)
        else:
            match = re.match(r'(\d{4})-[A-Za-z]{3}', label)
            if not match:
                continue
            for i in range(1, len(row)-1, 2):
                period, value = row[i:i+2]
                if re.fullmatch(r'\d{2}/\d{2}', period) and re.fullmatch(r'\d[\d,]*(?:\.\d+)?', value):
                    date = datetime.strptime(f'{match.group(1)}/{period}', '%Y/%m/%d').date().isoformat()
                    observations[date] = float(value.replace(',', ''))
    if not observations:
        raise ValueError('No dated EIA official history observations')
    return sorted(observations.items())[-5:]


def eia_rows(captured: str) -> tuple[list[list[Any]], list[str], int]:
    enabled = [(sid, meta) for sid, meta in EIA_SERIES.items() if meta[4]]
    rows, errors = [], []
    for series_id, (label, units, frequency, note, _) in enabled:
        try:
            _, original_url, _ = EIA_DOWNLOADS[series_id]
            for period, value in eia_page_observations(http_get(original_url).text, series_id):
                rows.append([
                    captured, "EIA", series_id, label, period, value, units, frequency,
                    "Observation", "US energy", original_url,
                    f"EIA:{series_id}:{period}:Observation", captured[:10], "Verified data",
                    note + "; source header/category/units/date checked. Context only; electricity remains excluded.",
                ])
        except Exception as exc:
            errors.append(f"EIA {series_id}: {type(exc).__name__}")
    return rows, errors, len(enabled)


def _socrata_rows(url: str, code: str) -> list[dict[str, Any]]:
    params = {
        "$where": f"cftc_contract_market_code='{code}'",
        "$order": "report_date_as_yyyy_mm_dd DESC",
        "$limit": 2,
    }
    payload = http_get(url, params=params).json()
    if not isinstance(payload, list):
        raise ValueError("unexpected CFTC response")
    expected = {"023651":"NAT GAS NYME", "067651":"WTI-PHYSICAL", "085692":"COPPER- #1", "088691":"GOLD", "043602":"UST 10Y NOTE"}
    if not payload or any(str(r.get('cftc_contract_market_code','')) != code or r.get('contract_market_name') != expected[code] for r in payload):
        raise ValueError('CFTC contract identity mismatch')
    return payload


def _number(item: dict[str, Any], *keys: str) -> float:
    for key in keys:
        raw = item.get(key)
        if raw not in (None, ""):
            value = float(raw)
            if not math.isfinite(value):
                raise ValueError("Non-finite CFTC value")
            return value
    raise KeyError(f"missing fields: {', '.join(keys)}")


def _display_number(value: float) -> int | float:
    return int(value) if value.is_integer() else value


def cftc_rows(captured: str) -> tuple[list[list[Any]], list[str]]:
    rows: list[list[Any]] = []
    errors: list[str] = []

    for code, (label, contract_units) in CFTC_COMMODITIES.items():
        try:
            for item in _socrata_rows(CFTC_DISAGG_URL, code):
                date = str(item.get("report_date_as_yyyy_mm_dd", ""))[:10]
                market = str(item.get("contract_market_name") or item.get("market_and_exchange_names") or label)
                long_pos = _number(item, "m_money_positions_long_all")
                short_pos = _number(item, "m_money_positions_short_all")
                open_interest = _number(item, "open_interest_all")
                if long_pos < 0 or short_pos < 0 or open_interest <= 0:
                    raise ValueError("Invalid CFTC positions")
                net = long_pos - short_pos
                ratio = net / open_interest if open_interest else 0.0
                measures = [
                    ("Managed money long", long_pos, "Contracts"),
                    ("Managed money short", short_pos, "Contracts"),
                    ("Managed money net", net, "Contracts"),
                    ("Open interest", open_interest, "Contracts"),
                    ("Managed money net / open interest", ratio, "Ratio"),
                ]
                for measure, value, units in measures:
                    rows.append([
                        captured, "CFTC", code, label, date, _display_number(value), units,
                        "Weekly", measure, market, CFTC_DISAGG_URL,
                        f"CFTC:{code}:{date}:{measure}", "", "Verified data",
                        "Positions as of Tuesday; usually released Friday. Delayed crowding context, not an automatic allocation signal. "
                        f"Disaggregated futures and options combined; {contract_units}",
                    ])
        except Exception as exc:  # noqa: BLE001
            errors.append(f"CFTC {code}: {type(exc).__name__}")

    for code, (label, contract_units) in CFTC_TFF.items():
        try:
            for item in _socrata_rows(CFTC_TFF_URL, code):
                date = str(item.get("report_date_as_yyyy_mm_dd", ""))[:10]
                market = str(item.get("contract_market_name") or item.get("market_and_exchange_names") or label)
                long_pos = _number(item, "lev_money_positions_long_all", "lev_money_positions_long")
                short_pos = _number(item, "lev_money_positions_short_all", "lev_money_positions_short")
                open_interest = _number(item, "open_interest_all")
                if long_pos < 0 or short_pos < 0 or open_interest <= 0:
                    raise ValueError("Invalid CFTC positions")
                net = long_pos - short_pos
                ratio = net / open_interest if open_interest else 0.0
                measures = [
                    ("Leveraged money long", long_pos, "Contracts"),
                    ("Leveraged money short", short_pos, "Contracts"),
                    ("Leveraged money net", net, "Contracts"),
                    ("Open interest", open_interest, "Contracts"),
                    ("Leveraged money net / open interest", ratio, "Ratio"),
                ]
                for measure, value, units in measures:
                    rows.append([
                        captured, "CFTC", code, label, date, _display_number(value), units,
                        "Weekly", measure, market, CFTC_TFF_URL,
                        f"CFTC:{code}:{date}:{measure}", "", "Verified data",
                        "Positions as of Tuesday; usually released Friday. Delayed crowding context, not an automatic allocation signal. "
                        f"TFF futures and options combined; {contract_units}",
                    ])
        except Exception as exc:  # noqa: BLE001
            errors.append(f"CFTC {code}: {type(exc).__name__}")
    return rows, errors


def main():
    from ft_macro_coordinator import main as run
    return run()

if __name__ == "__main__":
    raise SystemExit(main())

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
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import gspread
import requests
from google.oauth2.service_account import Credentials

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
        "US natural gas storage", "BCF", "Weekly", "Verified series from pilot", True
    ),
    "PET.WCESTUS1.W": (
        "US crude oil stocks", "Thousand barrels", "Weekly", "Verified series from pilot", True
    ),
    # Retained from the pilot but disabled until its v2 value/facet mapping is verified.
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


def google_client() -> gspread.Client:
    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    if not raw:
        raise RuntimeError("Missing GOOGLE_SERVICE_ACCOUNT_JSON secret")
    credentials = Credentials.from_service_account_info(json.loads(raw), scopes=SCOPES)
    return gspread.authorize(credentials)


def http_get(url: str, *, params: dict[str, Any] | None = None) -> requests.Response:
    response = requests.get(
        url,
        params=params,
        timeout=45,
        headers={"User-Agent": "FT Game Intelligence Macro Monitor/1.0"},
    )
    response.raise_for_status()
    return response


def existing_keys(ws: gspread.Worksheet) -> set[str]:
    values = ws.get(f"L8:L{ws.row_count}")
    return {str(row[0]).strip() for row in values if row and str(row[0]).strip()}


def append_rows_dedup(ws: gspread.Worksheet, candidate_rows: list[list[Any]], keys: set[str]) -> int:
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


def first_empty_log_row(ws: gspread.Worksheet) -> int:
    values = ws.get(f"R8:R{ws.row_count}")
    for offset, row in enumerate(values):
        if not row or not str(row[0]).strip():
            return 8 + offset
    old_rows = ws.row_count
    ws.add_rows(50)
    return old_rows + 1


def append_run_log(ws: gspread.Worksheet, source: str, status: str, series_checked: int, new_rows: int) -> None:
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


def fred_rows(captured: str) -> tuple[list[list[Any]], list[str]]:
    rows: list[list[Any]] = []
    errors: list[str] = []
    for series_id, (label, units, frequency) in FRED_SERIES.items():
        try:
            response = http_get(
                "https://fred.stlouisfed.org/graph/fredgraph.csv",
                params={"id": series_id},
            )
            parsed = list(csv.DictReader(io.StringIO(response.text)))
            observations = [
                item for item in parsed if item.get(series_id) not in (None, "", ".")
            ][-5:]
            for item in observations:
                date = item.get("observation_date") or item.get("DATE") or ""
                value = item.get(series_id, "")
                key = f"FRED:{series_id}:{date}:{value}"
                rows.append([
                    captured, "FRED", series_id, label, date, value, units, frequency,
                    "Observation", "US macro/financial conditions",
                    f"https://fred.stlouisfed.org/series/{series_id}", key,
                    captured[:10], "Official source",
                    "Context only; review the series and current market regime before using in Evidence.",
                ])
        except Exception as exc:  # noqa: BLE001
            errors.append(f"FRED {series_id}: {type(exc).__name__}: {exc}")
    return rows, errors


def _eia_data(payload: dict[str, Any]) -> list[dict[str, Any]]:
    response = payload.get("response", {})
    data = response.get("data", [])
    return data if isinstance(data, list) else []


def eia_rows(captured: str) -> tuple[list[list[Any]], list[str], int]:
    api_key = os.environ.get("EIA_API_KEY", "").strip()
    enabled = [(sid, meta) for sid, meta in EIA_SERIES.items() if meta[4]]
    if not api_key:
        return [], ["EIA: missing EIA_API_KEY secret"], len(enabled)

    rows: list[list[Any]] = []
    errors: list[str] = []
    for series_id, (label, units, frequency, note, enabled_flag) in enabled:
        if not enabled_flag:
            continue
        try:
            url = f"https://api.eia.gov/v2/seriesid/{series_id}"
            payload = http_get(url, params={"api_key": api_key}).json()
            data = _eia_data(payload)
            if not data:
                raise ValueError("no data returned")
            data = sorted(data, key=lambda x: str(x.get("period", "")))[-5:]
            for item in data:
                period = str(item.get("period", ""))
                value = item.get("value", "")
                row_units = str(item.get("units") or item.get("unit") or units)
                key = f"EIA:{series_id}:{period}:{value}"
                rows.append([
                    captured, "EIA", series_id, label, period, value, row_units, frequency,
                    "Observation", "US energy", url, key, captured[:10], "Official source",
                    f"{note}. Context only; electricity series remains disabled pending verification.",
                ])
        except Exception as exc:  # noqa: BLE001
            errors.append(f"EIA {series_id}: {type(exc).__name__}: {exc}")
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
                        f"CFTC:{code}:{date}:{measure}", "", "Official source",
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
                long_pos = _number(item, "lev_money_positions_long_all")
                short_pos = _number(item, "lev_money_positions_short_all")
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
                        f"CFTC:{code}:{date}:{measure}", "", "Official source",
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

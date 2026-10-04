"""SEC EDGAR -> FT Game Intelligence SEC Feed.

Discovery-only collector. It reads the FT Game portfolio universe, fetches recent
SEC filings, deduplicates by accession, and writes to the SEC Feed tab. It does
not create eligible Evidence or trading signals.

This file is wired to GitHub Actions so collector changes automatically run a
verification pass in addition to the normal twice-daily schedule.
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

SPREADSHEET_ID = "1ItUi3uYPK4AvuHQ6PHIZTRvQnVhMiLONefTpVLEzGGE"
PORTFOLIO_SHEET = "Portfolio"
SEC_FEED_SHEET = "SEC Feed"
GAME_START = date(2026, 9, 1)
GAME_END = datetime(2026, 11, 27, 23, 59, 59, tzinfo=ZoneInfo("America/Toronto"))

DATA_HEADERS = [
    "Captured at", "FT ticker", "SEC issuer", "CIK", "Form", "Filed",
    "Report period", "Accession", "Description", "Original URL",
    "Document state", "Duplicate key", "Coverage note",
]
LOG_HEADERS = ["Run at", "Status", "Issuers checked", "New filings", "Duplicates", "Failures", "Notes"]


def validate_destination(book, ws):
    from feed_validation import validate_destination as validate
    return validate(book, ws, {
        "FTSECFilings": ("A7:M7", DATA_HEADERS, 0),
        "FTSECRunLog": ("P7:V7", LOG_HEADERS, 15),
    })


RELEVANT_FORMS = {
    "10-K", "10-K/A", "10-Q", "10-Q/A", "8-K", "8-K/A",
    "20-F", "20-F/A", "6-K", "6-K/A",
    "3", "4", "4/A", "5",
    "SC 13D", "SC 13D/A", "SC 13G", "SC 13G/A",
}

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
]


class SecClient:
    def __init__(self, contact_email: str) -> None:
        import requests
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry
        if "@" not in contact_email:
            raise ValueError("SEC_CONTACT_EMAIL must be a valid email address")
        self.session = requests.Session()
        retry = Retry(
            total=5,
            connect=5,
            read=5,
            status=5,
            backoff_factor=2,
            status_forcelist=(403, 429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET"}),
            respect_retry_after_header=True,
        )
        self.session.mount("https://", HTTPAdapter(max_retries=retry))
        self.session.headers.update({
            "User-Agent": f"FT Game Intelligence Monitor/1.0 {contact_email}",
            "Accept": "application/json,text/plain,*/*",
            "Accept-Encoding": "gzip, deflate",
        })
        self.last_request = 0.0

    def get_json(self, url: str) -> dict[str, Any]:
        elapsed = time.monotonic() - self.last_request
        if elapsed < 0.6:
            time.sleep(0.6 - elapsed)
        response = self.session.get(url, timeout=45)
        self.last_request = time.monotonic()
        response.raise_for_status()
        return response.json()


def google_client() -> Any:
    import gspread
    from google.oauth2.service_account import Credentials
    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    if not raw:
        raise RuntimeError("Missing GOOGLE_SERVICE_ACCOUNT_JSON secret")
    info = json.loads(raw)
    credentials = Credentials.from_service_account_info(info, scopes=SCOPES)
    return gspread.authorize(credentials)


def portfolio_universe(ws: Any) -> list[str]:
    if ws.get("A7:D7") != [["Ticker", "Company", "Current", "Queued target"]]:
        raise ValueError("Portfolio headers mismatch at A7:D7")
    # Summary/rules below the supported holdings block must not hide new holdings.
    summaries = {20: "TOTAL", 21: "Weight check", 22: "Holdings", 23: "Position cap"}
    outside = ws.get(f"A20:D{ws.row_count}", value_render_option="UNFORMATTED_VALUE")
    for number, row in enumerate(outside, 20):
        ticker = str(row[0]).strip() if row and row[0] is not None else ""
        if not ticker or summaries.get(number) == ticker:
            continue
        weights = row[2:4]
        weighted = any(isinstance(v, (int, float)) and not isinstance(v, bool) for v in weights)
        ticker_only = (re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.^/:-]*", ticker)
                       and not any(v not in ("", None) for v in weights))
        if weighted or ticker_only:
            raise ValueError(f"Portfolio holding outside supported A8:A19 range at row {number}")
    values = ws.get("A8:A19")
    tickers: list[str] = []
    for row in values:
        if not row or not row[0]:
            continue
        ticker = str(row[0]).strip()
        if ticker and ticker.upper() != "TOTAL":
            tickers.append(ticker)
    if not tickers:
        raise ValueError("Unexpectedly empty SEC portfolio universe at A8:A19")
    return tickers


def ticker_map(sec: SecClient) -> dict[str, dict[str, Any]]:
    raw = sec.get_json("https://www.sec.gov/files/company_tickers.json")
    result: dict[str, dict[str, Any]] = {}
    for row in raw.values():
        ticker = str(row.get("ticker", "")).upper().strip()
        cik = row.get("cik_str")
        if ticker and cik is not None:
            result[ticker] = {
                "cik": str(int(cik)).zfill(10),
                "title": str(row.get("title", "")),
            }
    return result


def filing_url(cik: str, accession: str, primary_document: str) -> str:
    clean_accession = accession.replace("-", "")
    base = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{clean_accession}/"
    return base + primary_document if primary_document else base


def existing_keys(ws: Any) -> set[str]:
    values = ws.get(f"L8:L{ws.row_count}")
    return {str(row[0]).strip() for row in values if row and str(row[0]).strip()}


def date_serial(value: str | datetime) -> float | str:
    if not value:
        return ""
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(ZoneInfo("America/Toronto")).replace(tzinfo=None)
    return (parsed - datetime(1899, 12, 30)).total_seconds() / 86400


def first_empty_run_row(ws: Any) -> int:
    values = ws.get(f"P8:P{ws.row_count}", value_render_option="UNFORMATTED_VALUE")
    for offset in range(max(ws.row_count - 7, 0)):
        row = values[offset] if offset < len(values) else []
        if not row or row[0] in ("", None):
            return 8 + offset
    return ws.row_count + 1


def write_filings_verified(book, ws, rows):
    from ft_macro_coordinator import update
    table = validate_destination(book, ws)["FTSECFilings"]
    values = ws.get(f"A8:A{ws.row_count}", value_render_option="UNFORMATTED_VALUE")
    used = [i + 8 for i, v in enumerate(values) if v and v[0] not in ("", None)]
    last = max(used, default=7)
    end = last + len(rows)
    if end > ws.row_count:
        ws.add_rows(end - ws.row_count + 25)
    requests = []
    if rows:
        requests.append(update(ws.id, last, 0, rows))
    region = dict(table["range"])
    region["endRowIndex"] = max(end, 8)
    requests.append({"updateTable": {"table": {"tableId": table["tableId"], "range": region}, "fields": "range"}})
    for column, kind, pattern in ((0, "DATE_TIME", "dd/MM/yyyy HH:mm:ss"), (5, "DATE", "dd/MM/yyyy"), (6, "DATE", "dd/MM/yyyy")):
        requests.append({"repeatCell": {
            "range": {"sheetId": ws.id, "startRowIndex": 7, "endRowIndex": max(end, 8),
                      "startColumnIndex": column, "endColumnIndex": column + 1},
            "cell": {"userEnteredFormat": {"numberFormat": {"type": kind, "pattern": pattern}}},
            "fields": "userEnteredFormat.numberFormat",
        }})
    book.batch_update({"requests": requests})
    if rows:
        got = ws.get(f"A{last + 1}:M{end}", value_render_option="UNFORMATTED_VALUE")
        if got != rows:
            raise RuntimeError("SEC filing readback mismatch")


def append_run_log(book, ws, status, issuers_checked, new_filings, duplicates, failures, notes):
    from ft_macro_coordinator import update
    table = validate_destination(book, ws)["FTSECRunLog"]
    row = first_empty_run_row(ws)
    if row > ws.row_count:
        ws.add_rows(row - ws.row_count + 25)
    region = dict(table["range"])
    region["endRowIndex"] = row
    final = [date_serial(datetime.now(ZoneInfo("America/Toronto"))), status,
             issuers_checked, new_filings, duplicates, failures, notes]
    pending = list(final)
    pending[1] = "Partial"
    pending[6] = "Write committed; readback pending. " + notes
    book.batch_update({"requests": [
        update(ws.id, row - 1, 15, [pending]),
        {"updateTable": {"table": {"tableId": table["tableId"], "range": region}, "fields": "range"}},
        {"repeatCell": {
            "range": {"sheetId": ws.id, "startRowIndex": row - 1, "endRowIndex": row,
                      "startColumnIndex": 15, "endColumnIndex": 16},
            "cell": {"userEnteredFormat": {"numberFormat": {"type": "DATE_TIME", "pattern": "dd/MM/yyyy HH:mm:ss"}}},
            "fields": "userEnteredFormat.numberFormat",
        }},
    ]})
    got = ws.get(f"P{row}:V{row}", value_render_option="UNFORMATTED_VALUE")
    if not got or got[0] != pending:
        raise RuntimeError("SEC provisional log readback mismatch")
    book.batch_update({"requests": [update(ws.id, row - 1, 15, [final])]})
    got = ws.get(f"P{row}:V{row}", value_render_option="UNFORMATTED_VALUE")
    if not got or got[0] != final:
        raise RuntimeError("SEC final log readback mismatch")


def main() -> None:
    now = datetime.now(ZoneInfo("America/Toronto"))
    if now > GAME_END:
        print("FT Game ended; SEC collector exited without changes.")
        return

    contact_email = os.environ.get("SEC_CONTACT_EMAIL", "")
    sec = SecClient(contact_email)
    google = google_client()
    spreadsheet = google.open_by_key(SPREADSHEET_ID)
    portfolio_ws = spreadsheet.worksheet(PORTFOLIO_SHEET)
    feed_ws = spreadsheet.worksheet(SEC_FEED_SHEET)

    validate_destination(spreadsheet, feed_ws)
    universe = portfolio_universe(portfolio_ws)
    try:
        mapping = ticker_map(sec)
    except Exception as exc:
        append_run_log(spreadsheet, feed_ws, "Failure", 0, 0, 0, 1, "SEC issuer mapping unavailable: " + type(exc).__name__)
        raise RuntimeError("SEC issuer mapping unavailable") from None
    keys = existing_keys(feed_ws)

    rows: list[list[Any]] = []
    duplicates = 0
    failures: list[str] = []
    checked = 0
    successful = 0
    skipped_old = 0

    for ft_ticker in universe:
        sec_ticker = ft_ticker.split(":", 1)[0].upper()
        issuer = mapping.get(sec_ticker)
        if issuer is None:
            failures.append(f"{ft_ticker}: no SEC ticker mapping")
            continue

        checked += 1
        cik = issuer["cik"]
        try:
            submissions = sec.get_json(f"https://data.sec.gov/submissions/CIK{cik}.json")
            successful += 1
            recent = submissions.get("filings", {}).get("recent", {})
            forms = recent.get("form", [])
            accessions = recent.get("accessionNumber", [])
            filing_dates = recent.get("filingDate", [])
            report_dates = recent.get("reportDate", [])
            descriptions = recent.get("primaryDocDescription", [])
            primary_docs = recent.get("primaryDocument", [])

            for i, form in enumerate(forms[:200]):
                filing_date = filing_dates[i] if i < len(filing_dates) else ""
                if filing_date:
                    try:
                        if date.fromisoformat(filing_date) < GAME_START:
                            skipped_old += 1
                            continue
                    except ValueError:
                        pass
                if form not in RELEVANT_FORMS:
                    continue
                accession = accessions[i] if i < len(accessions) else ""
                if not accession:
                    continue
                duplicate_key = f"SEC|{cik}|{accession}"
                if duplicate_key in keys:
                    duplicates += 1
                    continue

                report_date = report_dates[i] if i < len(report_dates) else ""
                description = descriptions[i] if i < len(descriptions) else ""
                primary_doc = primary_docs[i] if i < len(primary_docs) else ""
                url = filing_url(cik, accession, primary_doc)

                rows.append([
                    date_serial(now),
                    ft_ticker,
                    issuer["title"],
                    cik,
                    form,
                    date_serial(filing_date),
                    date_serial(report_date),
                    accession,
                    description,
                    url,
                    "Discovery",
                    duplicate_key,
                    "Official SEC filing; review original document before promoting to Evidence.",
                ])
                keys.add(duplicate_key)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{ft_ticker}: {type(exc).__name__}")

    if rows:
        write_filings_verified(spreadsheet, feed_ws, rows)
    else:
        write_filings_verified(spreadsheet, feed_ws, [])

    status = "Success" if not failures else ("Partial" if successful else "Failure")
    notes = (
        f"FT Game SEC discovery feed; ignored {skipped_old} pre-game filings. "
        + ("; ".join(failures) if failures else "No issuer failures.")
    )
    append_run_log(spreadsheet, feed_ws, status, checked, len(rows), duplicates, len(failures), notes)

    result = {
        "status": status,
        "issuers_checked": checked,
        "new_filings": len(rows),
        "duplicates": duplicates,
        "pre_game_filings_skipped": skipped_old,
        "failures": failures,
    }
    print(json.dumps(result, ensure_ascii=False))
    if status == "Failure":
        raise RuntimeError(json.dumps(result))


if __name__ == "__main__":
    main()

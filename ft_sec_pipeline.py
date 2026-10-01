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
import time
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import gspread
import requests
from google.oauth2.service_account import Credentials
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

SPREADSHEET_ID = "1ItUi3uYPK4AvuHQ6PHIZTRvQnVhMiLONefTpVLEzGGE"
PORTFOLIO_SHEET = "Portfolio"
SEC_FEED_SHEET = "SEC Feed"
GAME_END = datetime(2026, 11, 27, 23, 59, 59, tzinfo=ZoneInfo("America/Toronto"))

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


def google_client() -> gspread.Client:
    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    if not raw:
        raise RuntimeError("Missing GOOGLE_SERVICE_ACCOUNT_JSON secret")
    info = json.loads(raw)
    credentials = Credentials.from_service_account_info(info, scopes=SCOPES)
    return gspread.authorize(credentials)


def portfolio_universe(ws: gspread.Worksheet) -> list[str]:
    values = ws.get("A8:A19")
    tickers: list[str] = []
    for row in values:
        if not row or not row[0]:
            continue
        ticker = str(row[0]).strip()
        if ticker and ticker.upper() != "TOTAL":
            tickers.append(ticker)
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


def existing_keys(ws: gspread.Worksheet) -> set[str]:
    values = ws.get(f"L8:L{ws.row_count}")
    return {str(row[0]).strip() for row in values if row and str(row[0]).strip()}


def first_empty_run_row(ws: gspread.Worksheet) -> int:
    # Column P is the dedicated run log. Use only populated values in that
    # column, rather than ws.row_count, because the SEC data table in A:M can
    # extend much farther down the sheet and gspread can report stale grid
    # dimensions during the same run.
    populated = ws.col_values(16)
    return max(8, len(populated) + 1)


def append_run_log(
    ws: gspread.Worksheet,
    status: str,
    issuers_checked: int,
    new_filings: int,
    duplicates: int,
    failures: int,
    notes: str,
) -> None:
    row = first_empty_run_row(ws)
    if row > ws.row_count:
        ws.add_rows(row - ws.row_count)
    ws.update(
        range_name=f"P{row}:V{row}",
        values=[[datetime.now(ZoneInfo("America/Toronto")).isoformat(), status,
                 issuers_checked, new_filings, duplicates, failures, notes]],
        value_input_option="USER_ENTERED",
    )


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

    universe = portfolio_universe(portfolio_ws)
    mapping = ticker_map(sec)
    keys = existing_keys(feed_ws)

    rows: list[list[Any]] = []
    duplicates = 0
    failures: list[str] = []
    checked = 0

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
            recent = submissions.get("filings", {}).get("recent", {})
            forms = recent.get("form", [])
            accessions = recent.get("accessionNumber", [])
            filing_dates = recent.get("filingDate", [])
            report_dates = recent.get("reportDate", [])
            descriptions = recent.get("primaryDocDescription", [])
            primary_docs = recent.get("primaryDocument", [])

            for i, form in enumerate(forms[:200]):
                if form not in RELEVANT_FORMS:
                    continue
                accession = accessions[i] if i < len(accessions) else ""
                if not accession:
                    continue
                duplicate_key = f"SEC|{cik}|{accession}"
                if duplicate_key in keys:
                    duplicates += 1
                    continue

                filing_date = filing_dates[i] if i < len(filing_dates) else ""
                report_date = report_dates[i] if i < len(report_dates) else ""
                description = descriptions[i] if i < len(descriptions) else ""
                primary_doc = primary_docs[i] if i < len(primary_docs) else ""
                url = filing_url(cik, accession, primary_doc)

                rows.append([
                    now.isoformat(),
                    ft_ticker,
                    issuer["title"],
                    cik,
                    form,
                    filing_date,
                    report_date,
                    accession,
                    description,
                    url,
                    "Discovery",
                    duplicate_key,
                    "Official SEC filing; review original document before promoting to Evidence.",
                ])
                keys.add(duplicate_key)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{ft_ticker}: {type(exc).__name__}: {exc}")

    if rows:
        feed_ws.append_rows(rows, value_input_option="USER_ENTERED", table_range="A8:M")

    status = "Success" if not failures else ("Partial" if checked else "Failure")
    notes = (
        "FT Game SEC discovery feed. "
        + ("; ".join(failures) if failures else "No issuer failures.")
    )
    append_run_log(feed_ws, status, checked, len(rows), duplicates, len(failures), notes)

    result = {
        "status": status,
        "issuers_checked": checked,
        "new_filings": len(rows),
        "duplicates": duplicates,
        "failures": failures,
    }
    print(json.dumps(result, ensure_ascii=False))
    if status == "Failure":
        raise RuntimeError(json.dumps(result))


if __name__ == "__main__":
    main()

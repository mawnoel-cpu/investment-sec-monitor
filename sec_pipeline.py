"""SEC EDGAR -> Google Sheets connector for the Investment Intelligence Monitor."""

from __future__ import annotations

import json
import logging
import os
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Iterable

import gspread
import requests
from google.oauth2.service_account import Credentials
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

SPREADSHEET_ID = "1NYgsQV7hZSmjjquQoyaP5ufNh3Rs_wDLjgf_TzSjpMc"
SEC_RAW_SHEET = "SEC Raw"
SIGNALS_SHEET = "Data Signals"
LOG_SHEET = "Data Automation Log"

COMPANIES = {
    "NVDA": {"cik": "0001045810", "name": "NVIDIA Corporation"},
    "META": {"cik": "0001326801", "name": "Meta Platforms, Inc."},
    "MU": {"cik": "0000723125", "name": "Micron Technology, Inc."},
    "CRWV": {"cik": "0001769628", "name": "CoreWeave, Inc."},
}

RELEVANT_FORMS = {
    "10-K", "10-K/A", "10-Q", "10-Q/A", "8-K", "8-K/A",
    "4", "4/A", "3", "5", "SC 13D", "SC 13D/A", "SC 13G", "SC 13G/A",
}

METRICS = {
    "SEC.NVDA.REVENUE": {
        "ticker": "NVDA",
        "metric": "Revenue",
        "concepts": [
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "Revenues",
            "SalesRevenueNet",
        ],
    },
    "SEC.META.CAPEX": {
        "ticker": "META",
        "metric": "Capital expenditure",
        "concepts": [
            "PaymentsToAcquirePropertyPlantAndEquipment",
            "PaymentsForAdditionsToPropertyPlantAndEquipment",
        ],
    },
    "SEC.MU.INVENTORY": {
        "ticker": "MU",
        "metric": "Inventory",
        "concepts": [
            "InventoryNet",
            "InventoryFinishedGoodsNetOfAllowancesCustomerAdvancesAndProgressBillings",
        ],
    },
    "SEC.CRWV.DEBT": {
        "ticker": "CRWV",
        "metric": "Long-term debt",
        "concepts": [
            "LongTermDebtAndFinanceLeaseObligationsCurrentAndNoncurrent",
            "LongTermDebtAndCapitalLeaseObligationsCurrent",
            "LongTermDebtCurrent",
            "LongTermDebtNoncurrent",
        ],
    },
}

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
]


@dataclass(frozen=True)
class FactResult:
    metric: str
    concept: str
    unit: str
    current: dict[str, Any]
    prior: dict[str, Any] | None


class SecClient:
    def __init__(self, contact_email: str) -> None:
        if "@" not in contact_email:
            raise ValueError("SEC_CONTACT_EMAIL must be a valid email address.")

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
        self.session.headers.update(
            {
                "User-Agent": f"Investment Intelligence Monitor {contact_email}",
                "Accept": "application/json",
                "Accept-Encoding": "gzip, deflate",
            }
        )
        self.last_request = 0.0

    def _get(self, url: str) -> requests.Response:
        # Conservative: at most about two requests per second.
        elapsed = time.monotonic() - self.last_request
        if elapsed < 0.55:
            time.sleep(0.55 - elapsed)

        response = self.session.get(url, timeout=45)
        self.last_request = time.monotonic()
        response.raise_for_status()
        return response

    def get_json(self, url: str) -> dict[str, Any]:
        return self._get(url).json()

    def get_text(self, url: str) -> str:
        return self._get(url).text


def google_client() -> gspread.Client:
    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    if not raw:
        raise RuntimeError("Missing GOOGLE_SERVICE_ACCOUNT_JSON secret.")

    info = json.loads(raw)
    credentials = Credentials.from_service_account_info(info, scopes=SCOPES)
    return gspread.authorize(credentials)


def sheet_values(worksheet: gspread.Worksheet) -> list[list[str]]:
    return worksheet.get_all_values()


def pad(row: list[Any], width: int) -> list[Any]:
    return row + [""] * max(0, width - len(row))


def upsert_rows(
    worksheet: gspread.Worksheet,
    rows: Iterable[list[Any]],
    key_column: int,
    width: int,
) -> tuple[int, int]:
    existing = sheet_values(worksheet)
    existing_keys = {
        row[key_column - 1]
        for row in existing[1:]
        if len(row) >= key_column and row[key_column - 1]
    }

    append_rows: list[list[Any]] = []
    duplicates = 0
    for row in rows:
        normalized = pad(list(row), width)[:width]
        key = str(normalized[key_column - 1])
        if not key or key in existing_keys:
            duplicates += 1
            continue
        existing_keys.add(key)
        append_rows.append(normalized)

    if append_rows:
        worksheet.append_rows(append_rows, value_input_option="USER_ENTERED")

    return len(append_rows), duplicates


def filing_url(cik: str, accession: str, primary_document: str = "") -> str:
    clean_accession = accession.replace("-", "")
    base = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{clean_accession}/"
    return base + primary_document if primary_document else base


def recent_filings(
    ticker: str,
    cik: str,
    submissions: dict[str, Any],
    retrieved_at: str,
) -> tuple[list[list[Any]], dict[str, str]]:
    recent = submissions.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    accession_to_url: dict[str, str] = {}
    rows: list[list[Any]] = []

    for index, form in enumerate(forms[:200]):
        accession = recent.get("accessionNumber", [""] * len(forms))[index]
        primary_doc = recent.get("primaryDocument", [""] * len(forms))[index]
        url = filing_url(cik, accession, primary_doc)
        accession_to_url[accession] = url

        if form not in RELEVANT_FORMS:
            continue

        description_list = recent.get("primaryDocDescription", [])
        description = description_list[index] if index < len(description_list) else ""

        rows.append(
            [
                retrieved_at,
                ticker,
                cik,
                "Filing",
                form,
                recent.get("filingDate", [""] * len(forms))[index],
                recent.get("reportDate", [""] * len(forms))[index],
                accession,
                description,
                "",
                "",
                "",
                "",
                url,
                f"SEC|{ticker}|{form}|{accession}",
            ]
        )

    return rows, accession_to_url


def valid_fact_entries(node: dict[str, Any]) -> tuple[str, list[dict[str, Any]]] | None:
    units = node.get("units", {})
    if not units:
        return None

    preferred = ("USD", "shares", "USD/shares")
    unit = next((candidate for candidate in preferred if candidate in units), None)
    if unit is None:
        unit = next(iter(units))

    entries = [
        item
        for item in units.get(unit, [])
        if item.get("form") in {"10-Q", "10-Q/A", "10-K", "10-K/A"}
        and item.get("val") is not None
        and item.get("end")
    ]

    # Keep the latest filed version for each reporting identity.
    deduplicated: dict[tuple[Any, ...], dict[str, Any]] = {}
    for item in entries:
        identity = (
            item.get("end"),
            item.get("form"),
            item.get("fy"),
            item.get("fp"),
            item.get("frame"),
        )
        previous = deduplicated.get(identity)
        if previous is None or str(item.get("filed", "")) > str(previous.get("filed", "")):
            deduplicated[identity] = item

    clean = sorted(
        deduplicated.values(),
        key=lambda item: (str(item.get("filed", "")), str(item.get("end", ""))),
        reverse=True,
    )
    return unit, clean


def select_fact(
    companyfacts: dict[str, Any],
    metric: str,
    concepts: list[str],
) -> FactResult | None:
    """Choose the newest valid fact across all equivalent US-GAAP concepts."""
    us_gaap = companyfacts.get("facts", {}).get("us-gaap", {})
    candidates: list[tuple[str, str, list[dict[str, Any]]]] = []

    for concept in concepts:
        node = us_gaap.get(concept)
        if not node:
            continue

        result = valid_fact_entries(node)
        if not result:
            continue

        unit, entries = result
        if entries:
            candidates.append((concept, unit, entries))

    if not candidates:
        return None

    # A filer can migrate between equivalent taxonomy concepts. Select the
    # concept whose best fact has the newest filing/end date, rather than the
    # first concept listed in the configuration.
    concept, unit, entries = max(
        candidates,
        key=lambda candidate: (
            str(candidate[2][0].get("filed", "")),
            str(candidate[2][0].get("end", "")),
        ),
    )
    current = entries[0]

    # Prefer a comparable prior period from the same selected concept.
    prior = next(
        (
            item
            for item in entries[1:]
            if item.get("end") != current.get("end")
            and item.get("fp") == current.get("fp")
            and item.get("form") == current.get("form")
        ),
        None,
    )
    if prior is None and len(entries) > 1:
        prior = entries[1]

    return FactResult(
        metric=metric,
        concept=concept,
        unit=unit,
        current=current,
        prior=prior,
    )



def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_iso_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def latest_financial_filing(submissions: dict[str, Any]) -> dict[str, str] | None:
    recent = submissions.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])

    for index, form in enumerate(forms):
        if form not in {"10-Q", "10-K"}:
            continue
        return {
            "form": form,
            "filing_date": recent.get("filingDate", [""] * len(forms))[index],
            "report_date": recent.get("reportDate", [""] * len(forms))[index],
            "accession": recent.get("accessionNumber", [""] * len(forms))[index],
            "primary_document": recent.get("primaryDocument", [""] * len(forms))[index],
        }
    return None


def numeric_xbrl_value(element: ET.Element) -> float | None:
    raw = (element.text or "").strip().replace(",", "")
    if not raw or raw in {"-", "—"}:
        return None

    negative = raw.startswith("(") and raw.endswith(")")
    if negative:
        raw = raw[1:-1]

    try:
        value = float(raw)
    except ValueError:
        return None

    scale = int(element.attrib.get("scale", "0") or "0")
    value *= 10 ** scale
    return -value if negative else value


def filing_specific_fact(
    sec: SecClient,
    cik: str,
    submissions: dict[str, Any],
    metric: str,
    concepts: list[str],
) -> FactResult | None:
    """Read the newest filing's extracted XBRL instance.

    This is a fallback for cases where SEC Company Facts omits or lags a
    current filer concept. It only accepts entity-wide, dimensionless facts.
    """
    filing = latest_financial_filing(submissions)
    if not filing:
        return None

    accession = filing["accession"]
    clean_accession = accession.replace("-", "")
    base = (
        f"https://www.sec.gov/Archives/edgar/data/"
        f"{int(cik)}/{clean_accession}/"
    )

    index_json = sec.get_json(base + "index.json")
    items = index_json.get("directory", {}).get("item", [])
    instance_name = next(
        (
            str(item.get("name", ""))
            for item in items
            if str(item.get("name", "")).endswith("_htm.xml")
        ),
        "",
    )
    if not instance_name:
        return None

    root = ET.fromstring(sec.get_text(base + instance_name))

    contexts: dict[str, dict[str, Any]] = {}
    for element in root.iter():
        if local_name(element.tag) != "context":
            continue

        context_id = element.attrib.get("id", "")
        start_value = None
        end_value = None
        has_dimensions = False

        for child in element.iter():
            name = local_name(child.tag)
            if name == "startDate":
                start_value = parse_iso_date(child.text)
            elif name == "endDate":
                end_value = parse_iso_date(child.text)
            elif name in {"explicitMember", "typedMember"}:
                has_dimensions = True

        if context_id and start_value and end_value:
            contexts[context_id] = {
                "start": start_value,
                "end": end_value,
                "has_dimensions": has_dimensions,
                "duration": (end_value - start_value).days + 1,
            }

    candidates: list[dict[str, Any]] = []
    concept_set = set(concepts)
    for element in root.iter():
        namespace = element.tag.split("}", 1)[0].lstrip("{") if "}" in element.tag else ""
        concept = local_name(element.tag)
        if concept not in concept_set or "fasb.org/us-gaap" not in namespace:
            continue

        context = contexts.get(element.attrib.get("contextRef", ""))
        if not context or context["has_dimensions"]:
            continue

        value = numeric_xbrl_value(element)
        if value is None:
            continue

        duration = int(context["duration"])
        target = 91 if filing["form"] == "10-Q" else 365
        tolerance = 35 if filing["form"] == "10-Q" else 45
        if abs(duration - target) > tolerance:
            continue

        candidates.append(
            {
                "concept": concept,
                "value": value,
                "start": context["start"],
                "end": context["end"],
                "duration": duration,
            }
        )

    if not candidates:
        return None

    report_date = parse_iso_date(filing["report_date"])
    current_pool = (
        [item for item in candidates if item["end"] == report_date]
        if report_date
        else candidates
    )
    if not current_pool:
        current_pool = candidates

    target_duration = 91 if filing["form"] == "10-Q" else 365
    current_fact = max(
        current_pool,
        key=lambda item: (
            item["end"],
            -abs(item["duration"] - target_duration),
            item["value"],
        ),
    )

    prior_pool = [
        item
        for item in candidates
        if item["end"] < current_fact["end"]
        and 330 <= (current_fact["end"] - item["end"]).days <= 400
        and abs(item["duration"] - current_fact["duration"]) <= 10
    ]
    prior_fact = (
        min(
            prior_pool,
            key=lambda item: abs(
                (current_fact["end"] - item["end"]).days - 364
            ),
        )
        if prior_pool
        else None
    )

    current = {
        "val": current_fact["value"],
        "form": filing["form"],
        "filed": filing["filing_date"],
        "end": current_fact["end"].isoformat(),
        "accn": accession,
        "fy": current_fact["end"].year,
        "fp": "Q" if filing["form"] == "10-Q" else "FY",
    }
    prior = (
        {
            "val": prior_fact["value"],
            "form": filing["form"],
            "filed": filing["filing_date"],
            "end": prior_fact["end"].isoformat(),
            "accn": accession,
            "fy": prior_fact["end"].year,
            "fp": current["fp"],
        }
        if prior_fact
        else None
    )

    return FactResult(
        metric=metric,
        concept=current_fact["concept"],
        unit="USD",
        current=current,
        prior=prior,
    )


def percent_change(current: Any, prior: Any) -> float | str:
    try:
        current_number = float(current)
        prior_number = float(prior)
        if prior_number == 0:
            return ""
        return current_number / prior_number - 1
    except (TypeError, ValueError):
        return ""


def metric_row(
    retrieved_at: str,
    ticker: str,
    cik: str,
    fact: FactResult,
    accession_urls: dict[str, str],
) -> list[Any]:
    current = fact.current
    prior = fact.prior
    accession = str(current.get("accn", ""))
    source_url = accession_urls.get(accession) or filing_url(cik, accession)

    return [
        retrieved_at,
        ticker,
        cik,
        "Metric",
        current.get("form", ""),
        current.get("filed", ""),
        current.get("end", ""),
        accession,
        fact.metric,
        fact.unit,
        current.get("val", ""),
        prior.get("val", "") if prior else "",
        percent_change(current.get("val"), prior.get("val") if prior else None),
        source_url,
        (
            f"SEC|{ticker}|{fact.metric}|{current.get('end', '')}|"
            f"{current.get('fy', '')}|{current.get('fp', '')}|{accession}"
        ),
    ]


def risk_status(direction: str, change: float | str) -> str:
    if direction == "Context" or change == "":
        return "Green"

    try:
        value = float(change)
    except (TypeError, ValueError):
        return "Green"

    if direction == "Higher worse":
        if value >= 0.20:
            return "Red"
        if value >= 0.10:
            return "Amber"

    if direction == "Lower worse":
        if value <= -0.20:
            return "Red"
        if value <= -0.10:
            return "Amber"

    return "Green"


def update_signal_rows(
    worksheet: gspread.Worksheet,
    metric_results: dict[str, tuple[FactResult, str]],
    retrieved_at: str,
) -> int:
    values = sheet_values(worksheet)
    updates: list[dict[str, Any]] = []

    for row_number, row in enumerate(values[1:], start=2):
        padded = pad(row, 15)
        signal_id = padded[0]
        result = metric_results.get(signal_id)
        if result is None:
            continue

        fact, source_url = result
        current = fact.current
        prior = fact.prior
        change = percent_change(
            current.get("val"),
            prior.get("val") if prior else None,
        )
        status = risk_status(str(padded[4]), change)

        # F:O, preserving Notes in O and leaving medium change/percentile blank.
        updates.append(
            {
                "range": f"F{row_number}:N{row_number}",
                "values": [
                    [
                        current.get("end", "") or current.get("filed", ""),
                        current.get("val", ""),
                        fact.unit,
                        change,
                        "",
                        "",
                        status,
                        retrieved_at,
                        source_url,
                    ]
                ],
            }
        )

    if updates:
        worksheet.batch_update(updates, value_input_option="USER_ENTERED")

    return len(updates)


def append_log(
    worksheet: gspread.Worksheet,
    status: str,
    updated: int,
    duplicates: int,
    failures: int,
    runtime: int,
    details: str,
) -> None:
    worksheet.append_row(
        [
            datetime.now(timezone.utc).isoformat(),
            "SEC",
            status,
            updated,
            duplicates,
            failures,
            runtime,
            details[:45000],
            "GitHub Actions",
        ],
        value_input_option="USER_ENTERED",
    )


def main() -> None:
    started = time.monotonic()
    retrieved_at = datetime.now(timezone.utc).isoformat()
    contact_email = os.environ.get("SEC_CONTACT_EMAIL", "")
    sec = SecClient(contact_email)
    google = google_client()
    spreadsheet = google.open_by_key(SPREADSHEET_ID)

    raw_ws = spreadsheet.worksheet(SEC_RAW_SHEET)
    signals_ws = spreadsheet.worksheet(SIGNALS_SHEET)
    log_ws = spreadsheet.worksheet(LOG_SHEET)

    raw_rows: list[list[Any]] = []
    metric_results: dict[str, tuple[FactResult, str]] = {}
    failures: list[str] = []

    for ticker, company in COMPANIES.items():
        cik = company["cik"]
        try:
            submissions = sec.get_json(
                f"https://data.sec.gov/submissions/CIK{cik}.json"
            )
            filing_rows, accession_urls = recent_filings(
                ticker, cik, submissions, retrieved_at
            )
            raw_rows.extend(filing_rows)

            companyfacts = sec.get_json(
                f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
            )

            for signal_id, definition in METRICS.items():
                if definition["ticker"] != ticker:
                    continue

                fact = select_fact(
                    companyfacts,
                    definition["metric"],
                    definition["concepts"],
                )

                # NVIDIA's current Revenue fact is present in the latest filing
                # but can lag or be omitted in the aggregated Company Facts feed.
                if signal_id == "SEC.NVDA.REVENUE":
                    filing_fact = filing_specific_fact(
                        sec,
                        cik,
                        submissions,
                        definition["metric"],
                        definition["concepts"],
                    )
                    if filing_fact is not None and (
                        fact is None
                        or str(filing_fact.current.get("end", ""))
                        > str(fact.current.get("end", ""))
                    ):
                        fact = filing_fact

                if fact is None:
                    failures.append(
                        f"{ticker}: no standardized fact found for "
                        f"{definition['metric']}"
                    )
                    continue

                row = metric_row(
                    retrieved_at,
                    ticker,
                    cik,
                    fact,
                    accession_urls,
                )
                raw_rows.append(row)
                metric_results[signal_id] = (fact, row[13])

        except Exception as exc:  # noqa: BLE001
            logging.exception("Failed processing %s", ticker)
            failures.append(f"{ticker}: {type(exc).__name__}: {exc}")

    added, duplicates = upsert_rows(
        raw_ws,
        raw_rows,
        key_column=15,
        width=15,
    )
    signals_updated = update_signal_rows(
        signals_ws,
        metric_results,
        retrieved_at,
    )

    runtime = round(time.monotonic() - started)
    status = "Success" if not failures else ("Partial" if added else "Failure")
    details = json.dumps(
        {
            "raw_rows_added": added,
            "duplicates_or_unchanged": duplicates,
            "signals_updated": signals_updated,
            "warnings": failures,
        },
        ensure_ascii=False,
    )
    append_log(
        log_ws,
        status,
        added + signals_updated,
        duplicates,
        len(failures),
        runtime,
        details,
    )

    print(details)
    if status == "Failure":
        raise RuntimeError(details)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()

"""Sole FT Game macro writer with preservation, freshness and verified logging.

FRED, EIA and CFTC are collected directly from their official public sources.
This coordinator is the sole raw-data writer; intake owns eligible Evidence.
"""
from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Toronto")
EPOCH = datetime(1899, 12, 30)
DATA_HEADERS = [
    "Captured", "Source", "Series / contract", "Label", "Observation date",
    "Value", "Units", "Frequency", "Measure", "Market / scope",
    "Original URL", "Observation key", "Vintage", "Verification", "Context",
]
LOG_HEADERS = [
    "Run at", "Source", "Status", "Series checked", "New rows",
    "Revised rows", "Failures", "Notes",
]


def parse_date(value):
    if isinstance(value, (float, int)):
        return (EPOCH + timedelta(days=value)).replace(tzinfo=TZ)
    if not value:
        raise ValueError("Missing date")
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        parsed = datetime.strptime(text, "%d/%m/%Y")
    return parsed.replace(tzinfo=TZ) if parsed.tzinfo is None else parsed.astimezone(TZ)


def serial(value):
    return (parse_date(value).replace(tzinfo=None) - EPOCH).total_seconds() / 86400


def normalize(row):
    normalized = list(row) + [""] * (15 - len(row))
    normalized = ["" if value is None else value for value in normalized[:15]]
    normalized[0] = serial(normalized[0])
    normalized[4] = serial(normalized[4])
    normalized[2] = (
        str(normalized[2]).zfill(6)
        if normalized[1] == "CFTC"
        else str(normalized[2])
    )
    normalized[5] = float(normalized[5])
    if not math.isfinite(normalized[5]):
        raise ValueError("Invalid numeric observation")
    if (
        not normalized[6]
        or not normalized[8]
        or not str(normalized[10]).startswith("https://")
    ):
        raise ValueError("Missing units, measure or original URL")
    observation_date = parse_date(normalized[4]).date().isoformat()
    normalized[11] = ":".join(
        [str(normalized[1]), normalized[2], observation_date, str(normalized[8])]
    )
    if normalized[13] not in ("Verified data", "Needs verification"):
        normalized[14] = (
            str(normalized[14]) + "; prior provenance: " + str(normalized[13])
        )
        normalized[13] = "Needs verification"
    return normalized


def merge_rows(existing, incoming, expected, now):
    merged, order, errors = {}, [], []
    for raw in existing:
        if not raw or not any(value not in ("", None) for value in raw):
            continue
        row = normalize(raw)
        if row[11] in merged:
            raise ValueError("Duplicate existing observation key")
        merged[row[11]] = row
        order.append(row[11])

    added = revised = 0
    refreshed = set()
    for raw in incoming:
        try:
            row = normalize(raw)
            group = (row[1], row[2])
            if (
                group not in expected
                or parse_date(row[4]).date() > now.date()
                or parse_date(row[0]) > now + timedelta(minutes=5)
            ):
                raise ValueError("Unexpected series or future date")
            old = merged.get(row[11])
            if old is None:
                order.append(row[11])
                added += 1
            elif old[1:] != row[1:]:
                # A newer retrieval timestamp alone is not a data revision.
                revised += 1
            merged[row[11]] = row
            refreshed.add(group)
        except (ValueError, TypeError, OverflowError):
            errors.append("A source row failed identity/date/numeric validation")

    for group in sorted(expected - refreshed):
        errors.append(
            ":".join(group) + ": refresh missing; prior observations retained where available"
        )
    return [merged[key] for key in order], added, revised, refreshed, errors


def diagnostics(rows, expected, now):
    details, errors = {}, []
    for group in sorted(expected):
        found = [row for row in rows if (row[1], row[2]) == group]
        key = ":".join(group)
        if not found:
            details[key] = {"status": "Missing"}
            errors.append(key + ": missing observation")
            continue
        latest = max(found, key=lambda row: (row[4], row[0]))
        observation_age = (now.date() - parse_date(latest[4]).date()).days
        retrieval_age = round((now - parse_date(latest[0])).total_seconds() / 3600, 1)
        frequency = str(latest[7]).lower()
        observation_limit = (
            150 if frequency in ("q", "quarterly")
            else 45 if frequency in ("m", "monthly")
            else 14 if frequency in ("w", "weekly")
            else 7
        )
        # Retrieval freshness must respect the publication cadence. A weekly
        # EIA/CFTC observation can be perfectly healthy several days after the
        # last upstream retrieval, while daily series need a tighter guard.
        retrieval_limit_hours = (
            2400 if frequency in ("q", "quarterly")
            else 840 if frequency in ("m", "monthly")
            else 192 if frequency in ("w", "weekly")
            else 96
        )
        unverified = latest[13] != "Verified data"
        # Weekly releases have a known normal publication cadence. Age alone
        # can otherwise make a missed new release look fresh for two weeks.
        expected_date = None
        if group[0] == "CFTC":
            release = now.date() - timedelta(days=(now.weekday() - 4) % 7)
            if now.weekday() == 4 and (now.hour, now.minute) < (15, 30):
                release -= timedelta(days=7)
            expected_date = release - timedelta(days=3)
        elif group == ("EIA", "NG.NW2_EPG0_SWO_R48_BCF.W"):
            release = now.date() - timedelta(days=(now.weekday() - 3) % 7)
            if now.weekday() == 3 and (now.hour, now.minute) < (10, 30):
                release -= timedelta(days=7)
            expected_date = release - timedelta(days=6)
        overdue = expected_date is not None and parse_date(latest[4]).date() < expected_date
        stale = (
            observation_age > observation_limit
            or retrieval_age > retrieval_limit_hours
            or overdue
        )
        details[key] = {
            "observation": parse_date(latest[4]).date().isoformat(),
            "age_days": observation_age,
            "retrieval_age_hours": retrieval_age,
            "verification": latest[13],
            "normal_latest_due": expected_date.isoformat() if expected_date else None,
            "status": "Unverified" if unverified else "Stale" if stale else "Current",
        }
        if unverified:
            errors.append(key + ": latest observation is not independently verified")
        if stale:
            errors.append(key + ": stale observation/retrieval or normal release overdue; verify any official delay")
    return details, errors


def cell(value):
    if isinstance(value, (int, float)):
        return {"userEnteredValue": {"numberValue": value}}
    return {"userEnteredValue": {"stringValue": str(value)}}


def update(sheet_id, start_row, start_col, rows):
    width = len(rows[0])
    return {
        "updateCells": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": start_row,
                "endRowIndex": start_row + len(rows),
                "startColumnIndex": start_col,
                "endColumnIndex": start_col + width,
            },
            "rows": [{"values": [cell(value) for value in row]} for row in rows],
            "fields": "userEnteredValue",
        }
    }


def first_contiguous_empty_log_row(ws):
    """Return the first blank run-log row after R7, ignoring stray cells far below."""
    values = ws.get(
        f"R8:R{ws.row_count}", value_render_option="UNFORMATTED_VALUE"
    )
    available = max(ws.row_count - 7, 0)
    for offset in range(available):
        row = values[offset] if offset < len(values) else []
        if not row or row[0] in ("", None):
            return 8 + offset
    return ws.row_count + 1


def write_verified(book, ws, rows, log, log_row):
    meta = book.fetch_sheet_metadata(
        params={"fields": "sheets(properties(sheetId,title),tables(tableId,name,range))"}
    )
    sheet = next(
        sheet
        for sheet in meta["sheets"]
        if sheet["properties"]["sheetId"] == ws.id
    )
    tables = {table["name"]: table for table in sheet.get("tables", [])}
    required = {"FTMacroObservations", "FTMacroRunLog"}
    if not required <= tables.keys():
        raise ValueError("Missing native macro tables")

    requests = []
    needed = max(7 + len(rows), log_row)
    if needed > ws.row_count:
        requests.append(
            {
                "appendDimension": {
                    "sheetId": ws.id,
                    "dimension": "ROWS",
                    "length": needed - ws.row_count + 50,
                }
            }
        )
    if rows:
        requests.append(update(ws.id, 7, 0, rows))
        for column, kind, pattern in ((0, "DATE_TIME", "dd/MM/yyyy HH:mm:ss"), (4, "DATE", "dd/MM/yyyy")):
            requests.append({"repeatCell": {
                "range": {"sheetId": ws.id, "startRowIndex": 7, "endRowIndex": 7 + len(rows),
                          "startColumnIndex": column, "endColumnIndex": column + 1},
                "cell": {"userEnteredFormat": {"numberFormat": {"type": kind, "pattern": pattern}}},
                "fields": "userEnteredFormat.numberFormat",
            }})

    # A provisional record cannot claim completion before sheet readback succeeds.
    pending = list(log)
    pending[2] = "Partial"
    pending[7] = "Write committed; readback pending. " + str(log[7])
    requests.append(update(ws.id, log_row - 1, 17, [pending]))
    requests.append({"repeatCell": {
        "range": {"sheetId": ws.id, "startRowIndex": log_row - 1, "endRowIndex": log_row,
                  "startColumnIndex": 17, "endColumnIndex": 18},
        "cell": {"userEnteredFormat": {"numberFormat": {"type": "DATE_TIME", "pattern": "dd/MM/yyyy HH:mm:ss"}}},
        "fields": "userEnteredFormat.numberFormat",
    }})

    for name, end_row in (
        ("FTMacroObservations", 7 + len(rows)),
        ("FTMacroRunLog", log_row),
    ):
        table = tables[name]
        region = dict(table["range"])
        minimum_end = int(region.get("startRowIndex", 0)) + 1
        # Set the exact active range so an earlier stray row cannot keep the table huge.
        region["endRowIndex"] = max(minimum_end, end_row)
        requests.append(
            {
                "updateTable": {
                    "table": {"tableId": table["tableId"], "range": region},
                    "fields": "range",
                }
            }
        )

    book.batch_update({"requests": requests})

    if rows:
        got = ws.get(
            f"A8:O{7 + len(rows)}", value_render_option="UNFORMATTED_VALUE"
        )
        if [normalize(row) for row in got] != rows:
            raise RuntimeError(
                "Macro observation readback mismatch; provisional log remains Partial"
            )

    book.batch_update({"requests": [update(ws.id, log_row - 1, 17, [log])]})
    got = ws.get(
        f"R{log_row}:Y{log_row}", value_render_option="UNFORMATTED_VALUE"
    )
    if not got or got[0] != log:
        raise RuntimeError("Macro run log readback mismatch")


def main():
    import ft_macro_pipeline as pipeline
    from concurrent.futures import ThreadPoolExecutor

    now = datetime.now(TZ)
    if now > pipeline.GAME_END:
        print(json.dumps({"status": "Stopped", "reason": "FT game ended"}))
        return 0

    book = pipeline.google_client().open_by_key(pipeline.SPREADSHEET_ID)
    ws = book.worksheet(pipeline.MACRO_SHEET)
    if ws.get("A7:O7")[0] != DATA_HEADERS or ws.get("R7:Y7")[0] != LOG_HEADERS:
        raise RuntimeError("Macro destination headers mismatch")

    existing = ws.get(
        f"A8:O{ws.row_count}", value_render_option="UNFORMATTED_VALUE"
    )
    expected = (
        {("FRED", series) for series in pipeline.FRED_SERIES}
        | {("EIA", series) for series, meta in pipeline.EIA_SERIES.items() if meta[4]}
        | {
            ("CFTC", series)
            for series in list(pipeline.CFTC_COMMODITIES) + list(pipeline.CFTC_TFF)
        }
    )

    incoming, errors = [], []
    # Independent source routes run together; bounded HTTP retries ensure even
    # an unavailable source leaves time to commit an honest Partial/Failed log.
    with ThreadPoolExecutor(max_workers=3) as pool:
        jobs = [(source, pool.submit(collector, now.isoformat())) for source, collector in (
            ("FRED", pipeline.fred_rows),
            ("EIA", pipeline.eia_rows),
            ("CFTC", pipeline.cftc_rows),
        )]
        for source, job in jobs:
            try:
                result = job.result()
                incoming.extend(result[0])
                errors.extend(result[1])
                print(json.dumps({"source": source, "rows": len(result[0]), "failures": len(result[1])}), flush=True)
            except Exception as exc:
                errors.append(source + ": collection failed (" + type(exc).__name__ + ")")

    rows, added, revised, refreshed, merge_errors = merge_rows(
        existing, incoming, expected, now
    )
    errors.extend(merge_errors)
    freshness, age_errors = diagnostics(rows, expected, now)
    errors = list(dict.fromkeys(errors + age_errors))
    status = "Failed" if not refreshed else "Partial" if errors else "Complete"

    notes = json.dumps(
        {
            "errors": errors,
            "series": freshness,
            "preserved_series": [
                ":".join(group) for group in sorted(expected - refreshed)
            ],
            "rows_retained": len(rows),
            "readback": "verified before final status",
            "workflow_semantics": "Partial is a successful degraded run; only Failed or write/readback errors fail the workflow.",
            "upstream": "Direct official FRED series pages, EIA exact history pages and CFTC combined reports; FT workbook only. Other macro writers remain inactive.",
        },
        ensure_ascii=False,
    )
    log = [
        serial(now.isoformat()),
        "FRED / EIA / CFTC coordinator",
        status,
        len(expected),
        added,
        revised,
        len(errors),
        notes,
    ]
    log_row = first_contiguous_empty_log_row(ws)
    write_verified(book, ws, rows, log, log_row)

    print(
        json.dumps(
            {
                "status": status,
                "checked": len(expected),
                "added": added,
                "revised": revised,
                "rows_retained": len(rows),
                "log_row": log_row,
                "errors": errors,
                "readback": "verified",
            }
        )
    )
    # A Partial run has delivered a verified, last-good-preserving snapshot and
    # should remain visible as Partial in the sheet without masquerading as a
    # GitHub Actions failure. Failed or thrown integrity errors still fail CI.
    return 1 if status == "Failed" else 0


if __name__ == "__main__":
    sys.exit(main())

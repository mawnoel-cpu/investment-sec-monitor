"""Audit real persisted FT outputs. Never create research or investment decisions.

This runs independently in Actions, including after a collector failure. It
records exact broken cells and makes a missing/Started-only write fail visibly.
"""
from collections import Counter
from datetime import datetime, time
import json
import os
import re

import ft_price_writer as prices


def value(row, i, default=""):
    return row[i] if len(row) > i else default


def evaluate(now, intake, reviews, snapshots, required, verified):
    day = int(prices.serial(now))
    faults = []
    latest = max(enumerate(intake, 46), key=lambda x: value(x[1], 0, 0) or 0, default=(46, []))
    n, row = latest
    if not row or value(row, 0, 0) < day:
        research = "PENDING" if now.time() < time(13, 40) else "MISSING"
    elif value(row, 1) == "Failed":
        research = "FAILED"
    elif str(value(row, 7)).lower().startswith("started"):
        research = "STARTED" if now.time() < time(13, 40) else "STALLED"
    elif value(row, 1) in {"Complete", "Partial"}:
        research = "SAVED " + value(row, 1).upper()
    else:
        research = "INVALID"
    if research in {"MISSING", "FAILED", "STALLED", "INVALID"}:
        faults.append(f"Sources!A{n}:H{n}: research {research}; expected finalized write by13:40 Toronto")
    key = "Daily:" + now.date().isoformat()
    matches = [(n, r) for n, r in enumerate(reviews, 8) if value(r, 9) == key]
    saved = [r for r in snapshots if value(r, 1) == key]
    counts = Counter(value(r, 2) for r in saved)
    missing = sorted(t for t in required if counts[t] != 1)
    complete = False
    review_state = "PENDING"
    location = "Reviews!A8:J1001 / Decision History!A5:S1000"
    if len(matches) == 1:
        n, row = matches[0]
        location = f"Reviews!A{n}:J{n} / Decision History!A5:S1000"
        stamp, asof = value(row, 0, 0), value(row, 2, 0)
        decision = str(value(row, 5))
        dates_ok = isinstance(stamp, (int, float)) and isinstance(asof, (int, float)) and day <= stamp <= prices.serial(now) and day <= asof <= prices.serial(now)
        complete = dates_ok and bool(decision) and not re.match(r"(?i)^(started|pending|failed|incomplete|blocked|in progress)", decision) and not missing
        if complete and isinstance(verified, (int, float)) and asof < verified:
            review_state = "NEW INPUTS"
            complete = False
        elif complete:
            review_state = "SAVED"
            if now.time() >= time(14, 45) and stamp < day + (14 * 60 + 20) / 1440:
                review_state = "PREWINDOW REFRESH MISSING"
                complete = False
        else:
            review_state = "INCOMPLETE"
    elif len(matches) > 1:
        review_state = "DUPLICATE"
    if not complete and now.time() >= time(14, 45):
        faults.append(f"{location}: review {review_state}; missing/duplicate snapshots {','.join(missing) or 'none'}")
    return research, review_state, faults


def main(now=None):
    now = now or datetime.now(prices.TORONTO)
    if now.date() > prices.date(2026, 11, 27) or now.weekday() >= 5:
        print(json.dumps({"status": "Skipped", "reason": "outside FT weekday horizon"}))
        return
    book = prices.auth_book()
    tracked = prices.tracking(book)
    archive = book.worksheet("DAILY CLOSES")
    data = archive.get(f"A5:J{prices.DATA_LAST_ROW}", value_render_option="UNFORMATTED_VALUE")
    faults = []
    for ticker, gs in tracked.items():
        rows = [(n, r) for n, r in enumerate(data, 5) if value(r, 1) == ticker and value(r, 8) == "VERIFIED"]
        expected = prices.last_completed(now, *prices.venue_info(ticker, gs)[2:])
        sessions = {value(r, 0) for _, r in rows}
        latest = max(sessions, default=0)
        if latest != prices.serial(expected) or len(sessions) < 21:
            loc = f"DAILY CLOSES!A{rows[-1][0]}:J{rows[-1][0]}" if rows else "DAILY CLOSES!A5:J12000"
            faults.append(f"{loc}: {ticker} expected {expected}, latest serial {latest}, {len(sessions)} sessions")
    logs = archive.get("L5:P200", value_render_option="UNFORMATTED_VALUE")
    latest = max((r for r in logs if r), key=lambda r: value(r, 0, 0), default=[])
    if value(latest, 2) != "Complete":
        faults.append("DAILY CLOSES!L5:P200: latest price run not Complete")
    if now.time() >= time(14, 15) and value(latest, 0, 0) < int(prices.serial(now)) + (13*60+50)/1440:
        faults.append("DAILY CLOSES!L5:P200: fresh13:50 pretrade receipt missing")
    price_state = "FAILED" if faults else "SAVED"
    portfolio = book.worksheet("Portfolio").get("A8:D19", value_render_option="UNFORMATTED_VALUE")
    required = {r[0] for r in portfolio if len(r) >= 3 and isinstance(r[2], (int, float)) and r[2] > 0}
    required.update(r[0] for r in book.worksheet("ROTATION BENCH").get("C5:C15") if r and r[0])
    source = book.worksheet("Sources")
    b39 = source.get("B39", value_render_option="UNFORMATTED_VALUE")
    verified = b39[0][0] if b39 and b39[0] else 0
    research, review, issues = evaluate(now, source.get("A46:H1000", value_render_option="UNFORMATTED_VALUE"), book.worksheet("Reviews").get("A8:J1001", value_render_option="UNFORMATTED_VALUE"), book.worksheet("Decision History").get("A5:S1000", value_render_option="UNFORMATTED_VALUE"), required, verified)
    faults.extend(issues)
    health = book.worksheet("BACKEND HEALTH")
    if health.row_count < 1000:
        health.resize(rows=1000)
    health.update(range_name="A43:I43", values=[["CHECKED AT", "PRICE WRITE", "RESEARCH WRITE", "REVIEW WRITE", "PRICE SYMBOLS", "REQUIRED SNAPSHOTS", "EXACT FAULT / COVERAGE", "GITHUB RUN", "COMMIT"]], value_input_option="RAW")
    rows = health.get("A44:I1000", value_render_option="UNFORMATTED_VALUE")
    dest = 44 + len(rows)
    link = "https://github.com/mawnoel-cpu/investment-sec-monitor/actions/runs/" + os.getenv("GITHUB_RUN_ID", "local")
    details = " | ".join(faults) or ("Required outputs saved; broader research remains Partial" if research.endswith("PARTIAL") else "No currently due persistence faults")
    record = [prices.serial(now), price_state, research, review, len(tracked), len(required), details[:48000], link, os.getenv("GITHUB_SHA", "local")]
    health.update(range_name=f"A{dest}:I{dest}", values=[record], value_input_option="RAW")
    if not prices.rows_match(health.get(f"A{dest}:I{dest}", value_render_option="UNFORMATTED_VALUE"), [record]):
        raise RuntimeError(f"BACKEND HEALTH!A{dest}:I{dest}: audit readback mismatch")
    desk = book.worksheet("TRADE DESK")
    note = f"Persistence audit BACKEND HEALTH!A{dest}:I{dest}, {now.isoformat()}. " + details
    book.batch_update({"requests": [{"updateCells": {"range": {"sheetId": desk.id, "startRowIndex": 12, "endRowIndex": 13, "startColumnIndex": 1, "endColumnIndex": 2}, "rows": [{"values": [{"note": note}]}], "fields": "note"}}, {"repeatCell": {"range": {"sheetId": health.id, "startRowIndex": dest-1, "endRowIndex": dest, "startColumnIndex": 0, "endColumnIndex": 1}, "cell": {"userEnteredFormat": {"numberFormat": {"type": "DATE_TIME", "pattern": "dd mmm yyyy HH:mm"}}}, "fields": "userEnteredFormat.numberFormat"}}]})
    print(json.dumps({"status": "Failed" if faults else "Verified", "prices": price_state, "research": research, "review": review, "audit_row": dest, "faults": faults, "readback": "verified"}))
    if faults:
        raise RuntimeError("FT persistence audit failed; see exact workbook cells above")


if __name__ == "__main__":
    main()

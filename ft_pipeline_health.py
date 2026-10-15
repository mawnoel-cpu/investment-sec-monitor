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
    elif value(row, 1) in {"Complete", "Partial"} and isinstance(value(row, 8), (int, float)) and value(row, 0) <= value(row, 8) <= prices.serial(now):
        research = "SAVED " + value(row, 1).upper()
    else:
        research = "IN PROGRESS" if now.time() < time(13, 40) else "UNFINALIZED"
    if research in {"MISSING", "FAILED", "STALLED", "UNFINALIZED"}:
        faults.append(f"Sources!A{n}:I{n}: research {research}; expected numeric Completed at in I{n} by13:40 Toronto")
    key = "Daily:" + now.date().isoformat()
    matches = [(n, r) for n, r in enumerate(reviews, 8) if value(r, 9) == key]
    saved = [r for r in snapshots if value(r, 1) == key]
    counts = Counter(value(r, 2) for r in saved)
    missing = sorted(t for t in required if counts[t] != 1)
    invalid = sorted(value(r, 2) for r in saved if value(r, 2) in required and
                     (not isinstance(value(r, 0), (int, float)) or
                      not day <= value(r, 0) <= prices.serial(now) or
                      not value(r, 10) or not value(r, 18)))
    complete = False
    review_state = "PENDING"
    location = "Reviews!A8:J1001 / Decision History!A5:S1000"
    if len(matches) == 1:
        n, row = matches[0]
        location = f"Reviews!A{n}:J{n} / Decision History!A5:S1000"
        stamp, asof = value(row, 0, 0), value(row, 2, 0)
        decision = str(value(row, 5))
        dates_ok = isinstance(stamp, (int, float)) and isinstance(asof, (int, float)) and day <= stamp <= prices.serial(now) and day <= asof <= prices.serial(now)
        complete = dates_ok and bool(decision) and not re.match(r"(?i)^(started|pending|failed|incomplete|blocked|in progress)", decision) and not missing and not invalid
        # A morning-only review cannot pass the afternoon decision check,
        # even when new evidence arrived after the trading cutoff.
        if complete and now.time() >= time(14, 45) and stamp < day + (14 * 60 + 20) / 1440:
            review_state = "PREWINDOW REFRESH MISSING"
            complete = False
        elif complete and isinstance(verified, (int, float)) and asof < verified:
            cutoff = day + (15 * 60 + 15) / 1440
            if now.time() >= time(15, 15) and verified >= cutoff:
                # Later research must be assessed at the next eligible trade
                # window. Preserve today's frozen review and the independent
                # historical prewindow-delivery warning (BACKEND HEALTH!B37).
                review_state = "NEXT-DAY INPUTS"
            else:
                review_state = "NEW INPUTS"
                complete = False
        elif complete:
            review_state = "SAVED"
        else:
            review_state = "INCOMPLETE"
    elif len(matches) > 1:
        review_state = "DUPLICATE"
    if not complete and now.time() >= time(14, 45):
        faults.append(f"{location}: review {review_state}; missing/duplicate snapshots {','.join(missing) or 'none'}; invalid timestamp/action/coverage {','.join(invalid) or 'none'}")
    return research, review_state, faults


def audit_phase(stamp):
    """One durable receipt for each trading-day checkpoint; no duplicate chatter.

    The append-only history preserves research/decision deadlines and one EOD
    recovery checkpoint. Any changed fault/state creates a new receipt even in
    the same checkpoint. GitHub run IDs do not count as a state change.
    """
    try:
        serial = float(stamp)
        minute = int(round((serial - int(serial)) * 1440))
    except (TypeError, ValueError, OverflowError):
        return "UNKNOWN"
    if minute < 13 * 60 + 40:
        return "PRE_RESEARCH"
    if minute < 14 * 60 + 45:
        return "RESEARCH_DUE"
    if minute < 15 * 60 + 15:
        return "DECISION_DUE"
    if minute < 18 * 60:
        return "AFTER_WINDOW"
    return "CLOSING"


def audit_signature(record):
    """Ignore per-run metadata; retain any exact issue/cell/coverage changes."""
    if not record or not isinstance(value(record, 0), (int, float)):
        return None
    return (int(value(record, 0)), audit_phase(value(record, 0)),
            *[str(value(record, n)) for n in range(1, 7)])


def should_append_audit(previous, proposed):
    return audit_signature(previous) != audit_signature(proposed)


def system_state_text(faults, research, review, delivery):
    """One explicit status, written only when the state/checkpoint changes."""
    if faults:
        return "SYSTEM — ⚠ BACKEND: SEE CELL NOTE"
    if "MISSED" in str(delivery):
        return "SYSTEM — ⚠ BACKEND: PREWINDOW DELIVERY MISSED"
    if research.endswith("PARTIAL") or review == "NEXT-DAY INPUTS":
        return "SYSTEM — CHECKED; RESEARCH PARTIAL / NEXT-DAY INPUTS"
    return "SYSTEM — VERIFIED: REQUIRED WRITES"


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
    research, review, issues = evaluate(now, source.get("A46:I1000", value_render_option="UNFORMATTED_VALUE"), book.worksheet("Reviews").get("A8:J1001", value_render_option="UNFORMATTED_VALUE"), book.worksheet("Decision History").get("A5:S1000", value_render_option="UNFORMATTED_VALUE"), required, verified)
    faults.extend(issues)
    health = book.worksheet("BACKEND HEALTH")
    if health.row_count < 1000:
        health.resize(rows=1000)
    health.update(range_name="A43:I43", values=[["CHECKED AT", "PRICE WRITE", "RESEARCH WRITE", "REVIEW WRITE", "PRICE SYMBOLS", "REQUIRED SNAPSHOTS", "EXACT FAULT / COVERAGE", "GITHUB RUN", "COMMIT"]], value_input_option="RAW")
    rows = health.get("A44:I1000", value_render_option="UNFORMATTED_VALUE")
    filled = [(44 + i, r) for i, r in enumerate(rows) if r and value(r, 0)]
    previous_row, previous = filled[-1] if filled else (None, [])
    dest = previous_row + 1 if previous_row is not None else 44
    link = "https://github.com/mawnoel-cpu/investment-sec-monitor/actions/runs/" + os.getenv("GITHUB_RUN_ID", "local")
    all_saved = price_state == "SAVED" and research.startswith("SAVED ") and review in {"SAVED", "NEXT-DAY INPUTS"}
    if faults:
        details = " | ".join(faults)
    elif all_saved:
        details = "All required writes verified"
        if research.endswith("PARTIAL"):
            details += "; broader research remains Partial"
        if review == "NEXT-DAY INPUTS":
            details += "; post-cutoff research queued for next trade-day review"
    else:
        details = "Waiting for unfinished stages; not yet due. This is not an end-to-end completion receipt."
    window = health.get("B37", value_render_option="FORMATTED_VALUE")
    delivery = window[0][0] if window and window[0] else "UNVERIFIED"
    if "MISSED" in delivery:
        details += " | Prewindow delivery MISSED: BACKEND HEALTH!B37, Sources!I46:I1000, Reviews!A8:J1001, DAILY CLOSES!L5:P200. Current recovery does not backdate the missed window."
    record = [prices.serial(now), price_state, research, review, len(tracked), len(required), details[:48000], link, os.getenv("GITHUB_SHA", "local")]
    changed = should_append_audit(previous, record)
    if changed:
        if dest > 1000:
            raise RuntimeError("BACKEND HEALTH!A44:I1000 full; cannot certify audit")
        health.update(range_name=f"A{dest}:I{dest}", values=[record], value_input_option="RAW")
        if not prices.rows_match(health.get(f"A{dest}:I{dest}", value_render_option="UNFORMATTED_VALUE"), [record]):
            raise RuntimeError(f"BACKEND HEALTH!A{dest}:I{dest}: audit readback mismatch")

        # GitHub is the sole backend-display writer. Keep A/B only, never
        # replace holdings or orders. Colour actual faults red and recoveries
        # neutral, while preserving a specific comment linking to exact cells.
        desk = book.worksheet("TRADE DESK")
        note = f"Persistence audit BACKEND HEALTH!A{dest}:I{dest}, {now.isoformat()}. " + details
        warning = bool(faults) or "MISSED" in str(delivery)
        background = {"red": 0.98, "green": 0.84, "blue": 0.84} if warning else {"red": 0.93, "green": 0.95, "blue": 0.97}
        desk_text = system_state_text(faults, research, review, delivery)
        stamp = now.strftime("%d %b %H:%M")
        book.batch_update({"requests": [
            {"updateCells": {"range": {"sheetId": desk.id, "startRowIndex": 12, "endRowIndex": 13, "startColumnIndex": 0, "endColumnIndex": 2},
                "rows": [{"values": [
                    {"userEnteredValue": {"stringValue": stamp}},
                    {"userEnteredValue": {"stringValue": desk_text}, "note": note,
                     "userEnteredFormat": {"backgroundColor": background}}
                ]}],
                "fields": "userEnteredValue,note,userEnteredFormat.backgroundColor"}},
            {"repeatCell": {"range": {"sheetId": health.id, "startRowIndex": dest-1, "endRowIndex": dest, "startColumnIndex": 0, "endColumnIndex": 1},
                "cell": {"userEnteredFormat": {"numberFormat": {"type": "DATE_TIME", "pattern": "dd mmm yyyy HH:mm"}}},
                "fields": "userEnteredFormat.numberFormat"}
        ]})
        actual = desk.get("A13:B13", value_render_option="FORMATTED_VALUE")
        if not actual or actual[0][:2] != [stamp, desk_text]:
            raise RuntimeError("TRADE DESK!A13:B13: backend status readback mismatch")
        audit_row = dest
    else:
        # Audit stays visible without another duplicate row/comment. A healthy
        # Actions exit requires zero faults; a persistent fault still fails.
        audit_row = previous_row
    status = ("Failed" if faults else "Recovered late" if all_saved and "MISSED" in str(delivery)
              else "Verified" if all_saved else "Waiting")
    print(json.dumps({"status": status, "prices": price_state, "research": research,
        "review": review, "prewindow": delivery, "audit_row": audit_row,
        "audit_change": "saved" if changed else "unchanged; existing verified receipt",
        "faults": faults, "readback": "verified" if changed else "previous certified receipt"}))
    if faults:
        raise RuntimeError("FT persistence audit failed; see exact workbook cells above")


if __name__ == "__main__":
    main()

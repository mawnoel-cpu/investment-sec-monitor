"""Audit real persisted FT outputs. Never create research or investment decisions.

This runs independently in Actions, including after a collector failure. It
records exact broken cells and makes a missing/Started-only write fail visibly.
"""
from collections import Counter
from datetime import datetime, time, timedelta
import json
import os
import re

import ft_price_writer as prices
import ft_financial_stress as finance
import ft_stress_panel as stress


def value(row, i, default=""):
    return row[i] if len(row) > i else default


def decision_clock(now):
    """Weekend/holiday diagnostics audit the last required business-day window."""
    day = now.date()
    while day.weekday() >= 5 or day in prices.US_HOLIDAYS:
        day -= timedelta(days=1)
    return now if day == now.date() else datetime.combine(day, time(23,59,59), tzinfo=prices.TORONTO)


def review_completion(row):
    # N is the explicit read-back completion. Legacy rows use C (as-of), never
    # A (start). If a modern row has a missing N, it must not use a fallback.
    return value(row,13) if len(row)>10 else value(row,2)


# Reviews O:Z are optional telemetry for new decision runs. Older frozen
# reviews intentionally retain their legacy schema and audit semantics.
CHECKPOINT_STAGES = (
    ("PORTFOLIO", 14, 15),
    ("EVIDENCE", 16, 17),
    ("ANALYSIS", 18, 19),
    ("PERSIST", 20, 21),
    ("READBACK", 22, 23),
)
CHECKPOINT_ALLOWED = {"PASS", "PARTIAL", "FAILED", "BLOCKED"}


def read_reviews(book):
    """Read the receipt AND persisted stage diagnostics, without truncating O:Z."""
    sheet = book.worksheet("Reviews")
    return sheet.get(f"A8:Z{sheet.row_count}", value_render_option="UNFORMATTED_VALUE")


def has_checkpoints(row):
    return any(value(row, index) not in ("", None) for index in range(14, 25))


def checkpoint_complete(row, now):
    if not has_checkpoints(row):
        return True  # Legacy historical rows are not retroactively invalidated.
    start = value(row, 0)
    completion = review_completion(row)
    if not stress.numeric(start) or not stress.numeric(completion):
        return False
    previous = start
    for _, time_col, result_col in CHECKPOINT_STAGES:
        stamp, result = value(row, time_col), value(row, result_col)
        if not stress.numeric(stamp) or result != "PASS" or not previous <= stamp <= completion <= prices.serial(now):
            return False
        previous = stamp
    return True


def checkpoint_diagnostic(row):
    """Report the last *persisted* stage; no invented reason for interruption."""
    if not has_checkpoints(row):
        return ""
    last = "NONE"
    next_stage = ""
    previous = value(row, 0)
    issues = []
    first_nonpass = ""
    for name, time_col, result_col in CHECKPOINT_STAGES:
        stamp, status = value(row, time_col), value(row, result_col)
        if not stress.numeric(stamp) and not status:
            next_stage = name
            break
        if not stress.numeric(stamp):
            issues.append(name + " timestamp invalid")
        elif stress.numeric(previous) and stamp < previous:
            issues.append(name + " timestamp out of order")
        else:
            previous = stamp
        if status not in CHECKPOINT_ALLOWED:
            issues.append(name + " result missing/invalid")
        if status != "PASS" and not first_nonpass:
            first_nonpass = name + "=" + (str(status) if status else "UNRECORDED")
        last = name + "=" + (str(status) if status else "UNRECORDED")
    elapsed = ""
    if stress.numeric(value(row, 0)) and stress.numeric(previous):
        elapsed = f"; observed {max(0, round((previous - row[0]) * 86400))}s from start"
    reason = str(value(row, 24))[:280]
    return f"checkpoint last {last}; next {next_stage or 'none'}{elapsed}" + (
        "; first unfinished stage " + first_nonpass if first_nonpass else "") + (
        "; " + ",".join(issues) if issues else "") + (
        "; recorded blocker: " + reason if reason else "")


def scheduled_intake(row):
    stamp=value(row,0)
    if not stress.numeric(stamp):
        return False
    note=str(value(row,7)).lower()
    if any(word in note for word in ("audit:","manual", "supplemental", "additional", "recovery")):
        return False
    # A manual early audit does not replace the 10:45 scheduled owner run.
    if stamp-int(stamp) < (10*60+30)/1440:
        return False
    if int(stamp) >= int(prices.serial(prices.date(2026,10,26))):
        return ("scheduledintake:" + prices.day_of(stamp).isoformat()) in note
    return True


def required_symbols(portfolio, bench, reviews, now):
    required={r[0] for r in portfolio if stress.numeric(value(r,2)) and r[2]>0}
    required.update(r[0] for r in bench if r and r[0])
    # Research candidates named by the review are required in addition to the
    # baseline active-bench set, not an alternative smaller denominator.
    key="Daily:"+now.date().isoformat()
    for row in reviews:
        if value(row,9)==key and value(row,12):
            try:
                required.update(json.loads(row[12]).get("candidates",[]))
            except (ValueError,TypeError,AttributeError):
                pass
    return required


def evaluate(now, intake, reviews, snapshots, required, verified):
    day = int(prices.serial(now))
    faults = []
    # A weekend recovery must not be mistaken for Friday's scheduled receipt.
    observed=[(n,r) for n,r in enumerate(intake,46)
              if stress.numeric(value(r,0)) and value(r,0)<=prices.serial(now)]
    latest = max(observed, key=lambda x: value(x[1], 0, 0), default=(46, []))
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
                      not value(r, 10) or not value(r, 18) or
                      not stress.numeric(value(r,11)) or value(r,11)<=0 or
                      not value(r,12) or not stress.numeric(value(r,14)) or value(r,14)<=0 or
                      not value(r,15)))
    complete = False
    review_state = "PENDING"
    location = "Reviews!A8:J1001 / Decision History!A5:S1000"
    if len(matches) == 1:
        n, row = matches[0]
        location = f"Reviews!A{n}:N{n} / Reviews!O{n}:Z{n} / Decision History!A5:S1000"
        stamp, asof = value(row, 0, 0), review_completion(row)
        decision = str(value(row, 5))
        dates_ok = stress.numeric(stamp) and stress.numeric(asof) and day <= stamp <= asof <= prices.serial(now)
        complete = dates_ok and bool(decision) and not re.match(r"(?i)^(started|pending|failed|incomplete|blocked|in progress)", decision) and not missing and not invalid and checkpoint_complete(row, now)
        # A morning-only review cannot pass the afternoon decision check,
        # even when new evidence arrived after the trading cutoff.
        if complete and now.time() >= time(14, 45) and stamp < day + (14 * 60 + 20) / 1440:
            review_state = "PREWINDOW REFRESH MISSING"
            complete = False
        elif complete and isinstance(verified, (int, float)) and (
                value(row,11,asof) if stress.numeric(value(row,11,asof)) else 0) < verified:
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
            review_state = "CHECKPOINT INCOMPLETE" if has_checkpoints(row) else "INCOMPLETE"
    elif len(matches) > 1:
        review_state = "DUPLICATE"
    if not complete and now.time() >= time(14, 45):
        diagnostic = checkpoint_diagnostic(matches[0][1]) if len(matches) == 1 else ""
        faults.append(f"{location}: review {review_state}; missing/duplicate snapshots {','.join(missing) or 'none'}; invalid timestamp/action/coverage {','.join(invalid) or 'none'}" + ("; " + diagnostic if diagnostic else ""))
    return research, review_state, faults


def delivery_status(now, price_logs, intake, reviews, review_ok=None):
    """Persist whether each decision-window write met its own deadline.

    A later successful write clears the live fault, but cannot turn a missed
    research, price, or review deadline into an on-time delivery.
    """
    day = int(prices.serial(now))
    today = now.date().isoformat()
    missed = []

    research_rows = [
        (n, r) for n, r in enumerate(intake, 46)
        if isinstance(value(r, 0), (int, float)) and int(value(r, 0)) == day
    ]
    research_deadline = day + (13 * 60 + 40) / 1440
    finalized_research = [
        (n, r) for n, r in research_rows
        if scheduled_intake(r) and value(r, 1) in {"Complete", "Partial"}
        and not str(value(r, 7)).lower().startswith("started")
        and isinstance(value(r, 8), (int, float))
        and value(r, 0) <= value(r, 8)
    ]
    research_on_time = any(value(r, 8) <= research_deadline
                           for _, r in finalized_research)
    if now.time() >= time(13, 40) and not research_on_time:
        if finalized_research:
            n, row = min(finalized_research, key=lambda x: value(x[1], 8))
            stamp = value(row, 8)
            shown = (prices.EPOCH + timedelta(days=float(stamp))).strftime("%H:%M")
            missed.append(f"research Sources!I{n}={shown} >13:40")
        else:
            missed.append("scheduled research Sources!I46:I1000 missing by13:40; early/manual audits are not scheduled delivery")

    price_deadline = day + (14 * 60 + 15) / 1440
    price_start = day + (12 * 60 + 20) / 1440
    price_rows = [
        (n, r) for n, r in enumerate(price_logs, 5)
        if isinstance(value(r, 0), (int, float)) and int(value(r, 0)) == day
        and value(r, 2) == "Complete"
    ]
    price_on_time = any(price_start <= value(r, 0) <= price_deadline for _, r in price_rows)
    if now.time() >= time(14, 15) and not price_on_time:
        eligible_price_rows = [(n, r) for n, r in price_rows
                               if value(r, 0) >= price_start]
        if eligible_price_rows:
            n, row = min(eligible_price_rows, key=lambda x: value(x[1], 0))
            shown = (prices.EPOCH + timedelta(days=float(value(row, 0)))).strftime("%H:%M")
            missed.append(f"price DAILY CLOSES!L{n}={shown} >14:15")
        else:
            missed.append("price DAILY CLOSES!L5:P200 missing by14:15")

    key = "Daily:" + today
    review_rows = [(n, r) for n, r in enumerate(reviews, 8) if value(r, 9) == key]
    review_start = day + (14 * 60 + 20) / 1440
    review_deadline = day + (14 * 60 + 45) / 1440
    review_on_time = len(review_rows) == 1 and stress.numeric(review_completion(review_rows[0][1])) \
        and review_start <= value(review_rows[0][1],0) <= review_completion(review_rows[0][1]) <= review_deadline \
        and review_ok is not False
    if now.time() >= time(14, 45) and not review_on_time:
        if review_rows and isinstance(value(review_rows[0][1], 0), (int, float)):
            n, row = review_rows[0]
            done=review_completion(row)
            stamp=(prices.EPOCH+timedelta(days=float(done))).strftime("%H:%M") if stress.numeric(done) else "MISSING"
            column="N" if len(row)>10 else "C"
            missed.append(f"review Reviews!{column}{n}={stamp}; completion/required coverage not verified within14:20-14:45")
        else:
            missed.append("review Reviews!A8:J1001 missing/duplicate by14:45")

    if missed:
        return f"MISSED {today} — " + "; ".join(missed)
    if now.time() >= time(14, 45):
        return f"ON TIME {today} — research<=13:40; price<=14:15; review14:20-14:45"
    return f"DUE BEFORE15:15 {today} — research13:40; price14:15; review14:45"


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
    try:
        counts = (int(float(value(record, 4))), int(float(value(record, 5))))
    except (TypeError, ValueError, OverflowError):
        counts = (str(value(record, 4)), str(value(record, 5)))
    return (int(value(record, 0)), audit_phase(value(record, 0)),
            str(value(record, 1)), str(value(record, 2)),
            str(value(record, 3)), *counts, str(value(record, 6)))


def should_append_audit(previous, proposed):
    return audit_signature(previous) != audit_signature(proposed)


def system_state_text(faults, research, review, delivery):
    """One explicit status, written only when the state/checkpoint changes."""
    if faults:
        return "SYSTEM — ⚠ BACKEND: SEE CELL NOTE"
    if "MISSED" in str(delivery):
        return "SYSTEM — ⚠ BACKEND: PREWINDOW DELIVERY MISSED"
    if research.startswith("SAVED ") and review in {"SAVED", "NEXT-DAY INPUTS"}:
        if research.endswith("PARTIAL") or review == "NEXT-DAY INPUTS":
            return "SYSTEM — CHECKED; RESEARCH PARTIAL / NEXT-DAY INPUTS"
        return "SYSTEM — VERIFIED: REQUIRED WRITES"
    return "SYSTEM — PENDING: NEXT DELIVERY CHECK"


def rotation_health(now, receipt, proposed, portfolio, runs):
    """A configured schedule is not delivery; inspect saved data and timestamps."""
    fields = {value(r,0):value(r,1) for r in receipt if value(r,0)}
    faults = []
    stamp = fields.get("COMPLETED AT")
    day = int(prices.serial(now))
    if fields.get("ROTATION STATUS") != "COMPLETE":
        faults.append("PRICE MONITOR!U195:V202: rotation " + str(fields.get("ROTATION STATUS","MISSING")) + "; " + str(fields.get("FAULTS","")))
    if not isinstance(stamp,(int,float)) or not day <= stamp <= prices.serial(now):
        faults.append("PRICE MONITOR!V196: missing/current-day rotation completion receipt")
    expected = prices.last_completed(now,*prices.venue_info("SPY","")[2:])
    if fields.get("PRICE SESSION") != prices.serial(expected):
        faults.append("PRICE MONITOR!V197: expected completed US session " + str(expected))
    held = {value(r, 0): (n, r) for n, r in enumerate(portfolio, 8)
            if isinstance(value(r, 2), (int, float)) and r[2] > 0}
    counts = Counter(value(r, 0) for r in proposed)
    for ticker, (portfolio_row, holding) in held.items():
        matches = [(n, r) for n, r in enumerate(proposed, 2051) if value(r, 0) == ticker]
        if (counts[ticker] != 1 or value(matches[0][1], 3) not in {"HOLD", "TRIM", "ROTATE", "EXIT"}
                or value(matches[0][1], 10) != "VERIFIED PRIOR CLOSE"):
            faults.append(f"PRICE MONITOR!A2051:Q2070: {ticker} missing/invalid recommendation; no silent HOLD")
            continue
        proposal_row, proposal = matches[0]
        # A verified old close remains useful, but a position/order revision
        # makes its saved recommendation stale until this engine reruns.
        if any(not isinstance(value(proposal, i), (int, float))
               or not isinstance(value(holding, j), (int, float))
               or abs(proposal[i] - holding[j]) > 1e-8
               for i, j in ((1, 2), (2, 3))):
            faults.append(
                f"PRICE MONITOR!B{proposal_row}:C{proposal_row} stale {ticker} weights"
                f" versus Portfolio!C{portfolio_row}:D{portfolio_row}; rerun rotation"
            )
    deadline = day + (14*60+45)/1440
    if now.weekday()<5 and now.date() not in prices.US_HOLIDAYS and now.time()>=time(14,45):
        ontime = any(isinstance(value(r,0),(int,float)) and day<=r[0]<=deadline and value(r,2)=="COMPLETE" for r in runs)
        if not ontime:
            faults.append("PRICE MONITOR!A1451:J2000: no verified rotation/recommendation delivery by14:45 Toronto; later recovery is LATE")
    return faults


def view_health(portfolio, analysis, gates):
    """A saved review cannot hide omitted holdings or incorrect cash totals."""
    faults = []
    held = {r[0]: r for r in portfolio if len(r)>3 and isinstance(r[2],(int,float)) and r[2]>0}
    end = next((i for i,r in enumerate(analysis) if value(r,0)=="TOTAL SELL"),None)
    if end is None:
        return ["TRADE ANALYSIS!A4:M100: TOTAL SELL boundary missing"]
    body, total = analysis[:end], analysis[end]
    for n,r in enumerate(body,4):
        if value(r,0) and not isinstance(value(r,3),(int,float)):
            faults.append(f"TRADE ANALYSIS!D{n}: nonnumeric target; displayed percentage may be excluded from totals")
    for ticker, position in held.items():
        matches = [(n,r) for n,r in enumerate(body,4) if value(r,0)==ticker]
        if len(matches)!=1:
            faults.append(f"TRADE ANALYSIS!A4:D{end+3}: {ticker} missing/duplicate held row")
        elif not isinstance(value(matches[0][1],2),(int,float)) or abs(matches[0][1][2]-position[2])>1e-8:
            faults.append(f"TRADE ANALYSIS!C{matches[0][0]}: {ticker} held weight differs from Portfolio")
        gate = [(n,r) for n,r in enumerate(gates,75) if value(r,0)==ticker]
        if len(gate)!=1:
            faults.append(f"PRICE GATES!A75:K93: {ticker} missing/duplicate current holding")
        elif (not isinstance(value(gate[0][1],1),(int,float)) or gate[0][1][1]<=0
                or value(gate[0][1],9)!="OK"):
            faults.append(f"PRICE GATES!B{gate[0][0]}:J{gate[0][0]}: {ticker} price gate unavailable")
    proposed = sum(value(r,3,0) for r in body if isinstance(value(r,3),(int,float)))
    for col,expected in ((7,proposed),(10,1-proposed)):
        actual = value(total,col)
        if not isinstance(actual,(int,float)) or abs(actual-expected)>1e-8:
            letter = "H" if col==7 else "K"
            faults.append(f"TRADE ANALYSIS!{letter}{end+4}: allocation total mismatch; expected {expected:.6f}")
    return faults


def performance_status(now, rows, displayed_value, portfolio_equity):
    """Account freshness is disclosed; absent manual FT data never blocks risk cuts."""
    verified = [(n,r) for n,r in enumerate(rows,9)
                if value(r,2)=="Verified — screenshot"
                and isinstance(value(r,22),(int,float))
                and isinstance(value(r,1),(int,float)) and r[1]>0
                and r[22]<=prices.serial(now)]
    if not verified:
        return "NO VERIFIED ACCOUNT VALUE — overall game profit/loss unknown", []
    n,row = max(verified,key=lambda x:x[1][22])
    faults = []
    for label, actual in (("PERFORMANCE!B4",displayed_value),("Portfolio!F3",portfolio_equity)):
        if not isinstance(actual,(int,float)) or abs(actual-row[1])>1e-8:
            faults.append(f"{label}: differs from latest verified PERFORMANCE!B{n}")
    day = prices.day_of(row[22])
    stale = "CURRENT-DATE ACCOUNT SNAPSHOT" if day==now.date() else "OLDER ACCOUNT SNAPSHOT — current overall profit/loss unknown"
    stamp = (prices.EPOCH+timedelta(days=row[22])).strftime("%d %b %H:%M")
    return f"{stale}; {stamp}; {row[1]:,.0f} credits; since100,000 {row[1]/100000-1:+.2%}; not a closing-settlement or trade-return attribution", faults


def decision_input_health(now,receipt,cards,inputs,portfolio):
    """Independent read-back of mappings, missing-vs-zero and production weights."""
    faults=[]
    if value(value(receipt,0,[]),1)!='VERIFIED':
        faults.append('PRICE MONITOR!V216: decision-input persistence not VERIFIED')
    source={value(r,0):r for r in inputs if value(r,0)}
    mapped={value(r,0):r for r in cards if value(r,0)}
    for position in portfolio:
        if not stress.numeric(value(position,2)) or position[2]<=0:continue
        ticker=position[0];row=source.get(ticker,[]);card=mapped.get(ticker,[])
        if not row or not card:
            faults.append(f'Decision Cards!A4:AF39 / A66:AF104: {ticker} missing mapping');continue
        if not stress.numeric(value(row,1)) or not 0<=prices.serial(now)-row[1]<=3:
            faults.append(f'Decision Cards!A66:AF104: {ticker} stale/future capture')
        if not all(stress.numeric(value(row,i)) for i in (15,17)):
            faults.append(f'Decision Cards!A66:AF104: {ticker} price/macro input missing; see AD input gaps')
            continue
        if not stress.numeric(value(row,16)):
            # No reviewed issuer direction is a research-coverage gap, not a
            # failed price collector. A fabricated composite remains a fault.
            if (value(row,18) not in ("",None) or value(card,27) not in ("",None)
                    or 'Company score UNKNOWN' not in str(value(row,29))
                    or value(row,30) != 0):
                faults.append(f'Decision Cards!A66:AF104: {ticker} missing-company score incorrectly certified; see Q/S/AD/AE')
            for dest,src in ((23,15),(26,17)):
                if not stress.numeric(value(card,dest)) or abs(card[dest]-row[src])>1e-8:
                    faults.append(f'Decision Cards!A4:AF39: {ticker} column {dest+1} disagrees with persisted inputs')
            continue
        if not stress.numeric(value(row,18)):
            faults.append(f'Decision Cards!A66:AF104: {ticker} verified inputs but composite missing')
            continue
        expected=.45*row[16]+.35*row[15]+.20*row[17]
        if abs(row[18]-expected)>1e-8 or not value(row,31):
            faults.append(f'Decision Cards!A66:AF104: {ticker} score/hash invalid')
        for dest,src in ((23,15),(25,16),(26,17),(27,18)):
            if not stress.numeric(value(card,dest)) or abs(card[dest]-row[src])>1e-8:
                faults.append(f'Decision Cards!A4:AF39: {ticker} column {dest+1} disagrees with persisted inputs')
    return faults


def main(now=None):
    injected_now = now is not None
    now = now or datetime.now(prices.TORONTO)
    if now.date() > prices.date(2026, 11, 28):
        print(json.dumps({"status": "Skipped", "reason": "outside FT game audit horizon"}))
        return
    checked_now=now
    now=decision_clock(now)
    book = prices.auth_book()
    tracked = prices.tracking(book)
    archive = book.worksheet("DAILY CLOSES")
    data = archive.get(f"A5:J{archive.row_count}", value_render_option="UNFORMATTED_VALUE")
    faults = []
    for ticker, gs in tracked.items():
        rows = [(n, r) for n, r in enumerate(data, 5) if value(r, 1) == ticker and value(r, 8) == "VERIFIED"]
        expected = prices.last_completed(now, *prices.venue_info(ticker, gs)[2:])
        sessions = {value(r, 0) for _, r in rows}
        latest = max(sessions, default=0)
        if latest != prices.serial(expected) or len(sessions) < 21:
            loc = f"DAILY CLOSES!A{rows[-1][0]}:J{rows[-1][0]}" if rows else f"DAILY CLOSES!A5:J{archive.row_count}"
            faults.append(f"{loc}: {ticker} expected {expected}, latest serial {latest}, {len(sessions)} sessions")
    logs = archive.get(f"L5:P{archive.row_count}", value_render_option="UNFORMATTED_VALUE")
    latest = max((r for r in logs if r), key=lambda r: value(r, 0, 0), default=[])
    if value(latest, 2) != "Complete":
        faults.append(f"DAILY CLOSES!L5:P{archive.row_count}: latest price run not Complete")
    if now.time() >= time(14, 15) and value(latest, 0, 0) < int(prices.serial(now)) + (12*60+20)/1440:
        faults.append(f"DAILY CLOSES!L5:P{archive.row_count}: fresh post12:20 pretrade receipt missing")
    price_state = "FAILED" if faults else "SAVED"
    portfolio = prices.portfolio_rows(book.worksheet("Portfolio"))
    analysis_ws=book.worksheet("TRADE ANALYSIS")
    full_analysis=analysis_ws.get(f"A1:M{analysis_ws.row_count}",value_render_option="UNFORMATTED_VALUE")
    analysis=full_analysis[3:100]
    gates = book.worksheet("PRICE GATES").get("A75:K93",value_render_option="UNFORMATTED_VALUE")
    view_faults = view_health(portfolio,analysis,gates)
    stress_faults,stress_gaps=stress.health(full_analysis)
    view_faults.extend(stress_faults)
    for n,row in enumerate(full_analysis,1):
        for c,v in enumerate(row):
            if isinstance(v,str) and v in {"#REF!","#VALUE!","#DIV/0!","#NAME?","#N/A","#NUM!","#ERROR!"}:
                view_faults.append(f"TRADE ANALYSIS!{chr(65+c)}{n}: {v}")
    performance = book.worksheet("PERFORMANCE")
    performance_note, performance_faults = performance_status(now,
        performance.get("A9:W100",value_render_option="UNFORMATTED_VALUE"),
        value(value(performance.get("B4",value_render_option="UNFORMATTED_VALUE"),0,[]),0),
        value(value(book.worksheet("Portfolio").get("F3",value_render_option="UNFORMATTED_VALUE"),0,[]),0))
    faults.extend(view_faults+performance_faults)
    monitor = book.worksheet("PRICE MONITOR")
    rotation_runs = monitor.get("A1451:J2000", value_render_option="UNFORMATTED_VALUE")
    if monitor.row_count >= 2601:
        rotation_runs += monitor.get(f"A2601:J{monitor.row_count}", value_render_option="UNFORMATTED_VALUE")
    # Weekend preparation uses its actual capture date; historical missed
    # Friday trading-window deliveries remain in the separate audit.
    faults.extend(rotation_health(checked_now, monitor.get("U195:V202", value_render_option="UNFORMATTED_VALUE"),
        monitor.get("A2051:Q2070", value_render_option="UNFORMATTED_VALUE"), portfolio, rotation_runs))
    faults.extend(finance.health(checked_now, monitor.get("U210:V215", value_render_option="UNFORMATTED_VALUE"),
        book.worksheet("Decision Cards").get("A41:AF60", value_render_option="UNFORMATTED_VALUE"),
        [(r[0],r[2],r[3]) for r in portfolio if len(r)>3 and isinstance(r[2],(int,float)) and r[2]>0]))
    cards_ws=book.worksheet('Decision Cards')
    inputs=cards_ws.get('A66:AF104',value_render_option='UNFORMATTED_VALUE')
    faults.extend(decision_input_health(checked_now,
        monitor.get('U216:V218',value_render_option='UNFORMATTED_VALUE'),
        cards_ws.get('A4:AF39',value_render_option='UNFORMATTED_VALUE'),
        inputs,portfolio))
    held_symbols={value(row,0) for row in portfolio if stress.numeric(value(row,2)) and row[2]>0}
    company_gaps=sorted(value(row,0) for row in inputs if value(row,0) in held_symbols
                        and not stress.numeric(value(row,16)))
    bench = book.worksheet("ROTATION BENCH").get("C5:C15")
    source = book.worksheet("Sources")
    b39 = source.get("B39", value_render_option="UNFORMATTED_VALUE")
    verified = b39[0][0] if b39 and b39[0] else 0
    intake = source.get("A46:I1000", value_render_option="UNFORMATTED_VALUE")
    reviews = read_reviews(book)
    snapshots = book.worksheet("Decision History").get("A5:S1000", value_render_option="UNFORMATTED_VALUE")
    # Independent review writes can arrive while our paced reads are in flight.
    # Judge their timestamps at observation time, not the earlier process start.
    # A truly future timestamp is still rejected; injected test clocks stay fixed.
    if not injected_now:
        checked_now = datetime.now(prices.TORONTO)
        now=decision_clock(checked_now)
    required=required_symbols(portfolio,bench,reviews,now)
    research, review, issues = evaluate(now, intake, reviews, snapshots, required, verified)
    faults.extend(issues)
    if now.time()>=time(13,40) and not any(scheduled_intake(r)
            and int(value(r,0))==int(prices.serial(now))
            and value(r,1) in {"Complete","Partial"}
            and stress.numeric(value(r,8)) and value(r,0)<=r[8]<=prices.serial(now)
            for r in intake):
        faults.append("Sources!A46:I1000: scheduled research receipt missing; early/manual saved audits do not prove the owner ran")
    health = book.worksheet("BACKEND HEALTH")
    consistency="FAILED" if view_faults+performance_faults else "PARTIAL" if stress_gaps else "PASS"
    coverage_note="Current holdings, allocation totals, scenario mapping and account-value links agree"
    if company_gaps:
        coverage_note += ("; company assessments UNKNOWN (not negative/zero): "+
                          ",".join(company_gaps)+"; 45/35/20 composites blocked pending eligible issuer evidence")
    if stress_gaps:
        coverage_note += "; scenario assumptions UNKNOWN at TRADE ANALYSIS!"+",".join(stress_gaps)+"; stress totals unavailable for sizing"
    checks = [["WORKBOOK CONSISTENCY",consistency,
               " | ".join(view_faults+performance_faults) or coverage_note],
              ["ACCOUNT PERFORMANCE",performance_note,"Account value is separate from stock momentum and workflow completion"]]
    if health.get("A41:C42",value_render_option="FORMATTED_VALUE") != checks:
        health.update(range_name="A41:C42",values=checks,value_input_option="RAW")
        if health.get("A41:C42",value_render_option="FORMATTED_VALUE") != checks:
            raise RuntimeError("BACKEND HEALTH!A41:C42: consistency/performance readback mismatch")
    if health.row_count < 1000:
        health.resize(rows=1000)
    headers = ["CHECKED AT", "PRICE WRITE", "RESEARCH WRITE", "REVIEW WRITE",
               "PRICE SYMBOLS", "REQUIRED SNAPSHOTS", "EXACT FAULT / COVERAGE",
               "GITHUB RUN", "COMMIT"]
    header_values = health.get("A43:I43", value_render_option="FORMATTED_VALUE")
    if not header_values or not any(header_values[0]):
        health.update(range_name="A43:I43", values=[headers], value_input_option="RAW")
        if health.get("A43:I43", value_render_option="FORMATTED_VALUE") != [headers]:
            raise RuntimeError("BACKEND HEALTH!A43:I43: header initialization readback mismatch")
    elif header_values[0] != headers:
        raise RuntimeError("BACKEND HEALTH!A43:I43: existing audit schema mismatch; not overwriting")
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
    delivery = delivery_status(now, logs, intake, reviews,review_ok=review in {"SAVED","NEXT-DAY INPUTS"})
    window = health.get("B37", value_render_option="FORMATTED_VALUE")
    persisted_delivery = window[0][0] if window and window[0] else ""
    if persisted_delivery != delivery:
        health.update(range_name="B37", values=[[delivery]], value_input_option="RAW")
        if health.get("B37", value_render_option="FORMATTED_VALUE") != [[delivery]]:
            raise RuntimeError("BACKEND HEALTH!B37: delivery-state readback mismatch")
    if "MISSED" in delivery:
        details += " | Prewindow delivery MISSED: BACKEND HEALTH!B37, Sources!I46:I1000, Reviews!A8:J1001, DAILY CLOSES!L5:P200. Current recovery does not backdate the missed window."
    if checked_now.date()!=now.date():
        details="Diagnostic of last required trading window "+str(now.date())+"; actual check "+checked_now.isoformat()+". "+details
    record = [prices.serial(checked_now), price_state, research, review, len(tracked), len(required), details[:48000], link, os.getenv("GITHUB_SHA", "local")]
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
        note = f"Persistence audit BACKEND HEALTH!A{dest}:I{dest}, {checked_now.isoformat()}. " + details
        warning = bool(faults) or "MISSED" in str(delivery)
        background = {"red": 0.98, "green": 0.84, "blue": 0.84} if warning else {"red": 0.93, "green": 0.95, "blue": 0.97}
        desk_text = system_state_text(faults, research, review, delivery)
        stamp = checked_now.strftime("%d %b %H:%M")
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
                "fields": "userEnteredFormat.numberFormat"}}
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





"""Executable RULE-015/020: matched-session leadership, warnings and decisions.

Owns only explicitly marked new blocks in PRICE MONITOR, CANDIDATE POOL AR:BC,
and ROTATION BENCH A140:P243. Does not write holdings, orders, Evidence, research
receipts, or the human review. Trade Desk formulas consume these dated results.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta
import hashlib
import json
import math
import os
import re

import ft_price_writer as prices
import ft_financial_stress as finance
import ft_trade_desk as desk_views
from ft_rotation_universe import SECTORS, INDUSTRIES, COMPANY_SECTOR, COMPANY_INDUSTRY

END = date(2026, 11, 27)
DEFAULTS = {
    "warning_3d_pp": 4.0, "warning_5d_pp": 6.0,
    "trim_5d_pct": -4.0, "trim_rs5_pp": -3.0,
    "exit_5d_pct": -8.0, "exit_rs5_pp": -5.0,
    "trim_step_pp": 5.0, "max_cash_pct": 25.0, "min_names": 5,
    "max_entry_3d_pct": 6.0, "max_entry_5d_pct": 8.0,
    "max_above_ma20_pct": 7.0, "research_max_days": 3,
}
METRIC_HEADERS = ["GROUP", "NAME", "KIND", "SESSION", "3D %", "5D %", "10D %", "20D %",
                  "3D-SPY pp", "5D-SPY pp", "10D-SPY pp", "20D-SPY pp", "3D ACCEL pp",
                  "SAMPLE >0 5D", "SAMPLE >SPY 5D", "SAMPLE ABOVE MA20", "SAMPLE COVERAGE",
                  "RANK", "STATE"]
ALERT_HEADERS = ["KEY", "FIRST CAPTURED", "FIRST SIGNAL SESSION", "LAGGING", "LEADING",
                 "3D DIVERGENCE pp", "5D DIVERGENCE pp", "LAG 3D %", "LEAD 3D %", "LAG 5D %",
                 "LEAD 5D %", "LEAD BREADTH SAMPLE", "EXPOSED HOLDINGS", "REVIEW STATE",
                 "LAST SIGNAL SESSION", "CAPTURE MODE", "3D THRESHOLD pp", "5D THRESHOLD pp", "DATA HASH"]
RUN_HEADERS = ["COMPLETED AT", "MODE", "STATUS", "SESSION", "SECTORS", "INDUSTRIES",
               "WARNINGS", "EXACT FAULTS", "GITHUB RUN", "SHA"]
PROPOSAL_HEADERS = ["TICKER", "HELD", "QUEUED", "ACTION", "PROPOSED TARGET", "REASON",
                    "SAVED AT", "PRICE SESSION", "NEXT ORDER WINDOW", "NEXT EXECUTION",
                    "DATA STATE", "ORDER GUARD", "INDUSTRY", "5D %", "5D-SPY pp", "CASH CHECK", "DISPLAY"]
CANDIDATE_HEADERS = ["SECTOR", "3D %", "5D %", "10D %", "20D %", "5D-SPY pp", "3D ACCEL pp",
                     "SECTOR BREADTH SAMPLE", "CROSS-SECTOR RANK", "ENTRY STATE", "CASH/INCUMBENT RANK", "SAVED AT"]
VALUATION_HEADERS = ["VALUATION MULTIPLE", "VALUATION BASIS", "VALUATION REVIEWED", "VALUATION SOURCE"]
BENCH_HEADERS = ["RANK", "TICKER", "SECTOR", "3D %", "5D %", "10D %", "20D %", "5D-SPY pp",
                 "SCORE (NOT RETURN)", "READINESS", "FT ELIGIBLE", "CATALYST", "RESEARCH DATE",
                 "ENTRY/RESEARCH GAP", "CASH COMPARISON", "SAVED AT"]


def val(row, i, default=""):
    return row[i] if len(row) > i and row[i] is not None else default


def isnum(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def prior_session(day, venue="US"):
    holidays = prices.LSE_HOLIDAYS if venue == "LSE" else prices.US_HOLIDAYS
    day -= timedelta(days=1)
    while day.weekday() >= 5 or day in holidays:
        day -= timedelta(days=1)
    return day


def next_session(day, venue="US"):
    holidays = prices.LSE_HOLIDAYS if venue == "LSE" else prices.US_HOLIDAYS
    day += timedelta(days=1)
    while day.weekday() >= 5 or day in holidays:
        day += timedelta(days=1)
    return day


def session_window(end, n, venue="US"):
    days = [end]
    for _ in range(n):
        days.append(prior_session(days[-1], venue))
    return list(reversed(days))


def load_archive(rows):
    """Quarantine duplicate/conflicting keys, invalid units/dates and source gaps."""
    hist, locations, problems = defaultdict(dict), {}, []
    counts = defaultdict(int)
    for n, r in enumerate(rows, 5):
        if len(r) < 10 or val(r, 8) != "VERIFIED":
            continue
        t, key = str(val(r, 1)), str(val(r, 9))
        try:
            d = prices.day_of(r[0])
            venue, unit, _, _ = prices.venue_info(t, str(val(r, 2)))
            if (not isnum(r[0]) or not isnum(r[3]) or r[3] <= 0 or
                key != f"{d}|{t}" or val(r, 4) != unit or val(r, 5) != venue or
                not str(val(r, 6)).startswith("https://") or d.weekday() >= 5):
                raise ValueError("invalid verified row")
            counts[key] += 1
            if counts[key] > 1:
                hist[t].pop(d, None)
                problems.append(f"DAILY CLOSES!A{n}:J{n}: duplicate {key}; excluded")
                continue
            hist[t][d] = float(r[3])
            locations[(t, d)] = n
        except (ValueError, TypeError, IndexError):
            problems.append(f"DAILY CLOSES!A{n}:J{n}: invalid verified record {t}")
    return dict(hist), locations, problems


def returns(hist, ticker, end, n, venue="US"):
    days = session_window(end, n, venue)
    series = hist.get(ticker, {})
    if any(d not in series for d in days):
        return None
    return (series[end] / series[days[0]] - 1) * 100


def metric(hist, ticker, end):
    venue = "LSE" if ticker.endswith(":LSE") else "US"
    out = {"ticker": ticker, "session": str(end), "close": hist.get(ticker, {}).get(end)}
    for n in (3, 5, 10, 20):
        out[f"r{n}"] = returns(hist, ticker, end, n, venue)
        # UK/US comparisons use the SAME endpoints; no mismatched close dates.
        start = session_window(end, n, venue)[0]
        benchmark = hist.get("SPY", {})
        b = (benchmark[end] / benchmark[start] - 1) * 100 if end in benchmark and start in benchmark else None
        out[f"rs{n}"] = out[f"r{n}"] - b if out[f"r{n}"] is not None and b is not None else None
    before = session_window(end, 3, venue)[0]
    old = returns(hist, ticker, before, 3, venue)
    old_spy = returns(hist, "SPY", before, 3)
    out["accel"] = out["rs3"] - (old - old_spy) if all(x is not None for x in (out["rs3"], old, old_spy)) else None
    days = session_window(end, 19, venue)
    values = [hist.get(ticker, {}).get(d) for d in days]
    out["extension"] = (out["close"] / (sum(values) / 20) - 1) * 100 if all(x is not None for x in values) else None
    out["score"] = sum(out[f"rs{n}"] * w for n, w in ((3, .35), (5, .30), (10, .20), (20, .15))) if all(out[f"rs{n}"] is not None for n in (3, 5, 10, 20)) else None
    out["state"] = "MISSING" if out["score"] is None else (
        "LEADING" if out["rs5"] > 0 and out["rs20"] > 0 else
        "EMERGING" if out["rs3"] > 0 and out["accel"] is not None and out["accel"] > 0 else
        "FADING" if out["rs3"] < 0 and out["rs20"] > 0 else "MIXED")
    return out


def breadth(hist, members, end):
    observed = [metric(hist, t, end) for t in members]
    good = [m for m in observed if m["r5"] is not None and m["rs5"] is not None and m["extension"] is not None]
    n = len(good)
    return {"positive": sum(m["r5"] > 0 for m in good) / n if n else None,
            "relative": sum(m["rs5"] > 0 for m in good) / n if n else None,
            "ma20": sum(m["extension"] > 0 for m in good) / n if n else None,
            "coverage": f"{n}/{len(members)} fixed sample; not full-sector breadth"}


def group_metrics(hist, end):
    result = {}
    for t, name in [(t, v[0]) for t, v in SECTORS.items()] + list(INDUSTRIES.items()):
        m = metric(hist, t, end)
        m.update(name=name, kind="SECTOR" if t in SECTORS else "INDUSTRY")
        m["breadth"] = breadth(hist, SECTORS[t][1], end) if t in SECTORS else {}
        result[t] = m
    ranked = sorted((m for m in result.values() if m["score"] is not None), key=lambda m: (-m["score"], m["ticker"]))
    for rank, m in enumerate(ranked, 1):
        m["rank"] = rank
    return result


def detect_warnings(metrics, config=DEFAULTS):
    """An initial divergence triggers review; breadth/research never delays it."""
    alerts = []
    # All sectors plus growth/industry laggards. Leaders must be genuine sectors
    # so several overlapping industry ETFs cannot masquerade as diversification.
    for lag, a in metrics.items():
        if a["r3"] is None or a["r5"] is None or a["rs3"] is None:
            continue
        for lead in SECTORS:
            b = metrics.get(lead, {})
            if lead == lag or any(b.get(k) is None for k in ("r3", "r5", "rs3")):
                continue
            d3, d5 = b["r3"] - a["r3"], b["r5"] - a["r5"]
            if (d3 >= config["warning_3d_pp"] or d5 >= config["warning_5d_pp"]) and a["rs3"] < 0 and b["rs3"] > 0:
                alerts.append({"lag": lag, "lead": lead, "d3": d3, "d5": d5,
                               "lag3": a["r3"], "lead3": b["r3"], "lag5": a["r5"], "lead5": b["r5"],
                               "breadth": (f"positive5D={b.get('breadth',{}).get('positive')}; >SPY={b.get('breadth',{}).get('relative')}; " + b.get("breadth", {}).get("coverage", "Unknown"))})
    return sorted(alerts, key=lambda a: (-max(a["d3"] / config["warning_3d_pp"], a["d5"] / config["warning_5d_pp"]), a["lag"], a["lead"]))


def parse_positions(rows):
    result = []
    for r in rows:
        if val(r, 0) and isnum(val(r, 2)) and isnum(val(r, 3)) and (r[2] > 0 or r[3] > 0):
            result.append({"ticker": r[0], "held": float(r[2]), "queued": float(r[3])})
    if not result:
        raise ValueError("Portfolio!A8:D100: no validated holdings")
    return result


def pending_orders(log):
    latest = {}
    for r in log:
        if val(r, 2):
            latest[r[2]] = str(val(r, 5))
    return {t for t, s in latest.items() if re.search(r"(?i)submitted|pending|queued|scheduled", s) and not re.search(r"(?i)^executed|cancel", s)}


def execution_window(now, venue="US"):
    order_day = now.date()
    if now.time() > time(15, 15) or order_day.weekday() >= 5 or order_day in prices.US_HOLIDAYS:
        order_day = next_session(order_day)
    opening_day = next_session(order_day, venue)
    order = datetime.combine(order_day, time(15, 15), prices.TORONTO)
    zone = prices.venue_info("BHP:LSE" if venue == "LSE" else "SPY", "")[2]
    opening = datetime.combine(opening_day, time(8) if venue == "LSE" else time(9, 30), zone)
    if opening_day > END:
        return order.isoformat(), "NONE: next session is after 27 Nov game end"
    return order.isoformat(), opening.isoformat()


def proposals(hist, positions, orders, metrics, alerts, now, config=DEFAULTS):
    pending = pending_orders(orders)
    targets = {p["ticker"]: p["queued"] for p in positions}
    available_cash = max(0.0, config["max_cash_pct"] / 100 - (1 - sum(targets.values())))
    output = []
    for p in positions:
        t, held, queued = p["ticker"], p["held"], p["queued"]
        if held <= 0 and queued <= 0:
            continue
        venue, _, zone, close = prices.venue_info(t, "")
        # At 14:05 Toronto London has closed but New York has not. Use the
        # latest common completed session for relative strength, never today's
        # London close against yesterday's SPY or a future US close.
        venue_end = prices.last_completed(now, zone, close)
        end = min(venue_end, prices.last_completed(now, *prices.venue_info("SPY", "")[2:]))
        m = metric(hist, t, end)
        industry = COMPANY_INDUSTRY.get(t, COMPANY_SECTOR.get(t, "SPY"))
        alert = any(a["lag"] in {industry, COMPANY_SECTOR.get(t)} for a in alerts)
        target, action, guard, cashcheck = held, "HOLD", "CLEAR", "Within limits"
        healthy = all(m[k] is not None for k in ("close", "r3", "r5", "r10", "r20", "rs5"))
        why = f"{end} verified close; 5D {m['r5']:+.2f}%, vs SPY {m['rs5']:+.2f}pp; " if healthy else f"DAILY CLOSES!A:J: missing {t} matched completed-session prices through {end}; "
        if t in pending or not math.isclose(held, queued):
            target = queued
            guard = "PENDING — DO NOT RESUBMIT"
            why += "existing order retained; settlement unconfirmed; reassess the existing instruction if risk changes"
        elif not healthy:
            action, why = "UNRESOLVED", why + "no evidence-based action generated; previous HOLD is not a current recommendation"
        else:
            severe = m["r5"] <= config["exit_5d_pct"] and m["rs5"] <= config["exit_rs5_pp"] and m["r10"] < 0
            weak = (m["r5"] <= config["trim_5d_pct"] and m["rs5"] <= config["trim_rs5_pp"]) or (alert and m["r3"] <= -config["warning_3d_pp"])
            if severe or weak:
                desired = held if severe else min(held, config["trim_step_pp"] / 100)
                cut = min(desired, available_cash)
                target = round(held - cut, 8)
                if 0 < target < .05:
                    # A modest trim must not become a larger full exit merely
                    # because the residual would breach the 5% minimum.
                    target = 0 if severe and available_cash >= held else min(held, .05)
                names = sum(w > 0 for w in targets.values())
                if target == 0 and names <= int(config["min_names"]):
                    target = .05 if held > .05 else held
                target = min(held, target)
                cut = held - target
                available_cash -= cut
                targets[t] = target
                action = "ROTATE" if target == 0 else "TRIM" if cut > 1e-9 else "HOLD"
                why += f"price-risk reduction justified; desired cut {desired:.0%} independently of replacements"
                if cut + 1e-9 < desired:
                    cashcheck = "RULE CONSTRAINT: full risk cut needs a compliant allocation; do not silently waive cash/minimum-name limits"
                    why += "; " + cashcheck
                if cut > 0 and any(not math.isclose(p["held"], p["queued"]) for p in positions):
                    conditional = "cash headroom uses existing queued buys, not confirmed fills; verify their status before submitting this trim"
                    why += "; " + conditional
                    cashcheck += "; " + conditional
            else:
                why += "no configured loss-control threshold crossed; retain exposure pending issuer/catalyst review"
            if alert:
                why += f"; RULE-015 rotation warning affects {industry}: immediate review required"
        order, execution = execution_window(now, venue)
        if guard != "CLEAR":
            display = f"{t} — HOLD {held:.0%}; already queued → {queued:.0%}; DO NOT RESUBMIT"
        elif action == "UNRESOLVED":
            display = f"{t} — UNRESOLVED: DATA FAILURE; see cell note"
        else:
            display = f"{t} — {action}; target {target:.0%}" + ("; sell to CASH" if target < held else "")
            if target < held and any(not math.isclose(p["held"], p["queued"]) for p in positions):
                display += "; conditional on existing queued buys"
        if not healthy and guard != "CLEAR":
            display += "; DATA FAILURE — review existing order"
        display += " | " + (f"3D {m['r3']:+.1f}%; 5D {m['r5']:+.1f}%; RS {m['rs5']:+.1f}pp" if healthy else "verified closes missing")
        output.append([t, held, queued, action, target, why, prices.serial(now), prices.serial(end), order, execution,
                       "VERIFIED PRIOR CLOSE" if healthy else "FAILED", guard, industry, m["r5"], m["rs5"], cashcheck, display])
    return output


def rank_candidates(rows, hist, end, now, config=DEFAULTS, positions=None):
    result = []
    for n, r in enumerate(rows, 5):
        t = str(val(r, 0))
        if not t or not val(r, 3):
            continue
        m = metric(hist, t, end)
        sector = COMPANY_SECTOR.get(t, "Unknown")
        gaps = []
        if m["score"] is None:
            gaps.append("missing matched price history")
        if val(r, 18) != "YES":
            gaps.append("FT eligibility unverified" if val(r, 18) != "NO" else "FT unavailable")
        reviewed = val(r, 17)
        if val(r, 16) != "Verified" or not isnum(reviewed) or not 0 <= (now.date() - prices.day_of(reviewed)).days <= config["research_max_days"]:
            gaps.append("refresh original-source research")
        event = val(r, 7)
        if not isnum(event) or not now.date() <= prices.day_of(event) <= END:
            gaps.append("verify catalyst before game end")
        if not val(r, 19) or not val(r, 11) or val(r, 25) != "Pass":
            gaps.append("entry/risk checks incomplete")
        if val(r, 39) != "Positive" or not val(r, 38):
            gaps.append("cash edge unqualified")
        valuation_date = val(r, 57)
        if (not isnum(val(r, 55)) or val(r, 55) <= 0 or not val(r, 56) or
            not isnum(valuation_date) or not 0 <= (now.date() - prices.day_of(valuation_date)).days <= config["research_max_days"] or
            not str(val(r, 58)).startswith("https://")):
            gaps.append("valuation review missing or stale")
        stretched = any(m[k] is not None and m[k] > config[c] for k, c in (("r3", "max_entry_3d_pct"), ("r5", "max_entry_5d_pct"), ("extension", "max_above_ma20_pct")))
        if stretched:
            gaps.append("extended: wait for orderly entry; do not chase")
        if m["rs5"] is not None and (m["rs5"] <= 0 or m["r5"] <= 0):
            gaps.append("momentum has not qualified")
        held = (any(p["ticker"] == t and (p["held"] > 0 or p["queued"] > 0) for p in positions)
                if positions is not None else isnum(val(r, 34)) and r[34] > 0)
        # Research gaps must be visible without turning unverified data into a buy.
        # Provisional candidates remain excluded from automatic paired orders.
        provisional = (not held and val(r, 18) == "YES" and str(val(r, 19)).startswith("https://") and m["score"] is not None)
        state = ("HELD — no duplicate purchase" if held else
                 "READY FOR HUMAN REVIEW" if not gaps else
                 "PROVISIONAL RESEARCH — " + "; ".join(gaps) if provisional else
                 "RESERVE — " + "; ".join(gaps))
        # Price score ranks the entire universe independently of topic or email.
        result.append({"row": n, "ticker": t, "sector": sector, "m": m, "state": state,
                       "eligible": val(r, 18), "event": val(r, 6), "reviewed": reviewed,
                       "gaps": "; ".join(gaps), "cash": val(r, 38), "held": held})
    result.sort(key=lambda c: (c["m"]["score"] is None, -(c["m"]["score"] or 0), c["ticker"]))
    for rank, c in enumerate(result, 1):
        c["rank"] = rank
    held = ([{"ticker":p["ticker"],"m":metric(hist,p["ticker"],end)} for p in positions if p["held"]>0]
            if positions is not None else [c for c in result if c["held"]])
    held = [c for c in held if c["m"]["score"] is not None]
    weakest = min(held, key=lambda c: c["m"]["score"]) if held else None
    ready = [c for c in result if c["state"] == "READY FOR HUMAN REVIEW"]
    for c in result:
        comparison = (f"momentum score {c['m']['score']:.2f} vs weakest actual incumbent {weakest['ticker']} {weakest['m']['score']:.2f}; not an expected return"
                      if weakest and c["m"]["score"] is not None else "incumbent comparison incomplete")
        if c in ready:
            c["cash_rank"] = f"Qualified purchase #{ready.index(c)+1}; cash after {len(ready)} qualified ideas; {comparison}; human sizing required"
        elif c["held"]:
            c["cash_rank"] = "Existing holding; see dated HOLD/TRIM/ROTATE proposal; " + comparison
        else:
            c["cash_rank"] = "CASH ranks ahead until qualification; " + comparison
    return result


def warning_rows(alerts, prior, end, now, positions, config, digest, mode="LIVE"):
    # One episode per pair, preserving first captured and first signal dates.
    # Reactivation after a cleared episode creates a new row, never backdates.
    old = [list(r) + [""] * (19 - len(r)) for r in prior if val(r, 0)]
    active = {(r[3], r[4]): i for i, r in enumerate(old) if r[13] != "CLEARED"}
    current = {(a["lag"], a["lead"]) for a in alerts}
    for pair, i in active.items():
        if pair not in current:
            old[i][13] = "CLEARED"
    for a in alerts:
        pair = a["lag"], a["lead"]
        exposure = ", ".join(f"{p['ticker']} {p['held']:.0%}" for p in positions if p["held"] > 0 and a["lag"] in {COMPANY_SECTOR.get(p["ticker"]), COMPANY_INDUSTRY.get(p["ticker"])}) or "Universe warning; no directly mapped holding"
        if pair in active:
            i = active[pair]
            old[i][14] = prices.serial(end)
            # First evidence and thresholds remain frozen, even after edits.
            continue
        base_key = f"RULE-015:{mode}:{end}:{a['lag']}:{a['lead']}"
        key = base_key
        # A threshold edit can clear/reactivate a pair on the same session.
        # Preserve both episodes without colliding with a historical key.
        existing_keys = {r[0] for r in old}
        episode = 2
        while key in existing_keys:
            key = f"{base_key}:episode-{episode}"
            episode += 1
        old.append([key, prices.serial(now), prices.serial(end), *pair, a["d3"], a["d5"], a["lag3"], a["lead3"], a["lag5"], a["lead5"], a["breadth"], exposure,
                    "REVIEW NOW — independent of Gmail", prices.serial(end), mode, config["warning_3d_pp"], config["warning_5d_pp"], digest])
    if len(old) > 1180:
        raise RuntimeError("PRICE MONITOR!A221:S1400: warning ledger capacity; refusing overwrite")
    return old


def clean_rows(rows):
    return [["" if x is None else x for x in r] for r in rows]


def financial_review(proposed, assessments, ranked, positions, orders, now, config=DEFAULTS):
    """Extend these proposals, using the existing candidate gates and game rules.

    No order is placed. A paired purchase is one indivisible recommendation;
    unresolved orders or different execution venues block a new pairing.
    """
    output = [list(p) for p in proposed]
    targets = {p["ticker"]:p["queued"] for p in positions}
    targets.update({p[0]:p[4] for p in output})
    pending = pending_orders(orders)
    unsettled = bool(pending) or any(abs(p["held"]-p["queued"])>1e-8 for p in positions)
    buys = []
    used = set(targets)
    for p in output:
        a = assessments[p[0]]
        a["scope"] = ("Company-specific financing/cash evidence" if a["material"] else
                      "Company funding watch plus market/sector context; no confirmed issuer credit event" if (a["scores"]["sustainability"] or 0)>=45 else
                      "Existing market/sector evidence; no confirmed issuer credit event")
        desired = max(0.,p[1]-p[4])
        match = re.search(r"desired cut ([0-9.]+)%",p[5])
        if match:
            desired = max(desired,float(match[1])/100)
        if a["material"]:
            desired = max(desired,min(p[1],config["trim_step_pp"]/100))
        eligible = p[11]=="CLEAR" and p[10]=="VERIFIED PRIOR CLOSE" and not unsettled and not p[9].startswith("NONE")
        why = "HOLD preferred: no independently justified new risk cut. Cash reduction lacks that trigger; alternatives remain subject to existing valuation/catalyst/entry qualification."
        if not any(c["state"]=="READY FOR HUMAN REVIEW" for c in ranked):
            why+=" No unheld replacement currently qualifies."
        if eligible and desired>1e-8:
            already = p[1]-p[4]
            room = max(0.,config["max_cash_pct"]/100-(1-sum(targets.values())))
            extra = min(max(0.,desired-already),room)
            cash_target = round(p[4]-extra,8)
            names = sum(w>0 for w in targets.values())
            if (cash_target==0 and names<int(config["min_names"])+1) or 0<cash_target<.05:
                extra=0.
            if extra>1e-8:
                p[4]=cash_target; targets[p[0]]=cash_target
            cut = p[1]-p[4]
            if cut>1e-8:
                p[3]="EXIT" if p[4]==0 else "TRIM"
                p[15]="CASH; within 25% ceiling; no purchase required"
                why=f"Reduce independently evidenced downside by selling {cut*100:g}pp to cash; cash preserves flexibility before a qualified entry."
            remaining=max(0.,desired-cut)
            choices=[c for c in ranked if c["state"]=="READY FOR HUMAN REVIEW" and c["ticker"] not in used and c["ticker"] not in pending]
            candidate=next((c for c in choices if prices.venue_info(c["ticker"],"")[0]==prices.venue_info(p[0],"")[0]),None)
            # New positions must be at least 5%, no more than 25%; conserve cash
            # across the same eligible game session. Never rank missing data.
            pair = min(remaining,.25)
            newtarget=round(p[4]-pair,8)
            if candidate and pair>=.05-1e-8 and (newtarget==0 or newtarget>=.05) and sum(w>0 for w in targets.values())+(1 if newtarget else 0)>=int(config["min_names"]):
                t=candidate["ticker"]; used.add(t); p[4]=newtarget; targets[p[0]]=newtarget; targets[t]=pair
                p[3]="ROTATE"; p[15]=f"REQUIRED SIMULTANEOUS BUY {t} {pair*100:g}pp; neither leg independently executable if cash cap breached"
                why=f"Sell {p[1]-newtarget:.0%} ({(p[1]-newtarget)*100:g}pp); buy {t} {pair*100:g}pp in the same session. {candidate['cash']}. Existing valuation, catalyst, entry and eligibility gates passed."
                m=candidate["m"]
                buys.append([t,0.,0.,"BUY",pair,why,prices.serial(now),p[7],p[8],p[9],"VERIFIED PRIOR CLOSE","CLEAR",candidate["sector"],m["r5"],m["rs5"],f"PAIRED ONLY with {p[0]} sale",f"{t} — BUY {pair*100:g}pp ONLY paired with {p[0]}; target {pair:.0%}"])
            elif remaining>1e-8:
                p[15]="BLOCKED: cash/name/size constraint; needs simultaneous qualified purchase in same execution venue"
                why=f"Desired sell {desired*100:g}pp; {remaining*100:g}pp blocked. Cash is at/near its ceiling; no compliant qualified purchase is available. Retain the blocked portion."
        elif desired>1e-8 and not eligible:
            why="No new financial trade: resolve existing orders/data/execution deadline first; never resubmit an existing instruction."
        a["reversal"] = ("Resolve cash constraint with a qualified same-session purchase; risk-cut case reverses if existing price weakness clears and issuer funding stabilizes."
                          if "BLOCKED" in p[15] else
                          "Reverse cut only after issuer credit/cash deterioration improves and existing price/relative weakness clears; recheck game limits."
                          if p[4]<p[1] else
                          "Reassess HOLD on a verified adverse financing event, cash-flow guidance cut or existing price-loss threshold; requalify cash/purchase constraints.")
        a["options"] = why
        p[5] += " | "+finance.summary(a)+" | "+a["scope"]+" | "+why+" Reversal: "+a["reversal"]
        if p[11]=="CLEAR" and p[10]=="VERIFIED PRIOR CLOSE":
            label="NO TRADE (blocked cut)" if p[4]==p[1] and "BLOCKED" in p[15] else p[3]
            p[16]=f"{p[0]} — {label}; {p[1]:.0%} → {p[4]:.0%}; sell {(p[1]-p[4])*100:g}pp | {finance.summary(a)} | {p[15]}"
        else:
            p[16]+=" | "+finance.summary(a)
    if len(output)+len(buys)>20:
        raise RuntimeError("PRICE MONITOR proposal capacity exceeded")
    if any(w>.25+1e-8 or w<0 for w in targets.values()) or not -1e-8<=1-sum(targets.values())<=.25+1e-8 or sum(w>0 for w in targets.values())<5:
        raise RuntimeError("Financial proposal fails final portfolio constraints")
    return output+buys


def write_verified(ws, address, rows):
    rows = clean_rows(rows)
    ws.update(range_name=address, values=rows, value_input_option="RAW")
    actual = ws.get(address, value_render_option="UNFORMATTED_VALUE")
    # Sheets trims trailing blanks. Pad only within the declared rectangle.
    normalized = [(r + [""] * len(e))[:len(e)] for r, e in zip(actual, rows)]
    if not prices.rows_match(normalized, rows):
        raise RuntimeError(f"{ws.title}!{address}: rotation write/readback mismatch")


def proposal_note_updates(sheet_id, desk_rows, proposed):
    """Attach each live proposal to its ticker, never to an assumed row order."""
    by_ticker = {str(p[0]): (i, p) for i, p in enumerate(proposed, 1) if p[1] > 0}
    requests = []
    for sheet_row, cells in enumerate(desk_rows, 2):
        decision = str(val(cells, 0)).strip()
        ticker = decision.split(" ", 1)[0] if decision else ""
        item = by_ticker.get(ticker)
        if item is None:
            if not re.match(r"^[A-Z][A-Z0-9.:/-]*\s+[—–-]\s+(?:EXITED|HOLD|TRIM|EXIT|ROTATE|BUY)\b", decision):
                continue
            # A disposed ticker must not retain another holding's live note.
            note = "No current held-position proposal. See the dated decision text and confirmed Portfolio weights."
            color = {"red":.96,"green":.97,"blue":.98}
        else:
            proposal_index, row = item
            bad = row[10] == "FAILED" or row[3] == "UNRESOLVED"
            color = {"red":.98,"green":.84,"blue":.84} if bad else {"red":.96,"green":.97,"blue":.98}
            note = (f"PRICE MONITOR!A{2050+proposal_index}:Q{2050+proposal_index}. {row[5]}\n"
                    f"Next order window {row[8]}; next executable open {row[9]}. "
                    "Existing price/rotation plus available financial evidence; provisional model. No order placed.")
        requests.append({"updateCells":{"range":{"sheetId":sheet_id,"startRowIndex":sheet_row-1,"endRowIndex":sheet_row,"startColumnIndex":1,"endColumnIndex":2},
            "rows":[{"values":[{"note":note,"userEnteredFormat":{"backgroundColor":color}}]}],
            "fields":"note,userEnteredFormat.backgroundColor"}})
    return requests


def main(now=None):
    injected_now = now is not None
    now = now or datetime.now(prices.TORONTO)
    if now.date() > END or now.weekday() >= 5:
        print(json.dumps({"status": "Skipped", "reason": "outside game weekday horizon"}))
        return
    book = prices.auth_book()
    monitor = book.worksheet("PRICE MONITOR")
    # Initialization is explicit and validated; never clobber an unrecognised block.
    if monitor.row_count < 2500:
        monitor.resize(rows=2500)
    for address, headers in (("A174:S174", METRIC_HEADERS), ("A220:S220", ALERT_HEADERS), ("A1450:J1450", RUN_HEADERS), ("A2050:Q2050", PROPOSAL_HEADERS)):
        existing = monitor.get(address)
        if existing and any(existing[0]) and existing[0] != headers:
            raise RuntimeError("PRICE MONITOR!" + address + ": unexpected schema")
        write_verified(monitor, address, [headers])
    settings = monitor.get("U175:V187", value_render_option="UNFORMATTED_VALUE")
    if not settings:
        settings = [[k, v] for k, v in DEFAULTS.items()]
        write_verified(monitor, "U175:V187", settings)
    config = dict(DEFAULTS)
    for r in settings:
        if val(r, 0) in config:
            if not isnum(val(r, 1)):
                raise RuntimeError("PRICE MONITOR!U175:V187: nonnumeric configuration")
            config[r[0]] = float(r[1])
    if config["warning_3d_pp"] <= 0 or config["warning_5d_pp"] <= 0 or config["max_cash_pct"] != 25 or config["min_names"] != 5:
        raise RuntimeError("PRICE MONITOR!U175:V187: unsafe thresholds or game limits")
    write_verified(monitor, "U195:V196", [["ROTATION STATUS", "STARTED"], ["LAST START", prices.serial(now)]])
    portfolio = prices.portfolio_rows(book.worksheet("Portfolio"))
    orders = book.worksheet("Trade Log").get("A8:K1000", value_render_option="UNFORMATTED_VALUE")
    positions = parse_positions(portfolio)
    # A view failure stays visible but must not disable independent price-risk
    # proposals. Preserve existing human targets and orders in either case.
    view_faults = []
    try:
        desk_views.sync_views(book, portfolio)
    except Exception as exc:
        view_faults.append(f"TRADE ANALYSIS / PRICE GATES: view sync failed: {type(exc).__name__}: {str(exc)[:300]}")
    archive = book.worksheet("DAILY CLOSES")
    raw = archive.get(f"A5:J{archive.row_count}", value_render_option="UNFORMATTED_VALUE")
    hist, locations, faults = load_archive(raw)
    faults.extend(view_faults)
    end = prices.last_completed(now, *prices.venue_info("SPY", "")[2:])
    groups = group_metrics(hist, end)
    missing = [t for t, m in groups.items() if m["score"] is None]
    faults.extend(f"DAILY CLOSES!A5:J{archive.row_count}: {t} incomplete matched sessions through {end}" for t in missing)
    faults.extend(f"PRICE MONITOR!N175:Q185: {t} breadth sample incomplete ({m['breadth']['coverage']})" for t,m in groups.items() if t in SECTORS and not m['breadth']['coverage'].startswith('3/3'))
    alerts = detect_warnings(groups, config)
    digest = hashlib.sha256(json.dumps(raw, separators=(",", ":")).encode()).hexdigest()[:20]
    old = monitor.get("A221:S1400", value_render_option="UNFORMATTED_VALUE")
    log = warning_rows(alerts, old, end, now, positions, config, digest)
    if log:
        write_verified(monitor, f"A221:S{220 + len(log)}", log)
    rows = []
    for t, m in groups.items():
        b = m["breadth"]
        rows.append([t, m["name"], m["kind"], prices.serial(end), *[m[f"r{n}"] for n in (3, 5, 10, 20)],
                     *[m[f"rs{n}"] for n in (3, 5, 10, 20)], m["accel"], b.get("positive"), b.get("relative"), b.get("ma20"), b.get("coverage", "Not measured for industry"), m.get("rank"), m["state"]])
    write_verified(monitor, f"A175:S{174 + len(rows)}", rows)
    proposed = proposals(hist, positions, orders, groups, alerts, now, config)
    faults.extend(r[5] for r in proposed if r[10] == "FAILED")
    pool = book.worksheet("CANDIDATE POOL")
    if pool.col_count < 59:
        pool.resize(cols=59)
    valuation_headers = pool.get("BD4:BG4")
    if valuation_headers and any(valuation_headers[0]) and valuation_headers[0] != VALUATION_HEADERS:
        raise RuntimeError("CANDIDATE POOL!BD4:BG4: unexpected valuation schema")
    write_verified(pool, "BD4:BG4", [VALUATION_HEADERS])
    candidates = pool.get(f"A5:BG{pool.row_count}", value_render_option="UNFORMATTED_VALUE")
    ranked = rank_candidates(candidates, hist, end, now, config, positions=positions)
    existing = pool.get("AR4:BC4")
    if existing and any(existing[0]) and existing[0] != CANDIDATE_HEADERS:
        raise RuntimeError("CANDIDATE POOL!AR4:BC4: unexpected existing schema")
    write_verified(pool, "AR4:BC4", [CANDIDATE_HEADERS])
    candidate_output = {}
    for c in sorted(ranked, key=lambda x: x["row"]):
        m, s = c["m"], c["sector"]
        b = groups.get(s, {}).get("breadth", {})
        row = [s, *[m[f"r{n}"] for n in (3, 5, 10, 20)], m["rs5"], m["accel"], b.get("coverage", "Unknown"), c["rank"], c["state"], c["cash_rank"], prices.serial(now)]
        candidate_output[c["row"]] = row
    if candidate_output:
        last = max(candidate_output)
        write_verified(pool, f"AR5:BC{last}", [candidate_output.get(n, [""]*12) for n in range(5,last+1)])
    bench = book.worksheet("ROTATION BENCH")
    # Existing A5:P104 contains an array-formula output: cells without an
    # entered value are not necessarily blank. Keep our block beyond its bound.
    if bench.row_count < 300:
        bench.resize(rows=300)
    existing = bench.get("A140:P140")
    if existing and any(existing[0]) and existing[0] != BENCH_HEADERS:
        raise RuntimeError("ROTATION BENCH!A140:P140: unexpected existing schema")
    write_verified(bench, "A140:P140", [BENCH_HEADERS])
    benchrows = [[c["rank"], c["ticker"], c["sector"], *[c["m"][f"r{n}"] for n in (3, 5, 10, 20)], c["m"]["rs5"], c["m"]["score"], c["state"], c["eligible"], c["event"], c["reviewed"], c["gaps"], c["cash_rank"], prices.serial(now)] for c in ranked]
    if len(benchrows) > 150:
        raise RuntimeError("ROTATION BENCH!A141:P290: research universe exceeds reserved 150 rows; do not overwrite summary")
    if benchrows:
        write_verified(bench, f"A141:P{140+len(benchrows)}", benchrows)
    # Legacy CASH row 243 would otherwise block candidates 103 onward.
    if len(benchrows) <= 102:
        bench.batch_clear(["A243:P243"])
    write_verified(bench, "A299:P299", [["CASH", "0% game price return", "Rank above any unqualified purchase", "", "", "", "", "", "", "READY (subject to 25% cash cap)", "N/A", "No issuer/opening-gap risk", "", "Current held and queued cash are different", "No automatic buy after sector rally", prices.serial(now)]])
    # Optional financial evidence extends the existing decision run. A failed
    # addon cannot disable verified RULE-015/020 proposals.
    financial_state, financial_faults, assessments = "FAILED", [], {}
    base_proposed = [list(row) for row in proposed]
    try:
        write_verified(monitor, "U210:V211", [["FINANCING CALCULATION", "STARTED"], ["FINANCING AS OF", prices.serial(now)]])
        cards = book.worksheet("Decision Cards")
        existing = cards.get("A40:AF40")
        if existing and any(existing[0]) and existing[0] != finance.HEADERS:
            raise RuntimeError("Decision Cards!A40:AF40: unexpected financial schema")
        evidence_ws=book.worksheet("Evidence")
        evidence = evidence_ws.get(f"A8:Z{evidence_ws.row_count}", value_render_option="UNFORMATTED_VALUE")
        signals = book.worksheet("Signals").get("A4:O19", value_render_option="UNFORMATTED_VALUE")
        previous = cards.get("A41:AF60", value_render_option="UNFORMATTED_VALUE")
        sec_ws=book.worksheet("SEC Feed")
        filings=sec_ws.get(f"A8:M{sec_ws.row_count}", value_render_option="UNFORMATTED_VALUE")
        assessments, financial_faults = finance.assess_all(evidence,base_proposed,signals,now,previous,filings)
        proposed = financial_review(base_proposed,assessments,ranked,positions,orders,now,config)
        write_verified(cards,"A40:AF40",[finance.HEADERS])
        financial_rows=finance.output_rows(assessments,proposed)
        write_verified(cards,f"A41:AF{40+len(financial_rows)}",financial_rows)
        if len(financial_rows)<20:
            cards.batch_clear([f"A{41+len(financial_rows)}:AF60"])
        # Only an explicitly labelled suffix is calculation-owned. Keep human
        # recommendation prose and all target/formula columns intact.
        analysis=book.worksheet("TRADE ANALYSIS")
        for n,row in enumerate(analysis.get("A4:F100", value_render_option="UNFORMATTED_VALUE"),4):
            t=val(row,0)
            if t in assessments:
                old=str(val(row,5)).split("\nFINANCIAL STRESS [")[0]
                a=assessments[t]
                text=old+f"\nFINANCIAL STRESS [{now.date()}]: "+finance.summary(a)+" | "+a["options"]+" | "+a["scope"]+" | Reversal: "+a["reversal"]
                write_verified(analysis,f"F{n}:F{n}",[[text]])
        financial_state="VERIFIED"
    except Exception as exc:
        proposed=base_proposed
        financial_faults.append(f"Financial addon failed: {type(exc).__name__}: {str(exc)[:300]}; verified price rules retained")
        for row in proposed:
            row[16]+=" | FIN: unavailable; see financial receipt"
    write_verified(monitor, f"A2051:Q{2050 + len(proposed)}", proposed)
    # Clear only prior own proposals below the current count, avoiding stale names.
    monitor.batch_clear([f"A{2051 + len(proposed)}:Q2070"])
    # Attach a full reason and exact data location to the two-column display.
    # The price writer precedes this step; SYSTEM/B13 remains health-owned.
    desk = book.worksheet("TRADE DESK")
    requests = proposal_note_updates(desk.id, desk.get("B2:B60"), proposed)
    if requests:
        book.batch_update({"requests":requests})
    write_verified(monitor,"U210:V215",[["FINANCING CALCULATION",financial_state],
        ["FINANCING AS OF",prices.serial(now)],["FINANCING HOLDINGS",len(assessments)],
        ["FINANCING INPUT GAPS"," | ".join(financial_faults)[:45000]],
        ["FINANCING MODEL",finance.MODEL],["FINANCING READBACK","Decision Cards A40:AF60; proposal Q; TRADE ANALYSIS F; no orders"]])
    # Verify order/holding invariants immediately before certification.
    if portfolio != prices.portfolio_rows(book.worksheet("Portfolio")) or orders != book.worksheet("Trade Log").get("A8:K1000", value_render_option="UNFORMATTED_VALUE"):
        raise RuntimeError("Portfolio/Trade Log changed during rotation review; refresh required before certifying")
    complete = now if injected_now else datetime.now(prices.TORONTO)
    state = "PARTIAL" if faults else "COMPLETE"
    receipt = [["ROTATION STATUS", state], ["COMPLETED AT", prices.serial(complete)], ["PRICE SESSION", prices.serial(end)],
               ["WARNINGS", len(alerts)], ["FAULTS", " | ".join(faults)[:45000]], ["DEADLINE", "14:45 America/Toronto"],
               ["DECISION MODE", "Price/rotation + financial evidence; independent issuer review; no orders"], ["COVERAGE", f"{11-sum(t in SECTORS for t in missing)}/11 sectors; {len(INDUSTRIES)-sum(t in INDUSTRIES for t in missing)}/{len(INDUSTRIES)} industries"]]
    write_verified(monitor, "U195:V202", receipt)
    runs = monitor.get("A1451:J2000", value_render_option="UNFORMATTED_VALUE")
    dest = 1451 + len(runs)
    if dest > 2000:
        raise RuntimeError("PRICE MONITOR!A1451:J2000: rotation run log full")
    write_verified(monitor, f"A{dest}:J{dest}", [[prices.serial(complete), "LIVE", state, prices.serial(end), 11-sum(t in SECTORS for t in missing), len(INDUSTRIES)-sum(t in INDUSTRIES for t in missing), len(alerts), " | ".join(faults)[:45000], os.getenv("GITHUB_RUN_ID", "local"), os.getenv("GITHUB_SHA", "local")]])
    print(json.dumps({"status": state, "session": str(end), "sectors": 11-sum(t in SECTORS for t in missing), "warnings": len(alerts), "holdings": len(proposed), "candidates": len(ranked), "readback": "verified", "orders": "unchanged", "faults": faults}))
    if faults:
        raise RuntimeError("Rotation coverage incomplete; see PRICE MONITOR!V199")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # A STARTED receipt plus the independent health writer exposes a failed
        # or interrupted run. Never turn it into successful HOLD decisions.
        print(json.dumps({"status": "FAILED", "error": str(exc)[:400]}))
        raise


"""Financial-risk overlay, called by the existing rotation engine; no collector.

Evidence U:Z holds typed, verified issuer facts beside their original provenance.
Decision Cards A40:AF60 is the single calculation/readback surface. Missing
categories remain unknown, weights never move, and this is not a trading score.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
import math

import ft_price_writer as prices

MODEL = "FIN-1 provisional; predictive effectiveness UNVALIDATED"
WEIGHTS = {"financing": .35, "sustainability": .30, "price": .25, "macro": .10}
INPUT_HEADERS = ["Financial fact key", "Financial inputs JSON", "Financial verified at",
                 "Financial expires at", "Industry / accounting basis", "Financial counterevidence"]
HEADERS = ["TICKER", "ASSESSED AT", "FINANCING 0-100", "FINANCING x35%",
           "SUSTAINABILITY 0-100", "SUSTAINABILITY x30%", "PRICE 0-100", "PRICE x25%",
           "MACRO 0-100", "MACRO x10%", "TOTAL RISK", "COVERED WEIGHT", "CONFIDENCE",
           "5Y CDS", "FINANCING BASIS", "CAUSE 1", "CAUSE 2", "CONTRADICTIONS",
           "CHANGE SINCE VERIFIED", "INPUT KEYS / REFERENCES", "STATUS", "REVIEW BAND",
           "HELD", "QUEUED", "TARGET", "SELL pp", "DESTINATION / CONSTRAINT",
           "NEXT EXECUTION", "REVERSAL TEST", "MODEL", "INPUT HASH", "ASSESSMENT JSON"]
PROFILES = {"hyperscaler", "refiner", "upstream", "miner", "pharma", "defence", "software", "neocloud", "retail", "exchange", "insurance", "bank", "semiconductor", "midstream", "industrial_grid"}


def value(row, i, default=""):
    return row[i] if len(row) > i and row[i] not in (None, "") else default


def number(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def financial_inputs(rows, now):
    """No lookahead, expiry/review gate, semantic dedup, conflicting facts excluded.

    A historical public document captured today cannot masquerade as a warning
    that our system actually recorded before today. Replay retains that boundary.
    """
    stamp = prices.serial(now)
    candidates, faults = {}, []
    for n, row in enumerate(rows, 8):
        key = str(value(row, 20))
        if not key:
            continue
        try:
            d = json.loads(value(row, 21))
            if (value(row, 9) not in {"Valid", "Context"} or value(row, 16) not in {"Full", "Excerpt", "Data"}
                or not str(value(row, 4)).startswith("https://") or not value(row, 24)
                or not all(number(value(row, i)) for i in (2, 15, 22, 23))
                or not 0 < row[2] <= row[15] <= row[22] <= stamp <= row[23]
                or d.get("ticker") != value(row, 1) or d.get("kind") not in {"fundamentals", "credit"}
                or d.get("profile") not in PROFILES or d.get("unit") != "USD_millions"
                or prices.day_of(d["period_end"]) > prices.day_of(row[2])):
                raise ValueError("unverified, expired, future or mismatched observation")
            d = dict(d, key=key, published=row[2], captured=row[15], verified=row[22], expires=row[23],
                     source=row[4], location=f"Evidence!A{n}:Z{n}", basis=row[24], counterpoint=value(row,25))
            semantic = (d["ticker"], d["kind"], d["period_end"])
            candidates.setdefault(semantic, []).append(d)
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
            faults.append(f"Evidence!U{n}:Z{n}: excluded invalid/expired financial input")
    valid = []
    for semantic, group in candidates.items():
        # Canonical financial content, not number of URLs or publishers.
        ignored = {"key", "published", "captured", "verified", "expires", "source", "location", "basis", "counterpoint"}
        facts = {json.dumps({k:v for k,v in d.items() if k not in ignored}, sort_keys=True) for d in group}
        if len(facts) != 1:
            faults.append(f"{group[0]['location']}: conflicting financial facts for {semantic}; excluded")
        else:
            valid.append(max(group, key=lambda d:d["verified"]))
    latest = {}
    for d in valid:
        k = (d["ticker"],d["kind"])
        if k not in latest or (d["period_end"],d["published"]) > (latest[k]["period_end"],latest[k]["published"]):
            latest[k] = d
    return latest, faults


def sustainability(d):
    """Cash funding capacity, not a penalty for growth capex or a valuation model.

    Comparisons must use the same issuer, duration, currency and cash-capex basis.
    Working-capital-sensitive refiners/miners use a >=6-month observation.
    Noncash lease additions and reported non-GAAP FCF stay separately labelled.
    """
    if not d:
        return None, "Cash-flow/capex evidence UNKNOWN", False
    if d.get('profile') in {'bank','insurance'}:
        return None, "Bank/insurer: industrial cash-capex score not applicable; use verified underwriting, capital and credit ratios", False
    ocf, capex = d.get("ocf"), d.get("capex")
    if (not number(ocf) or not number(capex) or capex < 0 or not d.get("capex_basis")
        or not number(d.get("period_months")) or d["period_months"] not in {3,6,9,12}
        or (d["profile"] in {"refiner","miner","upstream","defence"} and d["period_months"] < 6)):
        return None, "Comparable cash-capex/OCF basis incomplete", False
    ratio = capex / ocf if ocf > 0 else None
    score = 70 if ocf <= 0 else 70 if ratio > 1.25 else 60 if ratio > 1 else 45 if ratio > .8 else 20
    reasons = [f"{d['period_months']}m cash capex/OCF {ratio:.1%}" if ratio is not None else "non-positive operating cash flow"]
    prior = d.get("previous", {})
    deteriorating = False
    if (number(prior.get("ocf")) and number(prior.get("capex")) and prior["ocf"] > 0
        and prior["capex"] >= 0 and prior.get("period_months") == d["period_months"]
        and prior.get("capex_basis") == d["capex_basis"]):
        old_ratio = prior["capex"] / prior["ocf"]
        reasons.append(f"previous comparable {old_ratio:.1%}")
        deteriorating = ocf - capex < prior["ocf"] - prior["capex"] and (ratio is None or ratio > old_ratio + .15)
        if deteriorating:
            score = max(score, 55)
        if ocf < prior["ocf"] * .8 and capex >= prior["capex"]:
            score = max(score,70)
    cut = d.get("cash_guidance_cut_pct")
    if number(cut) and cut > 0 and d.get("guidance_basis"):
        score = max(score, 80 if cut >= 20 else 60)
        reasons.append(f"cash-flow guidance cut {cut:g}%")
        deteriorating = True
    # Positive cash surplus and increased spending alone cannot produce >=60.
    if ocf > capex and not (number(cut) and cut > 0):
        score = min(score,55)
    reasons.append(f"cash surplus {ocf-capex:g} USDm; {d['profile']} / {d['capex_basis']}")
    if d["profile"] == "pharma":
        acquisitions=d.get("acquisition_cash",0); iprd=d.get("purchased_iprd_cash",0)
        if number(acquisitions) and number(iprd) and acquisitions+iprd>max(0,ocf-capex):
            score=max(score,45)
            reasons.append("Acquisition/IPR&D cash exceeds PPE cash surplus; separate one-off funding review, not recurring capex distress")
    return score, "; ".join(reasons), deteriorating and score >= 60


def financing(d, credit):
    """Use strongest independent credit observation once; never sum same risk.

    CDS widening alone, trading volume and uncertain/illiquid quotes cannot
    certify genuine financing stress. Debt issuance alone is not deterioration.
    """
    options, cds = [], "UNKNOWN — no verified current five-year observation"
    if credit and number(credit.get("cds_5y_bps")) and credit["cds_5y_bps"]>=0 and credit.get("quote_quality") == "verified":
        cds = f"{credit['cds_5y_bps']:g} bp as of {credit['period_end']}"
        if credit.get("thin_market") or credit.get("hedging_only"):
            cds += "; hedging/liquidity distortion; not a scored distress fact"
        elif credit.get("independent_credit_confirmation") and number(credit.get("change_bps",0)):
            bps, change = credit["cds_5y_bps"], credit.get("change_bps",0)
            severity = 85 if bps >= 500 else 65 if bps >= 250 or change >= 100 else 40 if change >= 25 else 20
            options.append((severity,"Verified CDS plus independent credit confirmation",severity>=65))
    for obs in [x for x in (d,credit) if x]:
        event = obs.get("financing_event")
        if event in {"missed_payment","covenant_breach","going_concern","failed_refinancing"} and obs.get("event_verified"):
            options.append((95, f"Verified issuer event: {event}", True))
        # Restrictive gate: do not pretend cash / current debt captures all
        # lease, interest, purchase or committed financing obligations.
        if (obs.get("obligations_scope") == "all_material_12m_obligations"
            and number(obs.get("available_liquidity")) and number(obs.get("obligations_12m"))
            and obs["available_liquidity"] >= 0 and obs["obligations_12m"] > 0):
            coverage = obs["available_liquidity"] / obs["obligations_12m"]
            severity = 90 if coverage < .5 else 70 if coverage < 1 else 40 if coverage < 2 else 15
            options.append((severity,f"Filing liquidity/12m obligations {coverage:.2f}x; ALTERNATIVE, not CDS",coverage<1))
        if (number(obs.get("bond_spread_bps")) and number(obs.get("bond_change_bps"))
            and obs.get("bond_id") and obs.get("quote_quality") == "verified"
            and obs.get("independent_credit_confirmation") and not obs.get("thin_market")):
            severity = 85 if obs["bond_spread_bps"]>=500 else 65 if obs["bond_change_bps"]>=100 else 40
            options.append((severity,f"Bond {obs['bond_id']} spread; ALTERNATIVE, not CDS",severity>=65))
    if not options:
        details=[]
        if d:
            details=[f"{k}={d[k]} USDm" for k in ("debt","long_term_debt","current_debt","net_debt","cash","cash_and_marketable_securities","cash_and_short_investments","interest_expense","interest_net") if number(d.get(k))]
        return None,"UNKNOWN — no complete current credit/obligation measure; dated filing alternatives (not CDS): "+("; ".join(details) if details else "unavailable"),False,cds
    severity, reason, confirmed = max(options,key=lambda x:x[0])
    return severity,reason,confirmed,cds


def price_severity(proposal):
    # Reuse verified rotation output; never download or recompute its prices.
    if value(proposal,10) != "VERIFIED PRIOR CLOSE":
        return None
    why = str(value(proposal,5))
    if "price-risk reduction justified" in why:
        return 85 if "desired cut" in why and value(proposal,3)=="ROTATE" else 70
    r5, rs5 = value(proposal,13), value(proposal,14)
    if not number(r5) or not number(rs5):
        return None
    return 45 if r5<0 and rs5<0 else 30 if "RULE-015" in why else 15


def macro_severity(signals, now):
    # Refer to existing statuses, grouping correlated HY/IG/rates/conditions
    # by their maximum rather than counting the same tightening repeatedly.
    required = {"BAMLH0A0HYM2":7,"BAMLC0A0CM":7,"DFII10":7,"NFCI":14}
    found = {}
    for row in signals:
        key = value(row,0)
        if key not in required:
            continue
        try:
            age = (now.date()-prices.day_of(value(row,5))).days
            status = value(row,11)
            if (0<=age<=required[key] and status in {"Green","Amber","Red"} and str(value(row,13)).startswith("https://")
                and number(value(row,12)) and 0<=prices.serial(now)-row[12]<=4):
                found[key] = {"Green":15,"Amber":45,"Red":80}[status]
        except ValueError:
            pass
    return max(found.values()) if set(found)==set(required) else None


def assess(ticker, observations, proposal, signals, now, previous=None):
    d, credit = observations.get((ticker,"fundamentals")), observations.get((ticker,"credit"))
    fs, fwhy, confirmed, cds = financing(d,credit)
    ss, swhy, deteriorating = sustainability(d)
    scores = dict(financing=fs,sustainability=ss,price=price_severity(proposal),macro=macro_severity(signals,now))
    contribution = {k:round(v*WEIGHTS[k],2) if v is not None else None for k,v in scores.items()}
    coverage = round(sum(WEIGHTS[k] for k,v in scores.items() if v is not None),2)
    total = round(sum(contribution.values()),1) if coverage==1 else None
    band = "INCOMPLETE" if total is None else "HOLD supportive" if total<40 else "WATCH / investigate" if total<60 else "EVALUATE 2-5pp TRIM" if total<80 else "EVALUATE larger TRIM / EXIT"
    # No total-score-only trade. Confirmed issuer stress or deteriorating cash
    # generation AND adverse existing price evidence are separate reasons.
    material = confirmed or (deteriorating and scores["price"] is not None and scores["price"]>=45)
    changes = "First verified assessment; no backdated warning"
    if previous:
        deltas = [f"{k} {previous.get('scores',{}).get(k)} -> {v}" for k,v in scores.items() if previous.get('scores',{}).get(k)!=v]
        changes = "; ".join(deltas) or "No category change since previous verified assessment"
    facts = [x for x in (d,credit) if x]
    refs = [f"{x['key']} ({x['location']}; published {prices.day_of(x['published'])}; expires {prices.day_of(x['expires'])})" for x in facts]
    refs += ["PRICE MONITOR A2051:Q2070; Signals A4:O9"]
    explanations = {"financing":fwhy, "sustainability":swhy,
        "price":f"Existing price/rotation severity {scores['price']}; 5D {value(proposal,13)}%, relative {value(proposal,14)}pp",
        "macro":f"Existing Signals credit/rates/conditions status severity {scores['macro']} (shared market pressure)"}
    leaders = sorted((k for k,v in contribution.items() if v is not None),key=lambda k:-contribution[k])
    causes = ([explanations[k] for k in leaders]+[fwhy,swhy])[:2]
    return {"ticker":ticker,"scores":scores,"contributions":contribution,"coverage":coverage,"total":total,
            "confidence":"MODERATE" if coverage==1 else "LOW / incomplete", "band":band,"cds":cds,
            "basis":fwhy,"causes":causes,"sustainability_detail":swhy,"counterpoint":" | ".join(x['counterpoint'] for x in facts) or "Issuer counterevidence unavailable",
            "change":changes,"references":" | ".join(refs),"material":material,
            "reversal":"New issuer cash/credit evidence reverses deterioration; compare next matched closes and requalify destination",
            "hash":hashlib.sha256(json.dumps(facts,sort_keys=True).encode()).hexdigest()[:20],
            "model":MODEL,"asof":prices.serial(now)}


def exclude_superseded(observations,filings,now):
    """Existing SEC discoveries invalidate older fundamentals, never parse anew."""
    result=dict(observations);faults=[]
    for row in filings:
        t=value(row,1);d=result.get((t,"fundamentals"))
        if not d or value(row,4) not in {"10-K","10-K/A","10-Q","10-Q/A","20-F","20-F/A"}:
            continue
        try:
            if (number(value(row,0)) and row[0]<=prices.serial(now) and number(value(row,5))
                and prices.day_of(d["published"])<prices.day_of(row[5])<=now.date()
                and prices.day_of(value(row,6))>=prices.day_of(d["period_end"])
                and (prices.day_of(value(row,6))>prices.day_of(d["period_end"])
                     or prices.day_of(row[5])>prices.day_of(d["verified"])
                     or str(value(row,4)).endswith("/A"))):
                result.pop((t,"fundamentals"))
                faults.append(f"{t}: newer SEC issuer information; Research Intake must verify replacement facts")
        except ValueError:pass
    return result,faults


def assess_all(evidence, proposed, signals, now, previous_rows=(),filings=()):
    observations, faults = financial_inputs(evidence,now)
    observations,updates=exclude_superseded(observations,filings,now)
    faults+=updates
    previous={}
    for row in previous_rows:
        try:
            if value(row,20) in {"COMPLETE","INCOMPLETE"}:
                previous[row[0]]=json.loads(row[31])
        except (ValueError,IndexError,TypeError):
            pass
    return {row[0]:assess(row[0],observations,row,signals,now,previous.get(row[0])) for row in proposed},faults


def output_rows(assessments, proposed):
    result=[]
    for p in proposed:
        if p[0] not in assessments:
            continue  # paired new purchase is explained by the incumbent card
        a=assessments[p[0]]; s=a["scores"]; c=a["contributions"]
        result.append([p[0],a["asof"],s["financing"],c["financing"],s["sustainability"],c["sustainability"],
                       s["price"],c["price"],s["macro"],c["macro"],a["total"],a["coverage"],a["confidence"],a["cds"],a["basis"],
                       *a["causes"],a["counterpoint"],a["change"],a["references"],"COMPLETE" if a["total"] is not None else "INCOMPLETE",a["band"],
                       p[1],p[2],p[4],round((p[1]-p[4])*100,4),p[15],p[9],a["reversal"],MODEL,a["hash"],json.dumps(a,sort_keys=True)])
    return result


def summary(a):
    score = f"risk {a['total']:.1f}/100" if a["total"] is not None else "risk INCOMPLETE"
    return f"FIN: {score}; coverage {a['coverage']:.0%}; {a['confidence']}; sustainability {a['scores']['sustainability']}; {a['sustainability_detail'].split(';')[0]}; CDS UNKNOWN" if a["cds"].startswith("UNKNOWN") else f"FIN: {score}; coverage {a['coverage']:.0%}; {a['confidence']}; {a['cds']}"


def reason_updates(rows, assessments, now):
    """Bound prose updates to the decision table; fail before any mutation."""
    end = next((i for i, row in enumerate(rows) if value(row, 0) == "TOTAL SELL"), None)
    if end is None:
        raise ValueError("TRADE ANALYSIS!A4:F100: TOTAL SELL boundary missing")
    names = [value(r, 0) for r in rows[:end] if value(r, 0)]
    if len(names) != len(set(names)):
        raise ValueError("TRADE ANALYSIS!A4:F100: duplicate decision ticker")
    updates = []
    for n, row in enumerate(rows[:end], 4):
        ticker = value(row, 0)
        if ticker not in assessments:
            continue
        old = value(row, 5)
        if not isinstance(old, str):
            raise ValueError(f"TRADE ANALYSIS!F{n}: expected decision prose, found numeric input")
        a = assessments[ticker]
        old = old.split("\nFINANCIAL STRESS [")[0]
        updates.append((n, old + f"\nFINANCIAL STRESS [{now.date()}]: " + summary(a)
                        + " | " + a["options"] + " | " + a["scope"]
                        + " | Reversal: " + a["reversal"]))
    return updates


def health(now, receipt, rows, held):
    fields = {value(r,0):value(r,1) for r in receipt}
    faults=[]
    stamp=fields.get("FINANCING AS OF")
    if fields.get("FINANCING CALCULATION")!="VERIFIED" or not number(stamp) or not int(prices.serial(now))<=stamp<=prices.serial(now):
        return ["PRICE MONITOR!U210:V215: financial calculation/readback missing, failed or stale; price rules remain independent"]
    actual={value(r,0):r for r in rows if value(r,0)}
    for ticker,w,q in held:
        row=actual.get(ticker,[])
        if not row or value(row,22)!=w or value(row,23)!=q or value(row,1)!=stamp:
            faults.append(f"Decision Cards!A41:AF60: {ticker} missing or holdings/assessment mismatch")
            continue
        try:
            a=json.loads(value(row,31))
            s=a["scores"]; c=a["contributions"]
            covered=sum(WEIGHTS[k] for k,v in s.items() if v is not None)
            for k,v in s.items():
                if v is not None and (not number(v) or not 0<=v<=100 or abs(c[k]-round(v*WEIGHTS[k],2))>1e-8):
                    raise ValueError("category arithmetic")
                if v is None and c[k] is not None:
                    raise ValueError("unknown contribution")
            total=round(sum(v for v in c.values() if v is not None),1) if abs(covered-1)<1e-8 else None
            if abs(covered-a["coverage"])>1e-8 or total!=a["total"] or value(row,10,None)!=total or value(row,11)!=a["coverage"]:
                raise ValueError("coverage/total")
            for i,k in enumerate(WEIGHTS):
                if value(row,2+i*2,None)!=s[k] or value(row,3+i*2,None)!=c[k]:
                    raise ValueError("display/category mismatch")
        except (ValueError,TypeError,KeyError):
            faults.append(f"Decision Cards!A41:AF60: {ticker} financial arithmetic/readback mismatch")
    return faults


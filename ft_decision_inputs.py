"""Repair the existing decision-card inputs; reuse existing collectors only.

Called by ft_sector_rotation after its independent price-loss proposals. Owns
Decision Cards A65:AF104 and A110:O124, and formula links in A4:AF39. Never
writes allocations, orders, research receipts or historical decision snapshots.
"""
from __future__ import annotations

import hashlib
import json
import re
from urllib.parse import urlsplit, urlunsplit

import ft_price_writer as p
import ft_financial_stress as f
from ft_rotation_universe import COMPANY_SECTOR, SECTORS

WEIGHTS = (.45, .35, .20)
HEADERS = ["TICKER", "CAPTURED AT", "PRICE SESSION", "SECTOR BENCHMARK", "CLOSE",
           "1D %", "5D %", "20D %", "60D %", "5D-SPY pp", "20D-SPY pp", "60D-SPY pp",
           "5D-SECTOR pp", "20D-SECTOR pp", "60D-SECTOR pp", "PRICE SCORE", "COMPANY SCORE",
           "MACRO SCORE", "45/35/20 SCORE", "ELIGIBLE EVIDENCE IDS", "WEIGHTED COMPANY SUM",
           "REVENUE YoY %", "OPERATING MARGIN %", "MARGIN CHANGE pp", "CASH FCF USDm",
           "OCF / NET INCOME", "VALUATION MULTIPLE", "VALUATION BASIS / SOURCE",
           "FINANCING EVIDENCE", "MISSING / ACCOUNTING LIMITS", "UNIQUE COMPANY EVENTS", "INPUT HASH"]
SECTOR_HEADERS = ["GROUP", "PRICE SESSION", "5D-SPY pp", "20D-SPY pp", "60D-SPY pp",
                  "5D %", "20D %", "PRICE CLASSIFICATION", "EARNINGS SAMPLE COVERAGE",
                  "SUPPORTIVE COMPANY INPUTS", "ADVERSE COMPANY INPUTS", "EARNINGS CONFIRMATION",
                  "CAPTURED AT", "LIMITATION", "INPUT HASH"]


def canonical_url(url):
    u = urlsplit(str(url))
    return urlunsplit((u.scheme.lower(), u.netloc.lower(), u.path.rstrip('/'), '', ''))


def company_inputs(ticker, evidence, observations, now):
    """Expiry, explicit direction, exact tokens and event dedup; unknown != zero.

    Typed issuer facts require an explicitly reviewed company_direction and
    rationale. Calendar-only evidence (direction zero, no typed assessment) is
    not a neutral fundamental score. Context is never blindly promoted.
    """
    stamp = p.serial(now)
    events = {}
    typed = observations.get((ticker, "fundamentals"))
    if typed and f.number(typed.get("company_direction")) and -1 <= typed["company_direction"] <= 1 and typed.get("company_rationale"):
        events[canonical_url(typed['source'])] = (typed['company_direction'], typed['key'])
    for row in evidence:
        tokens = re.split(r"\s*/\s*", str(f.value(row, 1)))
        if ticker not in tokens or any(t.lower() == "macro" for t in tokens):
            continue
        # K is the existing source/calibration and quality eligibility gate.
        if (f.value(row,9) != "Valid" or f.value(row,16) not in {"Full","Excerpt","Data"}
            or not all(f.number(f.value(row,i)) for i in (2,6,7,10,11,15))
            or not 0 < row[2] <= row[15] <= stamp or not 0 <= int(stamp)-int(row[2]) <= row[7]
            or row[10] <= 0 or row[6] == 0 or not -1 <= row[6] <= 1
            or not str(f.value(row,4)).startswith("https://")):
            continue
        # A typed record supersedes the narrative of that same issuer event.
        key = canonical_url(row[4])
        if key in events:
            continue
        events[key] = (row[11], row[0])
    total = sum(v[0] for v in events.values())
    return {"score": max(-1., min(1., total/2)) if events else None,
            "sum": total if events else None, "count": len(events),
            "ids": "; ".join(v[1] for v in events.values()) or "UNKNOWN — no eligible directional company assessment"}


def macro_input(regime, signals, now):
    # Preserve the production mapping; reject stale or missing source series.
    if f.macro_severity(signals, now) is None:
        return None
    states = {f.value(row,0): f.value(row,3) for row in regime}
    keys = ("RG-CREDIT", "RG-VALUATION", "RG-FINCOND")
    mappings = ({"Easing":1,"Tightening":-1,"Stable":0},
                {"Easing pressure":1,"Restrictive":-1,"Elevated":-.4,"Stable":0,"Neutral":0},
                {"Easing":1,"Tightening":-1,"Stable":0})
    if any(states.get(k) not in m for k,m in zip(keys,mappings)):
        return None
    return sum(w*m[states[k]] for w,k,m in zip((.4,.4,.2),keys,mappings))


def fundamentals(d, close):
    out = dict(revenue=None, margin=None, margin_change=None, fcf=None,
               conversion=None, multiple=None, valuation="UNKNOWN", financing="UNKNOWN", gaps=[])
    if not d:
        out['gaps'].append("Verified comparable issuer financials missing; not a negative fundamental score")
        return out
    inc = d.get('income', {})
    revenue, old, op, prior_op = (inc.get(k) for k in ('revenue','previous_revenue','operating_income','previous_operating_income'))
    same = inc.get('basis') in {'GAAP','IFRS','adjusted_reconciled'} and inc.get('period_months') in {3,6,9,12}
    if same and f.number(revenue) and revenue > 0:
        if f.number(old) and old > 0:
            out['revenue'] = 100*(revenue/old-1)
        if f.number(op):
            out['margin'] = 100*op/revenue
            if f.number(old) and old > 0 and f.number(prior_op):
                out['margin_change'] = out['margin']-100*prior_op/old
    if d.get('profile') in {'bank','insurance'}:
        out['gaps'].append("Bank/insurer: cash-capex and industrial margin ratios not comparable; use underwriting/ROE/capital metrics in source assessment")
    elif f.number(d.get('ocf')) and f.number(d.get('capex')) and d['capex'] >= 0 and d.get('capex_basis'):
        out['fcf'] = d['ocf']-d['capex']
        if (same and inc.get('period_months') == d.get('period_months')
            and inc.get('period_end') == d.get('period_end') and f.number(inc.get('net_income')) and inc['net_income'] > 0):
            out['conversion'] = d['ocf']/inc['net_income']
    valuation = d.get('valuation', {})
    # Never annualise a single quarter, mix share splits or invent consensus.
    if (f.number(close) and f.number(valuation.get('denominator')) and valuation['denominator'] > 0
        and valuation.get('basis') and valuation.get('share_basis_verified') is True):
        out['multiple'] = close/valuation['denominator']
        out['valuation'] = valuation['basis'] + "; price at current verified close / dated issuer denominator; " + d['source']
    _, out['financing'], _, _ = f.financing(d, None)
    if out['revenue'] is None: out['gaps'].append('Comparable revenue growth missing')
    if out['margin'] is None: out['gaps'].append('Comparable operating margin missing')
    if out['conversion'] is None: out['gaps'].append('Comparable cash conversion missing')
    if out['multiple'] is None: out['gaps'].append('Verified same-share-basis forward/TTM/book denominator missing')
    out['gaps'].append('Current consensus revisions, forward peer multiples and issuer credit spreads unavailable unless explicitly sourced')
    return out


def build_inputs(tickers, hist, evidence, signals, regime, now):
    # Local import avoids a module cycle: this is part of the existing engine.
    import ft_sector_rotation as r
    observations, faults = f.financial_inputs(evidence, now)
    end = p.last_completed(now, *p.venue_info('SPY','')[2:])
    macro = macro_input(regime, signals, now)
    output, companies = [], {}
    for ticker in dict.fromkeys(tickers):
        m = r.metric(hist, ticker, end)
        sector = COMPANY_SECTOR.get(ticker, 'SPY')
        b = r.metric(hist, sector, end)
        daily = r.returns(hist,ticker,end,1,'LSE' if ticker.endswith(':LSE') else 'US')
        price = max(-1., min(1., .2*daily/3+.35*m['rs5']/5+.45*m['rs20']/10)) if all(f.number(x) for x in (daily,m['rs5'],m['rs20'])) else None
        company = company_inputs(ticker, evidence, observations, now)
        companies[ticker] = company
        combined = sum(w*v for w,v in zip(WEIGHTS,(company['score'],price,macro))) if all(f.number(v) for v in (company['score'],price,macro)) else None
        data = fundamentals(observations.get((ticker,'fundamentals')),m['close'])
        gaps = list(data['gaps'])
        if company['score'] is None: gaps.append('Company score UNKNOWN; calendar/preview is not a fundamental assessment')
        if price is None: gaps.append('Price score UNKNOWN: incomplete matched sessions')
        if macro is None: gaps.append('Macro score UNKNOWN: invalid/stale underlying series')
        row = [ticker,p.serial(now),p.serial(end),sector,m['close'],daily,m['r5'],m['r20'],m['r60'],
               m['rs5'],m['rs20'],m['rs60'],
               *[m[f'r{n}']-b[f'r{n}'] if all(f.number(x) for x in (m[f'r{n}'],b[f'r{n}'])) else None for n in (5,20,60)],
               price,company['score'],macro,combined,company['ids'],company['sum'],
               data['revenue'],data['margin'],data['margin_change'],data['fcf'],data['conversion'],data['multiple'],
               data['valuation'],data['financing'],'; '.join(gaps),company['count']]
        row.append(hashlib.sha256(json.dumps(row[2:],sort_keys=True).encode()).hexdigest())
        output.append(row)
    return output, companies, faults


def sector_rows(hist, evidence, now):
    import ft_sector_rotation as r
    end = p.last_completed(now, *p.venue_info('SPY','')[2:])
    observations, _ = f.financial_inputs(evidence, now)
    rows = []
    for ticker, (_,members) in SECTORS.items():
        m = r.metric(hist,ticker,end)
        cs = [company_inputs(t,evidence,observations,now)['score'] for t in members]
        known = [x for x in cs if f.number(x)]
        positive, negative = sum(x>0 for x in known), sum(x<0 for x in known)
        state = 'MISSING' if any(m[k] is None for k in ('r5','r20','rs5','rs20')) else (
            'SUSTAINED PRICE LEADERSHIP' if m['r5']>0 and m['rs5']>0 and m['rs20']>0 else
            'REBOUND ONLY' if m['r5']>0 and m['rs5']>0 and m['rs20']<=0 else
            'RELATIVE DEFENCE IN DECLINE' if m['r5']<=0 and m['rs5']>0 else 'LAGGING / MIXED')
        confirm = 'SUPPORTIVE SAMPLE' if len(known)==len(members) and positive>=2 and negative==0 else 'NOT ESTABLISHED — price warning remains active'
        rows.append([ticker,p.serial(end),m['rs5'],m['rs20'],m['rs60'],m['r5'],m['r20'],state,
                     f'{len(known)}/{len(members)} fixed sample',positive,negative,confirm,p.serial(now),
                     'Sample, not sector-wide earnings or capital flows; 60D missing remains blank',
                     hashlib.sha256(json.dumps([ticker,m,cs],sort_keys=True).encode()).hexdigest()])
    return rows


def formula_updates(sheet_id, cards, tickers, portfolio):
    import ft_trade_desk as desk
    existing = {str(row[0]): (n,row) for n,row in enumerate(cards,4) if row and row[0]}
    order = list(dict.fromkeys([*existing,*tickers]))
    if len(order)>36: raise ValueError('Decision Cards!A4:AF39: capacity; refusing overwrite of financing block')
    requests=[]
    for n,t in enumerate(order,4):
        old = existing.get(t,(None,[]))[1]
        get = lambda col: f'IFERROR(IF(ISBLANK(INDEX(${col}$66:${col}$104,MATCH($A{n},$A$66:$A$104,0))),"",INDEX(${col}$66:${col}$104,MATCH($A{n},$A$66:$A$104,0))),"")'
        formulas = {1:t,2:f'=IFERROR(XLOOKUP(A{n},Portfolio!A8:A100,Portfolio!B8:B100),IFERROR(XLOOKUP(A{n},\'CANDIDATE POOL\'!A5:A150,\'CANDIDATE POOL\'!B5:B150),A{n}))',
            3:f'=IFERROR(XLOOKUP(A{n},Portfolio!A8:A100,Portfolio!C8:C100),0)',
            4:f'=IFERROR(XLOOKUP(A{n},Portfolio!A8:A100,Portfolio!D8:D100),0)',
            7:'='+get('AE'),17:'='+get('U'),23:'='+get('B'),24:'='+get('P'),
            25:f'=IF(ISNUMBER(X{n}),IF(X{n}<=-0.65,"LOSS-CONTROL REVIEW",IF(X{n}>0.15,"SUPPORTIVE","MIXED")),"MISSING")',
            26:'='+get('Q'),27:'='+get('R'),28:'='+get('S'),
            30:f'=IF(AB{n}="","",MIN(0.95,(0.45*ABS(Z{n})+0.35*ABS(X{n})+0.2*ABS(AA{n}))*IF(AC{n}="ALIGNED",1,IF(AC{n}="DIVERGENCE",0.65,0.8))+MIN(G{n},4)*0.04))'}
        if t not in existing:
            # Reuse the established formula template by relative row translation.
            # New rows have independent ticker lookups, never guessed positions.
            formulas.update({8:0,13:'MONITOR',14:'Review current source-backed assessment and existing price-loss gates',16:'Company/price/macro input mapping; no order placed',18:f'=IF(AD{n}="","Unscored",IF(AD{n}>=0.7,"High",IF(AD{n}>=0.45,"Medium","Low")))',19:f'=AE{n}',20:f'=C{n}',
                29:f'=IF(AB{n}="","NO DATA",IF(AND(ABS(Z{n})<0.15,ABS(X{n})<0.15),"MIXED",IF(SIGN(Z{n})=SIGN(X{n}),IF(OR(SIGN(AA{n})=0,SIGN(AA{n})=SIGN(AB{n})),"ALIGNED","MACRO CONFLICT"),"DIVERGENCE")))',
                31:f'=IF(AND(C{n}=0,D{n}=0),IF(AND(ISNUMBER(AB{n}),AB{n}>=0.35,X{n}>0.15),"ROTATION CANDIDATE","RESERVE"),IF(OR(AND(ISNUMBER(X{n}),X{n}<=-0.65),AND(ISNUMBER(AB{n}),ISNUMBER(Z{n}),AB{n}<=-0.35,Z{n}<=0)),"REVIEW TRIM",IF(OR(Z{n}="",X{n}="",AA{n}=""),"VERIFY",IF(H{n}>0,"REVIEW",IF(AND(AB{n}>=0.35,Z{n}>=0),"REVIEW ADD","HOLD")))))',
                32:f'=IF(AB{n}="","Verify missing inputs; see Decision Cards A65:AF104","Company "&TEXT(Z{n},"0.00")&" · Price "&TEXT(X{n},"0.00")&" · Macro "&TEXT(AA{n},"0.00")&"; heuristic, not a probability")'})
        for col,value in formulas.items(): requests.append(desk.cell_request(sheet_id,n,col,value))
    requests.append(desk.cell_request(sheet_id,3,7,'Eligible unique company events (unexpired)'))
    requests.append(desk.cell_request(sheet_id,3,30,'Heuristic support (not probability)'))
    return requests


def sync(book, hist, evidence, signals, portfolio, candidates, now):
    import ft_sector_rotation as r
    ws=book.worksheet('Decision Cards')
    cards=ws.get('A4:AF39',value_render_option='UNFORMATTED_VALUE')
    # All recorded names plus the fixed review universe and five researched alternatives.
    bench=book.worksheet('ROTATION BENCH').get('C5:C15',value_render_option='UNFORMATTED_VALUE')
    tickers=list(dict.fromkeys([row[0] for row in portfolio if row and row[0]]+[row[0] for row in bench if row]+['CRWD','CRM','KMI','JPM','NVDA']))
    regime=book.worksheet('Regime').get('A4:D9',value_render_option='UNFORMATTED_VALUE')
    output,_,faults=build_inputs(tickers,hist,evidence,signals,regime,now)
    if len(output)>39: raise ValueError('Decision Cards!A66:AF104: input capacity exceeded')
    for address,headers in [('A65:AF65',HEADERS),('A110:O110',SECTOR_HEADERS)]:
        existing=ws.get(address)
        if existing and any(existing[0]) and existing[0]!=headers: raise ValueError(f'Decision Cards!{address}: unexpected existing data')
        r.write_verified(ws,address,[headers])
    r.write_verified(ws,f'A66:AF{65+len(output)}',output)
    if len(output)<39: ws.batch_clear([f'A{66+len(output)}:AF104'])
    r.write_verified(ws,'A111:O121',sector_rows(hist,evidence,now))
    book.batch_update({'requests':formula_updates(ws.id,cards,tickers,portfolio)})
    actual=ws.get('A4:AF39',value_render_option='UNFORMATTED_VALUE')
    by_ticker={row[0]:row for row in actual if row}
    for row in output:
        card=by_ticker.get(row[0],[])
        for dest,src in ((23,15),(25,16),(26,17),(27,18)):
            expected='' if row[src] is None else row[src]
            if f.value(card,dest)!=expected and not (f.number(expected) and f.number(f.value(card,dest)) and abs(card[dest]-expected)<1e-8):
                raise RuntimeError(f'Decision Cards {row[0]} score readback failed at column {dest+1}')
    return {'held':sum(f.number(f.value(x,2)) and x[2]>0 for x in portfolio),
            'mapped':len(output),'complete':sum(f.number(x[18]) for x in output),
            'unknown_company':[x[0] for x in output if x[16] is None],
            'excluded_financial_inputs':faults,'weights':WEIGHTS,'readback':'verified'}

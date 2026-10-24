"""Repair the existing scenario panel without inventing issuer sensitivities.

Mapping completeness and scenario-input completeness are separate. Unknown
flags/shocks never behave as zero, and neither creates an investment order.
"""
import math
from ft_rotation_universe import COMPANY_SECTOR


def val(row, i, default=""):
    return row[i] if len(row) > i else default


def numeric(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def layout(rows):
    total = next((i+1 for i,r in enumerate(rows) if val(r,0)=="TOTAL SELL"), None)
    header = next((i+1 for i,r in enumerate(rows)
                   if val(r,0)=="Ticker" and val(r,3)=="AI-linked flag"), None)
    end = next((i+1 for i,r in enumerate(rows) if header and i+1>header
                and str(val(r,0)).startswith("Assumptions")), None)
    if not total or not header or not end or total >= header:
        raise ValueError("TRADE ANALYSIS: missing decision/scenario boundary; no write")
    return total, header, end


def cell(sheet_id, row, col, value):
    entry = {} if value == "" else {"userEnteredValue": {
        "formulaValue" if isinstance(value,str) and value.startswith("=") else
        "numberValue" if numeric(value) else "stringValue": value}}
    return {"updateCells":{"start":{"sheetId":sheet_id,"rowIndex":row-1,
        "columnIndex":col-1},"rows":[{"values":[entry]}],"fields":"userEnteredValue"}}


def requests(sheet_id, rows):
    total, header, end = layout(rows)
    matrix = [r for r in rows[3:total-1] if val(r,0)]
    active = [r for r in matrix if any(numeric(val(r,c)) and r[c]>0 for c in (2,3))]
    if len(active)>30 or len({r[0] for r in active})!=len(active):
        raise ValueError("TRADE ANALYSIS: invalid active scenario universe")
    previous = {r[0]:r for r in rows[header:end-1] if val(r,0)}
    count = end-header-1
    result = []
    if len(active)>count:
        extra = len(active)-count
        result.append({"insertDimension":{"range":{"sheetId":sheet_id,
            "dimension":"ROWS","startIndex":end-1,"endIndex":end-1+extra},
            "inheritFromBefore":True}})
        end += extra
    first,last = header+1,end-1
    names = f"$A${first}:$A${last}"
    held = f"$B${first}:$B${last}"
    proposed = f"$C${first}:$C${last}"
    for i in range(last-first+1):
        n = first+i
        data = [""]*13
        if i<len(active):
            t = active[i][0]
            old = previous.get(t, [])
            data[:3] = [t,
                f'=IFNA(XLOOKUP(A{n},$A$4:$A${total-1},$C$4:$C${total-1}),0)',
                f'=IFNA(XLOOKUP(A{n},$A$4:$A${total-1},$D$4:$D${total-1}),0)']
            for c in range(3,9):
                v = val(old,c)
                # Repair only the exact corruption introduced by the prose writer.
                if c==5 and isinstance(v,str) and "\nFINANCIAL STRESS [" in v:
                    prefix=v.split("\nFINANCIAL STRESS [",1)[0]
                    v=int(prefix) if prefix in {"0","1"} else ""
                data[c] = v if numeric(v) and (v in (0,1) if c<6 else -1<=v<=1) else ""
            for c,w,shock in ((9,"B","G"),(10,"C","G"),(11,"B","I"),(12,"C","I")):
                data[c]=f'=IF({w}{n}=0,0,IF(ISNUMBER({shock}{n}),{w}{n}*{shock}{n},"UNKNOWN"))'
        for c,v in enumerate(data,1):
            result.append(cell(sheet_id,n,c,v))
    # Reuse the existing three scenario rows above the detail header.
    for label,flag,shock in (("AI spending","D","G"),("Financing stress","E","H"),("Rate / valuation stress","F","I")):
        summary=next(i+1 for i,r in enumerate(rows) if val(r,0)==label)
        for col,weights in ((2,held),(3,proposed)):
            source=f"${flag}${first}:${flag}${last}"
            result.append(cell(sheet_id,summary,col,
                f'=IF(SUMPRODUCT(({weights}>0)*(1-ISNUMBER({source})))>0,"UNKNOWN",SUMPRODUCT({weights},{source}))'))
        for col,weights in ((5,held),(6,proposed)):
            source=f"${shock}${first}:${shock}${last}"
            result.append(cell(sheet_id,summary,col,
                f'=IF(SUMPRODUCT(({weights}>0)*(1-ISNUMBER({source})))>0,"UNKNOWN",SUMPRODUCT({weights},{source}))'))
        for col,left,right in ((4,"B","C"),(7,"E","F")):
            result.append(cell(sheet_id,summary,col,
                f'=IF(AND(ISNUMBER({left}{summary}),ISNUMBER({right}{summary})),{right}{summary}-{left}{summary},"UNKNOWN")'))
    energy=next(i+1 for i,r in enumerate(rows) if str(val(r,0)).startswith("Oil/refining reversal"))
    tickers=",".join('"'+t+'"' for t,s in sorted(COMPANY_SECTOR.items()) if s=="XLE")
    for col,weights in ((2,held),(3,proposed)):
        result.append(cell(sheet_id,energy,col,f'=SUM(ARRAYFORMULA(SUMIF({names},{{{tickers}}},{weights})))'))
    # The inherited oil shocks cover PSX, FANG, TLW only. Never silently omit XOM/EOG.
    for col,weights,exposure in ((5,held,"B"),(6,proposed,"C")):
        known=f'SUM(ARRAYFORMULA(SUMIF({names},{{"PSX","FANG","TLW:LSE"}},{weights})))'
        shock=f'SUMIF({names},"PSX",{weights})*(-0.10)+SUMIF({names},"FANG",{weights})*(-0.12)+SUMIF({names},"TLW:LSE",{weights})*(-0.25)'
        result.append(cell(sheet_id,energy,col,f'=IF(ABS({exposure}{energy}-{known})>0.0000001,"UNKNOWN",{shock})'))
    result.append(cell(sheet_id,energy,4,f'=C{energy}-B{energy}'))
    result.append(cell(sheet_id,energy,7,f'=IF(AND(ISNUMBER(E{energy}),ISNUMBER(F{energy})),F{energy}-E{energy},"UNKNOWN")'))
    coverage=header-4
    result.append(cell(sheet_id,coverage,1,
        f'=IF(ABS(SUM({held})-SUM(C4:C{total-1}))+ABS(SUM({proposed})-SUM(D4:D{total-1}))<0.0000001,"Exposure mapping: "&COUNTIF({held},">0")&" holdings / "&TEXT(SUM({held}),"0%")&" held; scenario inputs may be UNKNOWN","ERROR: exposure mapping incomplete")'))
    result.append(cell(sheet_id,header-9,1,
        f'="Current energy exposure: "&TEXT(B{energy},"0%")&"; proposed "&TEXT(C{energy},"0%")&". Scenario effects are hypothetical; UNKNOWN inputs cannot be used for sizing."'))
    result.append(cell(sheet_id,end,1,"Assumptions: inherited 13-Oct scenario inputs are retained by ticker. Blank flags/shocks mean UNKNOWN, not zero. New holdings require an explicitly documented scenario basis before a portfolio stress estimate is available. Effects are hypothetical, not forecasts; no trades or fills inferred."))
    result.append({"setDataValidation":{"range":{"sheetId":sheet_id,"startRowIndex":first-1,
        "endRowIndex":last,"startColumnIndex":3,"endColumnIndex":6},
        "rule":{"condition":{"type":"CUSTOM_FORMULA","values":[{"userEnteredValue":
            f'=OR(ISBLANK(D{first}),AND(ISNUMBER(D{first}),OR(D{first}=0,D{first}=1)))'}]},"strict":True}}})
    return result


def health(rows):
    faults,gaps=[],[]
    try:
        total,header,end=layout(rows)
    except ValueError as exc:
        return [str(exc)],[]
    matrix={r[0]:r for r in rows[3:total-1] if val(r,0)}
    data=[(n,r) for n,r in enumerate(rows[header:end-1],header+1) if val(r,0)]
    for ticker,m in matrix.items():
        if not any(numeric(val(m,c)) and m[c]>0 for c in (2,3)):
            continue
        matches=[(n,r) for n,r in data if r[0]==ticker]
        if len(matches)!=1:
            faults.append(f"TRADE ANALYSIS!A{header+1}:M{end-1}: {ticker} missing/duplicate scenario mapping")
            continue
        n,r=matches[0]
        for c,expected in ((1,val(m,2)),(2,val(m,3))):
            if not numeric(val(r,c)) or abs(r[c]-expected)>1e-8:
                faults.append(f"TRADE ANALYSIS!{'BC'[c-1]}{n}: {ticker} weight mismatch")
        for c in range(3,9):
            v=val(r,c)
            if v in ("",None):
                gaps.append(f"{'DEFGHI'[c-3]}{n}")
            elif not numeric(v) or (v not in (0,1) if c<6 else not -1<=v<=1):
                faults.append(f"TRADE ANALYSIS!{'DEFGHI'[c-3]}{n}: invalid numeric scenario input")
    for n,r in data:
        if r[0] not in matrix and any(numeric(val(r,c)) and r[c]>0 for c in (1,2)):
            faults.append(f"TRADE ANALYSIS!A{n}:C{n}: orphan scenario weight")
    for n,r in enumerate(rows,1):
        if str(val(r,0)).startswith("Oil/refining reversal"):
            for c,mc in ((1,2),(2,3)):
                expected=sum(m[mc] for t,m in matrix.items() if COMPANY_SECTOR.get(t)=="XLE" and numeric(val(m,mc)))
                if not numeric(val(r,c)) or abs(r[c]-expected)>1e-8:
                    faults.append(f"TRADE ANALYSIS!{'BC'[c-1]}{n}: energy exposure mismatch; expected {expected:.0%}")
    return faults,gaps

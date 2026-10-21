"""Two-column Trade Desk formulas; human fallback needs a frozen price receipt.

The independent daily review may use verified prior closes when Actions is
delayed. A STARTED review, stale prices, or a snapshot of different holdings
cannot authorize the fallback. The engine fault remains visible in B14.
"""


def decision_formula(n):
    return f'''=IF({n}>COUNTIF(Portfolio!$C$8:INDEX(Portfolio!$C:$C,MATCH("TOTAL",Portfolio!$A:$A,0)-1),">0"),"",IFERROR(LET(
t,INDEX(FILTER(Portfolio!$A$8:INDEX(Portfolio!$A:$A,MATCH("TOTAL",Portfolio!$A:$A,0)-1),Portfolio!$C$8:INDEX(Portfolio!$C:$C,MATCH("TOTAL",Portfolio!$A:$A,0)-1)>0),{n}),
w,XLOOKUP(t,Portfolio!$A$8:INDEX(Portfolio!$A:$A,MATCH("TOTAL",Portfolio!$A:$A,0)-1),Portfolio!$C$8:INDEX(Portfolio!$C:$C,MATCH("TOTAL",Portfolio!$A:$A,0)-1)),
q,XLOOKUP(t,Portfolio!$A$8:INDEX(Portfolio!$A:$A,MATCH("TOTAL",Portfolio!$A:$A,0)-1),Portfolio!$D$8:INDEX(Portfolio!$D:$D,MATCH("TOTAL",Portfolio!$A:$A,0)-1)),
ord,IFERROR(XLOOKUP(t,'Trade Log'!$C$8:$C$1000,'Trade Log'!$F$8:$F$1000,"",0,-1),""),
stamp,XLOOKUP(t,'PRICE MONITOR'!$A$2051:$A$2070,'PRICE MONITOR'!$G$2051:$G$2070,0),
data,XLOOKUP(t,'PRICE MONITOR'!$A$2051:$A$2070,'PRICE MONITOR'!$K$2051:$K$2070,"FAILED"),
key,"Daily:"&TEXT(TODAY(),"yyyy-mm-dd"),
review,IFERROR(XLOOKUP(key,Reviews!$J$8:$J$1001,Reviews!$A$8:$A$1001,0,0,-1),0),
receipt,IFERROR(XLOOKUP(key,Reviews!$J$8:$J$1001,Reviews!$H$8:$H$1001,"",0,-1),""),
target,XLOOKUP(t,'TRADE ANALYSIS'!$A$4:$A$100,'TRADE ANALYSIS'!$D$4:$D$100,w),
fresh,AND(INT(stamp)=TODAY(),stamp<=NOW(),data="VERIFIED PRIOR CLOSE",ABS(XLOOKUP(t,'PRICE MONITOR'!$A$2051:$A$2070,'PRICE MONITOR'!$B$2051:$B$2070,-1)-w)<0.00000001,ABS(XLOOKUP(t,'PRICE MONITOR'!$A$2051:$A$2070,'PRICE MONITOR'!$C$2051:$C$2070,-1)-q)<0.00000001,OR('PRICE MONITOR'!$V$195="COMPLETE",'PRICE MONITOR'!$V$195="PARTIAL")),
snapshot,COUNTIFS('Decision History'!$B$5:$B$1004,key,'Decision History'!$C$5:$C$1004,t,'Decision History'!$D$5:$D$1004,w,'Decision History'!$E$5:$E$1004,q,'Decision History'!$L$5:$L$1004,">0",'Decision History'!$S$5:$S$1004,"*VERIFIED PRIOR CLOSE "&TEXT(WORKDAY(TODAY(),-1,{{DATE(2026,11,26)}}),"yyyy-mm-dd")&"*")=1,
human,AND(INT(review)=TODAY(),review<=NOW(),snapshot,REGEXMATCH(receipt,"VERIFIED PRIOR CLOSE")),
fallback,AND(human,REGEXMATCH(receipt,"VERIFIED PRIOR CLOSE FALLBACK")),
pending,OR(q<>w,REGEXMATCH(LOWER(ord),"submitted|pending|queued|scheduled")),
auto,XLOOKUP(t,'PRICE MONITOR'!$A$2051:$A$2070,'PRICE MONITOR'!$Q$2051:$Q$2070,t&" — UNRESOLVED"),
manual,t&" — "&IF(target<w,IF(target=0,"EXIT","TRIM"),"HOLD")&"; target "&TEXT(target,"0%")&IF(target<w,"; sell to CASH","")&" | "&XLOOKUP(t,'TRADE ANALYSIS'!$A$4:$A$100,'TRADE ANALYSIS'!$F$4:$F$100,"See review"),
IF(pending,t&" — HOLD "&TEXT(w,"0%")&"; already queued → "&TEXT(q,"0%")&"; DO NOT RESUBMIT"&IF(OR(fresh,fallback),"","; UNRESOLVED DATA"),
IF(AND(human,OR(AND(fresh,review>=stamp),AND(NOT(fresh),fallback))),manual&IF(fresh,""," | VERIFIED PRIOR-CLOSE FALLBACK; engine unresolved"),
IF(fresh,auto,t&" — UNRESOLVED: missing or stale verified proposal")))&IF('PRICE MONITOR'!$V$195="PARTIAL"," ⚠ COVERAGE FAILURE","")),"UNRESOLVED — decision formula/data failure"))'''.replace("\n", "")


def date_formula(n):
    row = n + 1
    return f'=IF(B{row}="","",LET(saved,IFERROR(INDEX(\'PRICE MONITOR\'!$G$2051:$G$2070,{n}),0),review,IFERROR(XLOOKUP("Daily:"&TEXT(TODAY(),"yyyy-mm-dd"),Reviews!$J$8:$J$1001,Reviews!$A$8:$A$1001,0,0,-1),0),IF(MAX(saved,review)>0,MAX(saved,review),"")))'


def install_request(sheet_id):
    return {"updateCells": {"range": {"sheetId": sheet_id, "startRowIndex": 1,
        "endRowIndex": 11, "startColumnIndex": 0, "endColumnIndex": 2},
        "rows": [{"values": [{"userEnteredValue": {"formulaValue": date_formula(n)}},
                              {"userEnteredValue": {"formulaValue": decision_formula(n)}}]}
                 for n in range(1, 11)], "fields": "userEnteredValue"}}


def cell_request(sheet_id, row, column, value):
    kind = "formulaValue" if isinstance(value, str) and value.startswith("=") else (
        "numberValue" if isinstance(value, (int, float)) else "stringValue")
    return {"updateCells": {"start": {"sheetId": sheet_id, "rowIndex": row-1,
        "columnIndex": column-1}, "rows": [{"values": [
            {"userEnteredValue": {kind: value}}]}], "fields": "userEnteredValue"}}


def view_requests(analysis_id, gates_id, analysis_rows, portfolio):
    """Keep presentation aligned with actual holdings, without deciding trades.

    Preserve every existing proposed target and human reason. A newly recorded
    holding receives its existing queued weight, never a new order. The daily
    decision owner still must review it and freeze its prospective assessment.
    """
    end = next((i for i, r in enumerate(analysis_rows)
                if r and r[0] == "TOTAL SELL"), None)
    if end is None:
        raise ValueError("TRADE ANALYSIS!A4:M100: TOTAL SELL boundary missing")
    rows = [list(r) for r in analysis_rows[:end]]
    names = [r[0] for r in rows if r and r[0]]
    if len(names) != len(set(names)):
        raise ValueError("TRADE ANALYSIS!A4:M100: duplicate ticker")
    active = [r for r in portfolio if len(r)>3 and any(
        isinstance(w, (int, float)) and w>0 for w in r[2:4])]
    missing = [r for r in active if r[0] not in names]
    if len(rows) + len(missing) > 90 or len(active)>19:
        raise ValueError("TRADE ANALYSIS / PRICE GATES: holding display capacity exceeded")
    requests = []
    if missing:
        insert = 3 + end
        requests.append({"insertDimension": {"range": {"sheetId": analysis_id,
            "dimension": "ROWS", "startIndex": insert,
            "endIndex": insert+len(missing)}, "inheritFromBefore": True}})
        requests.append({"copyPaste": {"source": {"sheetId": analysis_id,
            "startRowIndex": 3, "endRowIndex": 4, "startColumnIndex": 0,
            "endColumnIndex": 13}, "destination": {"sheetId": analysis_id,
            "startRowIndex": insert, "endRowIndex": insert+len(missing),
            "startColumnIndex": 0, "endColumnIndex": 13}, "pasteType": "PASTE_FORMAT"}})
        for r in missing:
            n = 4+len(rows)
            for c, value in [(1,r[0]),(2,r[1]),(4,r[3]),
                (6,"Existing holding and queued weight synchronized; no new order. "
                    "See dated daily review; market returns before purchase are not game profits.")]:
                requests.append(cell_request(analysis_id,n,c,value))
            requests.append(cell_request(analysis_id,n,5,
                f'=IF(C{n}=0,"NOT HELD",IF(D{n}<C{n},IF(D{n}=0,"ROTATE TO CASH","TRIM TO CASH"),IF(D{n}>C{n},"BUY REVIEW","HOLD")))'))
            requests.append(cell_request(analysis_id,n,7,
                f'=IFERROR("Last record: "&XLOOKUP(A{n},\'Trade Log\'!$C$8:$C$1000,\'Trade Log\'!$F$8:$F$1000,"",0,-1),"No recorded order")'))
            requests.append(cell_request(analysis_id,n,8,
                f'=IF(C{n}=0,"Not held",IFNA(XLOOKUP(A{n},Catalysts!$A$10:$A$1000,Catalysts!$C$10:$C$1000),"Confirm event"))'))
            rows.append([r[0],r[1],r[2],r[3]])
    tickers = 'Portfolio!$A$8:INDEX(Portfolio!$A:$A,MATCH("TOTAL",Portfolio!$A:$A,0)-1)'
    weights = 'Portfolio!$C$8:INDEX(Portfolio!$C:$C,MATCH("TOTAL",Portfolio!$A:$A,0)-1)'
    for n, r in enumerate(rows,4):
        if r and r[0]:
            # RAW connector writes can leave a displayed "10%" as text. SUM
            # silently skips it, so preserve the amount as a native number.
            if len(r)>3 and isinstance(r[3],str):
                import re
                if not re.fullmatch(r"\d+(?:\.\d+)?%",r[3].strip()):
                    raise ValueError(f"TRADE ANALYSIS!D{n}: target is not a numeric percentage")
                numeric = float(r[3].strip()[:-1])/100
                if not 0<=numeric<=.25:
                    raise ValueError(f"TRADE ANALYSIS!D{n}: target exceeds position limits")
                requests.append(cell_request(analysis_id,n,4,numeric))
            requests.append(cell_request(analysis_id,n,3,
                f'=IFNA(XLOOKUP(A{n},{tickers},{weights}),0)'))
    last = 3+len(rows)
    total = last+1
    for column, formula in [
        (2,f'=SUMPRODUCT((C4:C{last}>D4:D{last})*(C4:C{last}-D4:D{last}))'),
        (5,f'=SUMPRODUCT((D4:D{last}>C4:C{last})*(D4:D{last}-C4:C{last}))'),
        (8,f'=SUM(D4:D{last})'),(11,f'=1-H{total}')]:
        requests.append(cell_request(analysis_id,total,column,formula))
    for n in range(75,94):
        requests.append(cell_request(gates_id,n,1,
            f'=IFERROR(INDEX(UNIQUE({{FILTER({tickers},{weights}>0);FILTER($A$5:$A$23,$A$5:$A$23<>"")}}),{n-74}),"")'))
        requests.append(cell_request(gates_id,n,10,
            f'=IF(A{n}="","",IFERROR(IF(XLOOKUP(A{n},\'PRICE MONITOR\'!$A$2051:$A$2070,\'PRICE MONITOR\'!$K$2051:$K$2070,"")="VERIFIED PRIOR CLOSE","OK",XLOOKUP(A{n},\'PRICE MONITOR\'!$B$5:$B$28,\'PRICE MONITOR\'!$Q$5:$Q$28,"NO DATA")),"NO DATA"))'))
        requests.append(cell_request(gates_id,n,11,
            f'=IF(A{n}="","",IFERROR(IF(XLOOKUP(A{n},{tickers},Portfolio!$D$8:INDEX(Portfolio!$D:$D,MATCH("TOTAL",Portfolio!$A:$A,0)-1),0)<>XLOOKUP(A{n},{tickers},{weights},0),"ALREADY QUEUED - DO NOT RESUBMIT","REVIEW ONLY - NO ORDER"),"REVIEW ONLY - NO ORDER"))'))
    return requests


def sync_views(book, portfolio):
    analysis, gates = book.worksheet("TRADE ANALYSIS"), book.worksheet("PRICE GATES")
    rows = analysis.get("A4:M100", value_render_option="UNFORMATTED_VALUE")
    book.batch_update({"requests": view_requests(analysis.id,gates.id,rows,portfolio)})


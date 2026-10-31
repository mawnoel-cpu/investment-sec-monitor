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
            requests.append(cell_request(analysis_id,n,5,
                f'=LET(w,C{n},tar,D{n},q,IFNA(XLOOKUP(A{n},{tickers},Portfolio!$D$8:INDEX(Portfolio!$D:$D,MATCH("TOTAL",Portfolio!$A:$A,0)-1)),w),IF(q<>w,"ALREADY QUEUED - DO NOT RESUBMIT",IF(w=0,IF(tar>0,"BUY","NOT HELD"),IF(tar<w,IF(tar=0,"ROTATE TO CASH","TRIM TO CASH"),IF(tar>w,"BUY","HOLD")))))'))
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
    import ft_stress_panel
    # Re-read after any decision-row insertions; scenario coordinates may move.
    book.batch_update({"requests": ft_stress_panel.requests(analysis.id,
        analysis.get(f"A1:M{analysis.row_count}", value_render_option="UNFORMATTED_VALUE"))})
    book.batch_update({"requests": status_requests(book.worksheet("TODAY").id,
                                                  book.worksheet("BACKEND HEALTH").id)})
    # Live formulas, not a second decision writer. Reinstall safely on reruns.
    sync_readiness(book)


def sync_readiness(book):
    """Read back installed formulas; a submitted batch alone is not success."""
    sheet = book.worksheet("TRADE DESK")
    requests = readiness_requests(sheet.id)
    book.batch_update({"requests": requests})
    expected = []
    for offset in range(0, len(requests), 2):
        expected.append([next(iter(requests[offset+c]["updateCells"]["rows"][0]
                                   ["values"][0]["userEnteredValue"].values()))
                         for c in range(2)])
    actual = sheet.get("A25:B60", value_render_option="FORMULA")
    if actual != expected:
        raise RuntimeError("TRADE DESK!A25:B60 readiness formula readback mismatch")


def readiness_gate(pending, complete, fresh, qualified, action):
    """Pending wins. Unknown inputs can never default to HOLD."""
    return (f'IF({pending},"ALREADY QUEUED — DO NOT RESUBMIT",'
            f'IF(NOT({complete}),"UNRESOLVED — review incomplete",'
            f'IF(NOT({fresh}),"BLOCKED — stale or unassessed prices",'
            f'IF(NOT({qualified}),"UNRESOLVED — action/source; see dated addendum",'
            f'IF({action}="HOLD","HOLD—review completed","READY — "&{action})))))')


def readiness_requests(sheet_id):
    """Live view only: own A25:B60; preserve decisions A1:B23 and all history."""
    day = 'WORKDAY(TODAY()+1,-1,{DATE(2026,11,26)})'
    last = 'MATCH("TOTAL",Portfolio!$A:$A,0)-1'
    names = f'Portfolio!$A$8:INDEX(Portfolio!$A:$A,{last})'
    weights = f'Portfolio!$C$8:INDEX(Portfolio!$C:$C,{last})'
    queued = f'Portfolio!$D$8:INDEX(Portfolio!$D:$D,{last})'
    session = 'WORKDAY(TODAY()+IF(AND(WEEKDAY(TODAY(),2)<6,MOD(NOW(),1)>=TIME(16,0,0)),1,0),-1,{DATE(2026,11,26)})'
    key = f'"Daily:"&TEXT({day},"yyyy-mm-dd")'
    def rv(col):
        return 'IFERROR(XLOOKUP('+key+',Reviews!$J$8:$J$1001,Reviews!$'+col+'$8:$'+col+'$1001,0),0)'
    common = (f'key,{key},started,{rv("A")},done,{rv("N")},'
              f'complete,AND(COUNTIF(Reviews!$J$8:$J$1001,key)=1,ISNUMBER(done),done>=started,done<=NOW(),INT(done)={day},'
              + ','.join(f'{rv(c)}="PASS"' for c in ("P","R","T","V","X")) + ','
              + ','.join(f'AND(ISNUMBER({rv(c)}),{rv(c)}>=started,{rv(c)}<=done)' for c in ("O","Q","S","U","W")) + '),')
    values = {
        25: ("DECISION READINESS", "LIVE CHECKS — supporting view; dated decisions above retain their original times"),
        26: ("Overall", '=IF(TODAY()>DATE(2026,11,27),"GAME ENDED",IF(COUNTIF(A35:A53,"?*")=0,"BLOCKED — holdings missing",IF(COUNTIF(B35:B53,"*UNRESOLVED*")+COUNTIF(B35:B53,"*BLOCKED*")>0,"PARTIAL — see individual blockers",IF(WEEKDAY(TODAY(),2)>5,"PARTIAL — next-session review due","READY — saved checks; no automatic order"))))'),
        27: ("Latest saved review", f'=LET({common}IF(complete,"SAVED "&TEXT(done,"dd mmm HH:mm")&" Toronto; "&TODAY!B27,"UNRESOLVED — "&TODAY!B27))'),
        28: ("Exposure / orders", f'=LET(w,SUM({weights}),q,SUM({queued}),"Confirmed "&TEXT(w,"0%")&" invested / "&TEXT(1-w,"0%")&" cash; queued targets "&TEXT(q,"0%")&"; "&SUMPRODUCT(--(ABS({weights}-{queued})>0.00000001))&" weight changes. Proposed targets: TRADE ANALYSIS; never fills.")'),
        29: ("Market-price time", f'=TEXT({session},"dd mmm yyyy")&" 16:00 New York close reference; observed tick time unavailable. Held names: US/USD; other venues/currencies need separate checks."'),
        30: ("Collection / calculation", '="Archive collected "&TEXT(MAX(\'DAILY CLOSES\'!$H$5:$H$12000),"dd mmm HH:mm:ss")&"; inputs calculated "&TEXT(MAX(\'Decision Cards\'!$B$66:$B$104),"dd mmm HH:mm:ss")&" Toronto; per-name freshness below."'),
        31: ("Alternatives", '="GEV/JPM/NVDA: "&COUNTIF(\'ROTATION BENCH\'!L304:L306,"*NO BUY*")&"/3 RESERVE; checks A303:P309. Candidate gaps do not block completed holding checks."'),
        32: ("Deadline / recovery", f'=LET(n,{rv("N")},m,{rv("M")},IF(OR(REGEXMATCH(TO_TEXT(m),"on_time_scheduled_proof[^a-z]+false"),AND(ISNUMBER(n),n>{day}+TIME(14,45,0))),"MISSED 14:45 — late recovery is not scheduled proof","See Reviews O:Z; no backdated completion"))'),
        33: ("Rules", "Detect → Verify → Compare → Qualify → Publish → Reconcile. Flags are research only; thresholds provisional. Confirm execution before updating holdings."),
        34: ("HELD NAME", "READINESS · confirmed → queued · frozen action"),
        54: ("PROCESS MEASURES", "Actual results and hypothetical estimates remain separate"),
        55: ("Current-session checks", '=(COUNTIF(B35:B53,"*HOLD—review completed*")+COUNTIF(B35:B53,"*READY —*"))&"/"&COUNTIF(A35:A53,"?*")&" holding checks qualified; candidate purchase qualification separate."'),
        56: ("Signal → recommendation delay", "UNKNOWN — no consistent linked first-signal/first-recommendation IDs. Reviews Z measures runtime, not detection delay."),
        57: ("Account return", '="Last displayed return "&TEXT(PERFORMANCE!B5,"0.00%")&"; "&PERFORMANCE!H5&". Current return/matched benchmark UNKNOWN pending FT snapshot."'),
        58: ("Drawdown", "UNKNOWN — continuous matched FT equity observations unavailable; never infer from stock momentum."),
        59: ("Rotation contribution", "Replacement return minus funding holding return from feasible execution date. Actual fills UNKNOWN. Existing proxy batches: PERFORMANCE A105:J115; not realised profit."),
        60: ("Scope", "READY certifies saved checks, not profit or an order. Refresh after a new session, material news or changed holdings. Historical failures stay recorded."),
    }
    for n in range(1,20):
        row = 34+n
        ticker = f'IFERROR(INDEX(FILTER({names},{weights}>0),{n}),"")'
        def dh(col, default='""'):
            return 'IFERROR(INDEX(FILTER(\'Decision History\'!$'+col+'$5:$'+col+'$1000,\'Decision History\'!$B$5:$B$1000=key,\'Decision History\'!$C$5:$C$1000=t),1),'+default+')'
        expression = readiness_gate('pending','complete','fresh','qualified','act')
        formula = (f'=IF(A{row}="","",IFERROR(LET({common}t,A{row},w,XLOOKUP(t,{names},{weights}),q,XLOOKUP(t,{names},{queued}),'
                   f'ord,IFERROR(XLOOKUP(t,\'Trade Log\'!$C$8:$C$1000,\'Trade Log\'!$F$8:$F$1000,"",0,-1),""),'
                   f'pending,OR(ABS(w-q)>0.00000001,REGEXMATCH(LOWER(ord),"submitted|pending|queued|scheduled")),'
                   f'act,{dh("K")},basis,{dh("S")},cnt,COUNTIFS(\'Decision History\'!$B$5:$B$1000,key,\'Decision History\'!$C$5:$C$1000,t),'
                   f'ps,IFERROR(XLOOKUP(t,\'Decision Cards\'!$A$66:$A$104,\'Decision Cards\'!$C$66:$C$104,0),0),'
                   f'cap,IFERROR(XLOOKUP(t,\'Decision Cards\'!$A$66:$A$104,\'Decision Cards\'!$B$66:$B$104,0),0),'
                   f'fresh,AND(RIGHT(t,4)<>":LSE",ps={session},cap>=ps,cap<=NOW(),REGEXMATCH(basis,"VERIFIED PRIOR CLOSE "&TEXT(ps,"yyyy-mm-dd")&"|VERIFIED.*session"&TEXT(ps,"0"))),'
                   f'qualified,AND(cnt=1,{dh("A","0")}>=started,{dh("A","0")}<=done,{dh("L","0")}>0,{dh("O","0")}>0,{dh("P")}<>"",REGEXMATCH(act,"^(HOLD|TRIM|EXIT|ROTATE)$"),'
                   f'ABS({dh("D","-1")}-w)<0.00000001,ABS({dh("E","-1")}-q)<0.00000001),'
                   f'{expression}&" · "&TEXT(w,"0%")&" → "&TEXT(q,"0%")&"; frozen "&IF(act="","missing",act)),'
                   '"UNRESOLVED — readiness formula/data error"))')
        values[row] = ('='+ticker,formula)
    values[26] = (values[26][0], '=IF(OR(COUNTIF(A35:A53,"?*")<>COUNTIF('+weights+',">0"),COUNTIF('+weights+',">0")<5,MAX('+weights+')>0.25000001,MAX('+queued+')>0.25000001,SUM('+weights+')<0.74999999,SUM('+queued+')<0.74999999,SUM('+weights+')>1.00000001,SUM('+queued+')>1.00000001),"BLOCKED — portfolio limits or display coverage",'+values[26][1][1:]+')')
    values[26] = (values[26][0], '=IFERROR(' + values[26][1][1:] + ',"BLOCKED — readiness calculation error")')
    return [cell_request(sheet_id,r,c,v) for r,pair in sorted(values.items()) for c,v in enumerate(pair,1)]


def status_requests(today_id, health_id):
    """Use the same baseline bench and explicit completion contract as Python."""
    day='WORKDAY(TODAY()+1,-1,{DATE(2026,11,26)})'
    review=f'''=IFERROR(LET(day,{day},k,"Daily:"&TEXT(day,"yyyy-mm-dd"),
qty,COUNTIF(Reviews!$J$8:$J$1001,k),at,XLOOKUP(k,Reviews!$J$8:$J$1001,Reviews!$A$8:$A$1001,0),
done,XLOOKUP(k,Reviews!$J$8:$J$1001,Reviews!$N$8:$N$1001,0),
d,XLOOKUP(k,Reviews!$J$8:$J$1001,Reviews!$F$8:$F$1001,""),
meta,XLOOKUP(k,Reviews!$J$8:$J$1001,Reviews!$M$8:$M$1001,""),
cutoff,XLOOKUP(k,Reviews!$J$8:$J$1001,Reviews!$L$8:$L$1001,0),
last,MATCH("TOTAL",Portfolio!$A:$A,0)-1,
held,FILTER(Portfolio!$A$8:INDEX(Portfolio!$A:$A,last),Portfolio!$C$8:INDEX(Portfolio!$C:$C,last)>0),
baseline,FILTER('ROTATION BENCH'!$C$5:$C$15,'ROTATION BENCH'!$C$5:$C$15<>""),
picked,IFERROR(TRANSPOSE(SPLIT(REGEXREPLACE(REGEXEXTRACT(meta,"""candidates""\\s*:\\s*\\[([^\\]]*)\\]"),"""|\\s",""),",")),""),
allnames,UNIQUE({{held;baseline;picked}}),ts,FILTER(allnames,allnames<>""),
good,SUM(ARRAYFORMULA(--(COUNTIFS('Decision History'!$B$5:$B$1000,k,'Decision History'!$C$5:$C$1000,ts,
'Decision History'!$A$5:$A$1000,">="&day,'Decision History'!$A$5:$A$1000,"<"&day+1,
'Decision History'!$K$5:$K$1000,"<>",'Decision History'!$L$5:$L$1000,">0",'Decision History'!$M$5:$M$1000,"<>",
'Decision History'!$O$5:$O$1000,">0",'Decision History'!$P$5:$P$1000,"<>",'Decision History'!$S$5:$S$1000,"<>")=1))),
IF(TODAY()>DATE(2026,11,27),"GAME ENDED",IF(qty<>1,IF(NOW()<day+TIME(14,45,0),"DUE 14:45","MISSING / DUPLICATE REVIEW"),
IF(OR(NOT(ISNUMBER(done)),done<at,INT(at)<>day,INT(done)<>day,done>NOW(),REGEXMATCH(d,"(?i)^(STARTED|PENDING|FAILED|INCOMPLETE|BLOCKED)")),"INCOMPLETE REVIEW",
IF(good<>ROWS(ts),"INCOMPLETE SNAPSHOTS "&good&"/"&ROWS(ts)&" · "&TEXT(day,"dd mmm"),
IF(MAX(Evidence!$P$8:$P)>cutoff,"NEW INPUTS — recheck","SAVED "&TEXT(done,"dd mmm HH:mm")&" · "&good&"/"&ROWS(ts)&" required · research partial")))))),"CHECK ERROR")'''.replace('\n','')
    intake=f'''=IFERROR(LET(day,{day},at,MAX(Sources!$A$46:$A$1000),st,XLOOKUP(at,Sources!$A$46:$A$1000,Sources!$B$46:$B$1000,""),
done,XLOOKUP(at,Sources!$A$46:$A$1000,Sources!$I$46:$I$1000,0),
scheduled,IFERROR(ROWS(FILTER(Sources!$A$46:$A$1000,ISNUMBER(Sources!$A$46:$A$1000),Sources!$A$46:$A$1000>=day+TIME(10,30,0),Sources!$A$46:$A$1000<day+1,
ISNUMBER(Sources!$I$46:$I$1000),Sources!$I$46:$I$1000>=Sources!$A$46:$A$1000,Sources!$I$46:$I$1000<=NOW(),
REGEXMATCH(Sources!$B$46:$B$1000,"^(Complete|Partial)$"),
NOT(REGEXMATCH(LOWER(Sources!$H$46:$H$1000),"audit:|manual|supplemental|additional|recovery")),
IF(day>=DATE(2026,10,26),REGEXMATCH(LOWER(Sources!$H$46:$H$1000),"scheduledintake:"&TEXT(day,"yyyy-mm-dd")),Sources!$A$46:$A$1000>0))),0),
IF(TODAY()>DATE(2026,11,27),"GAME ENDED",IF(scheduled=0,IF(NOW()<day+TIME(13,40,0),"IN PROGRESS / DUE13:40","MISSING SCHEDULED INTAKE · latest saved "&TEXT(done,"dd mmm HH:mm")),
IF(OR(NOT(ISNUMBER(done)),done<at,done>NOW()),"UNFINALIZED", "SAVED "&UPPER(st)&" · "&TEXT(done,"dd mmm HH:mm"))))),"CHECK ERROR")'''.replace('\n','')
    return [cell_request(today_id,27,2,review),cell_request(today_id,28,2,intake),
        cell_request(today_id,25,2,'=SUMPRODUCT(--(\'TRADE ANALYSIS\'!C4:INDEX(\'TRADE ANALYSIS\'!C:C,MATCH("TOTAL SELL",\'TRADE ANALYSIS\'!A:A,0)-1)<>\'TRADE ANALYSIS\'!D4:INDEX(\'TRADE ANALYSIS\'!D:D,MATCH("TOTAL SELL",\'TRADE ANALYSIS\'!A:A,0)-1)))'),
        cell_request(health_id,32,2,f'=IFERROR(LET(z,MAX(\'DAILY CLOSES\'!$L$5:$L),s,XLOOKUP(z,\'DAILY CLOSES\'!$L$5:$L,\'DAILY CLOSES\'!$N$5:$N),IF(AND(z>={day},z<=NOW(),s="Complete"),"OK","ISSUE")),"ISSUE")'),
        cell_request(health_id,34,2,f'=IF(REGEXMATCH(TODAY!B28,"^SAVED PARTIAL"),"LIMITED",IF(REGEXMATCH(TODAY!B28,"^SAVED COMPLETE"),"OK",IF(NOW()<{day}+TIME(13,40,0),"PENDING","ISSUE")))'),
        cell_request(health_id,35,2,f'=IF(REGEXMATCH(TODAY!B27,"^SAVED"),"OK",IF(NOW()<{day}+TIME(14,45,0),"PENDING","ISSUE"))'),
        cell_request(health_id,36,2,'=IF(OR(B32="ISSUE",B34="ISSUE",B35="ISSUE",B41="FAILED",REGEXMATCH(B33,"FAILED|NOT AUDITED"),REGEXMATCH(B37,"MISSED")),"ISSUE",IF(B41="PARTIAL","LIMITED","OK"))')]



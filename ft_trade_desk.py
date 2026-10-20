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
target,XLOOKUP(t,'TRADE ANALYSIS'!$A$4:$A$20,'TRADE ANALYSIS'!$D$4:$D$20,w),
fresh,AND(INT(stamp)=TODAY(),stamp<=NOW(),data="VERIFIED PRIOR CLOSE",ABS(XLOOKUP(t,'PRICE MONITOR'!$A$2051:$A$2070,'PRICE MONITOR'!$B$2051:$B$2070,-1)-w)<0.00000001,ABS(XLOOKUP(t,'PRICE MONITOR'!$A$2051:$A$2070,'PRICE MONITOR'!$C$2051:$C$2070,-1)-q)<0.00000001,OR('PRICE MONITOR'!$V$195="COMPLETE",'PRICE MONITOR'!$V$195="PARTIAL")),
snapshot,COUNTIFS('Decision History'!$B$5:$B$1004,key,'Decision History'!$C$5:$C$1004,t,'Decision History'!$D$5:$D$1004,w,'Decision History'!$E$5:$E$1004,q,'Decision History'!$L$5:$L$1004,">0",'Decision History'!$S$5:$S$1004,"*VERIFIED PRIOR CLOSE "&TEXT(WORKDAY(TODAY(),-1,{{DATE(2026,11,26)}}),"yyyy-mm-dd")&"*")=1,
human,AND(INT(review)=TODAY(),review<=NOW(),snapshot,REGEXMATCH(receipt,"VERIFIED PRIOR CLOSE")),
fallback,AND(human,REGEXMATCH(receipt,"VERIFIED PRIOR CLOSE FALLBACK")),
pending,OR(q<>w,REGEXMATCH(LOWER(ord),"submitted|pending|queued|scheduled")),
auto,XLOOKUP(t,'PRICE MONITOR'!$A$2051:$A$2070,'PRICE MONITOR'!$Q$2051:$Q$2070,t&" — UNRESOLVED"),
manual,t&" — "&IF(target<w,IF(target=0,"EXIT","TRIM"),"HOLD")&"; target "&TEXT(target,"0%")&IF(target<w,"; sell to CASH","")&" | "&XLOOKUP(t,'TRADE ANALYSIS'!$A$4:$A$20,'TRADE ANALYSIS'!$F$4:$F$20,"See review"),
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

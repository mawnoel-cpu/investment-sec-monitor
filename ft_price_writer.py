"""FT-only daily EOD stock-price archive, fallback feed and run diagnostics.

Only reads market data and writes DAILY CLOSES A:J / L:P and TRADE DESK cell
notes. No decisions, executions, holdings, queued weights or Evidence edits.
"""
from __future__ import annotations

import csv
import io
import json
import math
import os
import sys
import time
from datetime import date, datetime, time as clock, timedelta
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

BOOK_ID = "1ItUi3uYPK4AvuHQ6PHIZTRvQnVhMiLONefTpVLEzGGE"
TORONTO = ZoneInfo("America/Toronto")
EPOCH = datetime(1899, 12, 30)
SOURCE_SHEET = "DAILY CLOSES"
DATA_COLUMNS = ["DATE", "TICKER", "GOOGLE SYMBOL", "CLOSE", "UNIT", "VENUE",
                "SOURCE URL", "CAPTURED AT", "QA STATUS", "SESSION KEY"]
LOG_COLUMNS = ["RUN AT", "RUN MODE", "STATUS", "TICKERS", "FAILURES / COVERAGE"]
HTTP = requests.Session()
HTTP.headers.update({"User-Agent": "Mozilla/5.0 (compatible; FTGamePriceArchive/1.0)"})
MAX_HISTORICAL_DAYS = 95
DATA_LAST_ROW = 12000
# Exchange closures within the collection/game period. Never treat a holiday as
# a missing close, or a pre-close daily candle as a completed session.
US_HOLIDAYS = {date(2026, 9, 7), date(2026, 11, 26)}
LSE_HOLIDAYS = {date(2026, 8, 31)}


def serial(value):
    if isinstance(value, date) and not isinstance(value, datetime):
        value = datetime.combine(value, clock())
    if value.tzinfo:
        value = value.astimezone(TORONTO).replace(tzinfo=None)
    return (value-EPOCH).total_seconds()/86400


def day_of(value):
    if isinstance(value, (float,int)):
        return (EPOCH+timedelta(days=float(value))).date()
    s = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d %b %Y", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(s,fmt).date()
        except ValueError:
            pass
    raise ValueError("Invalid date: "+s[:30])


def venue_info(ticker, gs):
    if ticker.endswith(":LSE") or gs.startswith("LON:"):
        return "LSE", "GBX", ZoneInfo("Europe/London"), clock(16,30)
    return "US", "USD", ZoneInfo("America/New_York"), clock(16,0)


def last_completed(now, zone, close_time):
    local = now.astimezone(zone)
    day = local.date()
    london = str(zone) == "Europe/London"
    holidays = LSE_HOLIDAYS if london else US_HOLIDAYS
    if not london and day == date(2026, 11, 27):
        close_time = clock(13)
    # Require settlement buffer; today's Yahoo daily candle may still be intraday.
    if local.time() < (datetime.combine(day,close_time)+timedelta(minutes=35)).time():
        day -= timedelta(days=1)
    while day.weekday()>=5 or day in holidays:
        day-=timedelta(days=1)
    return day


def yahoo_symbol(ticker):
    return ticker.replace(":LSE",".L")


def yahoo_history(ticker, now):
    symbol=yahoo_symbol(ticker)
    errors=[]
    for host in ("query1.finance.yahoo.com","query2.finance.yahoo.com"):
        url=f"https://{host}/v8/finance/chart/{symbol}"
        try:
            response=HTTP.get(url,params={"range":"3mo","interval":"1d"},timeout=12)
            response.raise_for_status()
            root=response.json()["chart"]
            if root.get("error"):
                raise ValueError(str(root["error"])[:100])
            item=root["result"][0]
            meta=item.get("meta",{})
            currency=str(meta.get("currency",""))
            _, unit, zone, _ = venue_info(ticker,"")
            if unit=="GBX":
                if currency not in {"GBP", "GBp", "GBX", "GBx", "GBpence", "GBpenny"}:
                    raise ValueError("unexpected London currency "+currency)
                factor=100 if currency=="GBP" else 1
            else:
                if currency!="USD":
                    raise ValueError("unexpected US currency "+currency)
                factor=1
            values=item["indicators"]["quote"][0]["close"]
            rows=[]
            for ts,price in zip(item.get("timestamp",[]),values):
                if price is None or not math.isfinite(float(price)) or float(price)<=0:
                    continue
                dt=datetime.fromtimestamp(int(ts),zone).date()
                if dt<now.date()-timedelta(days=MAX_HISTORICAL_DAYS):
                    continue
                rows.append((dt,round(float(price)*factor,5),response.url))
            if not rows:
                raise ValueError("empty valid close history")
            return rows, ""
        except (requests.RequestException,ValueError,KeyError,IndexError,TypeError) as ex:
            errors.append(host+": "+str(ex)[:90])
    return [], "; ".join(errors)


def stooq_history(ticker,now):
    if ticker.endswith(":LSE"):
        return [],"Stooq London ticker mapping not trusted"
    symbol=ticker.lower()+".us"
    start=(now.date()-timedelta(days=MAX_HISTORICAL_DAYS)).strftime("%Y%m%d")
    end=now.date().strftime("%Y%m%d")
    url=f"https://stooq.com/q/d/l/?s={symbol}&d1={start}&d2={end}&i=d"
    try:
        resp=HTTP.get(url,timeout=12)
        resp.raise_for_status()
        parsed=list(csv.DictReader(io.StringIO(resp.text)))
        rows=[]
        for record in parsed:
            dt=day_of(record["Date"])
            close=float(record["Close"])
            if math.isfinite(close) and close>0:
                rows.append((dt,close,url))
        if not rows:
            raise ValueError("no valid close rows")
        return rows,""
    except (requests.RequestException,ValueError,KeyError,TypeError) as ex:
        return [],"Stooq: "+str(ex)[:100]


def fetch_ticker(ticker,gs,now):
    venue,unit,zone,close_time=venue_info(ticker,gs)
    end=last_completed(now,zone,close_time)
    bars,err=yahoo_history(ticker,now)
    if not bars:
        bars,err2=stooq_history(ticker,now)
        err=(err+" | "+err2).strip(" |")
    valid={dt:(float(p),url) for dt,p,url in bars if dt<=end and dt.weekday()<5}
    # A holiday produces no close; last known session remains valid, not a zero return.
    return ticker,gs,venue,unit,valid,end,err


def auth_book():
    import gspread
    from google.oauth2.service_account import Credentials
    secret=os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON","")
    if not secret:
        raise RuntimeError("Missing GOOGLE_SERVICE_ACCOUNT_JSON")
    info=json.loads(secret)
    scopes=["https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive.file"]
    return gspread.authorize(Credentials.from_service_account_info(info,scopes=scopes)).open_by_key(BOOK_ID)


def tracking(book):
    gates=book.worksheet("PRICE GATES").get("A5:D23",value_render_option="FORMATTED_VALUE")
    tracked={}
    for r in gates:
        if not r or not r[0]:
            continue
        ticker=r[0].strip()
        gf="LON:"+ticker.split(":")[0] if ticker.endswith(":LSE") else (
            "NYSE:"+ticker if ticker in {"PSX","GEV","ETN","FCX","PWR"} else "NASDAQ:"+ticker)
        tracked[ticker]=gf
    mon=book.worksheet("PRICE MONITOR")
    display=mon.get("A5:C36",value_render_option="FORMATTED_VALUE")
    for r in display:
        if len(r)>=3 and r[0] in {"HOLDING", "BENCH"} and r[1] and ":" in r[2]:
            tracked[r[1]]=r[2]
        if len(r)>=3 and r[0] in {"QQQ","SOXX","SPY","XLI","COPX","XLV"}:
            tracked[r[0]]=r[2]
    # The research universe is broader than the nine PRICE GATES replacements.
    # Read current symbols instead of freezing yesterday's holdings in code.
    for r in book.worksheet("CANDIDATE POOL").get("A5:D104"):
        if len(r) >= 4 and r[0] and ":" in str(r[3]):
            tracked[str(r[0]).strip()] = str(r[3]).strip()
    for r in mon.get("A39:C48"):
        if len(r) >= 3 and r[0] in {"GROWTH", "SECTOR"} and r[1] and ":" in str(r[2]):
            tracked[str(r[1])] = str(r[2])
    # Sector proxies used by the held refiner and upstream oil producer.
    for ticker in ("CRAK", "XOP"):
        tracked[ticker] = "NYSEARCA:" + ticker
    for r in book.worksheet("Portfolio").get("A8:D19", value_render_option="UNFORMATTED_VALUE"):
        if len(r) >= 4 and any(isinstance(v, (float, int)) and v > 0 for v in r[2:4]):
            ticker = str(r[0]).strip()
            if ticker not in tracked:
                raise RuntimeError(f"Portfolio!A8:D19: held/queued {ticker} has no verified symbol mapping")
    if not tracked:
        raise RuntimeError("PRICE GATES/CANDIDATE POOL/PRICE MONITOR: empty universe")
    return tracked


def existing_keys(ws):
    values=ws.get(f"A5:J{DATA_LAST_ROW}",value_render_option="UNFORMATTED_VALUE")
    keys=set()
    last=4
    prices={}
    for i,r in enumerate(values,start=5):
        if not any(v not in ("",None) for v in r):
            continue
        last=i
        if len(r)>=10 and r[9]:
            key=str(r[9]).strip()
            keys.add(key)
            if len(r)>3:
                try:
                    prices[key]=float(r[3])
                except (TypeError,ValueError):
                    pass
    return keys,prices,last


def get_google_fallback(book,ticker,now,gs):
    """Live GOOGLEFINANCE quotes are context, never a substitute daily bar.

    tradetime is not a certified close, and its timezone differs by venue.
    Yahoo daily bars and Stooq history provide the independently dated fallback.
    """
    return [], "Google Finance live quote is not a certified daily close"


def rows_match(actual, expected):
    if len(actual) != len(expected):
        return False
    for a, b in zip(actual, expected):
        if len(a) != len(b):
            return False
        for x, y in zip(a, b):
            if isinstance(x, (int, float)) and isinstance(y, (int, float)):
                if not math.isclose(x, y, rel_tol=1e-12, abs_tol=1e-8):
                    return False
            elif x != y:
                return False
    return True


def append_rows(ws,start,rows):
    # Numeric serials + unformatted closing prices preserve Sheets-native calculations.
    for pos in range(0,len(rows),200):
        batch=rows[pos:pos+200]
        r1=start+pos
        ws.update(range_name=f"A{r1}:J{r1+len(batch)-1}",values=batch,value_input_option="RAW")
        actual = ws.get(f"A{r1}:J{r1+len(batch)-1}", value_render_option="UNFORMATTED_VALUE")
        if not rows_match(actual, batch):
            raise RuntimeError(f"DAILY CLOSES!A{r1}:J{r1+len(batch)-1}: price write/readback mismatch")


def save_log(ws,when,mode,state,total,failures,row_number=None):
    history=ws.get("L5:P200",value_render_option="UNFORMATTED_VALUE")
    start=5
    for i,row in enumerate(history):
        if row and row[0] not in ("",None):
            start=6+i
    start = row_number or start
    if start>200:
        raise RuntimeError("DAILY CLOSES price-run log full L5:P200")
    ws.update(range_name=f"L{start}:P{start}",values=[[serial(when),mode,state,total," | ".join(failures)[:48000]]],value_input_option="RAW")
    ws.spreadsheet.batch_update({"requests":[{"repeatCell":{"range":{"sheetId":ws.id,"startRowIndex":start-1,"endRowIndex":start,"startColumnIndex":11,"endColumnIndex":12},"cell":{"userEnteredFormat":{"numberFormat":{"type":"DATE_TIME","pattern":"dd mmm yyyy HH:mm"}}},"fields":"userEnteredFormat.numberFormat"}}]})
    return start


def mark_fault_notes(book,issues):
    """Expose precise failures as the native hover note on the actual decision cell."""
    desk=book.worksheet("TRADE DESK")
    current=desk.get("B2:B13",value_render_option="FORMATTED_VALUE")
    requests=[]
    for i,row in enumerate(current):
        if not row or " — " not in row[0]:
            continue
        ticker=row[0].split(" — ",1)[0]
        # Do not erase research/review diagnostics (especially SYSTEM/B13).
        if ticker not in issues:
            continue
        text=issues[ticker]
        requests.append({"updateCells":{"range":{"sheetId":desk.id,"startRowIndex":i+1,"endRowIndex":i+2,"startColumnIndex":1,"endColumnIndex":2},"rows":[{"values":[{"note":text}]}],"fields":"note"}})
    if requests:
        desk.spreadsheet.batch_update({"requests":requests})


def main(mode="close",now=None):
    now=now or datetime.now(TORONTO)
    if now.date()>date(2026,11,27):
        print(json.dumps({"status":"Skipped","reason":"FT Game ended"}))
        return
    book=auth_book()
    ws=book.worksheet(SOURCE_SHEET)
    if ws.get("A4:J4")[0]!=DATA_COLUMNS:
        raise RuntimeError("DAILY CLOSES schema changed; refusing unsafe write")
    tracked=tracking(book)
    if ws.row_count < DATA_LAST_ROW:
        ws.resize(rows=DATA_LAST_ROW)
    ws.update(range_name="L4:P4", values=[LOG_COLUMNS], value_input_option="RAW")
    logrow = save_log(ws, now, mode, "Partial", len(tracked), ["Started; price data/write verification pending"])
    keys,original,last=existing_keys(ws)
    rows=[]; faults={}; bad=[]; success=0
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures={pool.submit(fetch_ticker,t,gs,now):t for t,gs in tracked.items()}
        for fut in as_completed(futures):
            t=futures[fut]
            try:
                ticker,gs,venue,unit,bars,expected,err=fut.result()
                if not bars:
                    fallback,extra=get_google_fallback(book,ticker,now,gs)
                    bars={dt:(p,url) for dt,p,url in fallback}
                    err=err+"; "+extra
                if not bars:
                    faults[ticker]=f"BACKEND PRICE GAP: DAILY CLOSES!A:J ticker {ticker}; PRICE MONITOR!B:S Google quote missing/uncertified. {err[:180]}"
                    bad.append(ticker+": no verified daily close; "+err[:110])
                    continue
                latest=max(bars)
                if latest != expected:
                    bad.append(ticker+f": latest {latest} vs completed {expected}")
                    faults[ticker]=f"STALE PRICE: DAILY CLOSES!A:J latest {latest}, expected by {expected}. Live Google quote PRICE MONITOR!B:S requires verification."
                else:
                    success+=1
                for d,(price,url) in bars.items():
                    key=f"{d.isoformat()}|{ticker}"
                    if key in keys:
                        if key in original and abs(original[key]/price-1)>0.025:
                            bad.append(ticker+f": source mismatch {d}, stored {original[key]} vs {price}")
                            faults[ticker]=f"PRICE MISMATCH: DAILY CLOSES!A:J for {key}. Preserve last verified historical price; compare independent source."
                        continue
                    rows.append([serial(d),ticker,gs,price,unit,venue,url,serial(now),"VERIFIED",key])
                    keys.add(key)
            except Exception as exc:
                bad.append(t+": unexpected "+str(exc)[:100])
                faults[t]="COLLECTOR FAILURE: DAILY CLOSES!A:J for "+t+"; "+str(exc)[:170]
    rows.sort(key=lambda r:(r[0],r[1]))
    if last+len(rows)>DATA_LAST_ROW:
        raise RuntimeError("DAILY CLOSES capacity exceeded; no overwrite performed")
    if rows:
        append_rows(ws,last+1,rows)
        # Native date and capture-time formatting for appended numeric serials.
        ws.spreadsheet.batch_update({"requests":[{"repeatCell":{"range":{"sheetId":ws.id,"startRowIndex":last,"endRowIndex":last+len(rows),"startColumnIndex":col,"endColumnIndex":col+1},"cell":{"userEnteredFormat":{"numberFormat":{"type":fmt,"pattern":pat}}},"fields":"userEnteredFormat.numberFormat"}} for col,fmt,pat in ((0,"DATE","dd mmm yyyy"),(7,"DATE_TIME","dd mmm yyyy HH:mm"))]})
    state="Complete" if success==len(tracked) and not bad else "Partial" if success else "Failed"
    receipt = f"run={os.getenv('GITHUB_RUN_ID', 'local')}; sha={os.getenv('GITHUB_SHA', 'local')}; rows={len(rows)}; price readback verified"
    save_log(ws,now,mode,state,len(tracked),bad + [receipt], row_number=logrow)
    mark_fault_notes(book,faults)
    # Explicit readback before reporting a successful write.
    log=ws.get("L5:P200",value_render_option="UNFORMATTED_VALUE")
    latest=[r for r in log if r and r[0] not in ("",None)][-1]
    if str(latest[2])!=state or str(latest[1])!=mode:
        raise RuntimeError("price run-log write/readback mismatch")
    print(json.dumps({"status":state,"tickers":len(tracked),"covered":success,"rows_added":len(rows),"failures":bad[:25]}))
    if state!="Complete":
        raise RuntimeError("Incomplete verified price coverage; see DAILY CLOSES!L:P")


if __name__=="__main__":
    main(sys.argv[1] if len(sys.argv)>1 else "close")

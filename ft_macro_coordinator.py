"""Sole FT macro writer: preserve history, atomic writes, verified run records.

FRED/EIA remain upstream in the pilot until a separately verified migration.
Do not enable the staged direct Apps Script writers while this relay is active.
"""
from __future__ import annotations
import json
import math
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo('America/Toronto')
EPOCH = datetime(1899, 12, 30)
DATA_HEADERS = ['Captured','Source','Series / contract','Label','Observation date','Value','Units','Frequency','Measure','Market / scope','Original URL','Observation key','Vintage','Verification','Context']
LOG_HEADERS = ['Run at','Source','Status','Series checked','New rows','Revised rows','Failures','Notes']


def parse_date(value):
    if isinstance(value, (float, int)):
        return (EPOCH + timedelta(days=value)).replace(tzinfo=TZ)
    if not value:
        raise ValueError('Missing date')
    text = str(value).strip()
    try:
        d = datetime.fromisoformat(text.replace('Z', '+00:00'))
    except ValueError:
        d = datetime.strptime(text, '%d/%m/%Y')
    return d.replace(tzinfo=TZ) if d.tzinfo is None else d.astimezone(TZ)


def serial(value):
    return (parse_date(value).replace(tzinfo=None) - EPOCH).total_seconds()/86400


def normalize(row):
    r = list(row) + [''] * (15-len(row))
    r = ['' if v is None else v for v in r[:15]]
    r[0], r[4] = serial(r[0]), serial(r[4])
    r[2] = str(r[2]).zfill(6) if r[1] == 'CFTC' else str(r[2])
    r[5] = float(r[5])
    if not math.isfinite(r[5]):
        raise ValueError('Invalid numeric observation')
    if not r[6] or not r[8] or not str(r[10]).startswith('https://'):
        raise ValueError('Missing units, measure or original URL')
    date = parse_date(r[4]).date().isoformat()
    r[11] = ':'.join([str(r[1]),r[2],date,str(r[8])])
    # Legacy verification labels are retained as provenance, not invented verification.
    if r[13] not in ('Verified data','Needs verification'):
        r[14] = str(r[14]) + '; prior provenance: ' + str(r[13])
        r[13] = 'Needs verification'
    return r


def merge_rows(existing, incoming, expected, now):
    merged, order, errors = {}, [], []
    for raw in existing:
        if not raw or not any(v not in ('',None) for v in raw):
            continue
        r = normalize(raw)
        if r[11] in merged:
            raise ValueError('Duplicate existing observation key')
        merged[r[11]] = r
        order.append(r[11])
    added = revised = 0
    refreshed = set()
    for raw in incoming:
        try:
            r = normalize(raw)
            group = (r[1],r[2])
            if group not in expected or parse_date(r[4]).date() > now.date() or parse_date(r[0]) > now + timedelta(minutes=5):
                raise ValueError('Unexpected series or future date')
            old = merged.get(r[11])
            if old is None:
                order.append(r[11]); added += 1
            elif old != r:
                revised += 1
            merged[r[11]] = r
            refreshed.add(group)
        except (ValueError, TypeError, OverflowError):
            errors.append('A source row failed identity/date/numeric validation')
    for group in sorted(expected-refreshed):
        errors.append(':'.join(group) + ': refresh missing; prior observations retained where available')
    return [merged[k] for k in order], added, revised, refreshed, errors


def diagnostics(rows, expected, now):
    details, errors = {}, []
    for group in sorted(expected):
        found = [r for r in rows if (r[1],r[2]) == group]
        key = ':'.join(group)
        if not found:
            details[key] = {'status':'Missing'}
            continue
        latest = max(found, key=lambda r:(r[4],r[0]))
        observation_age = (now.date()-parse_date(latest[4]).date()).days
        retrieval_age = round((now-parse_date(latest[0])).total_seconds()/3600,1)
        freq = str(latest[7]).lower()
        limit = 150 if freq in ('q','quarterly') else 45 if freq in ('m','monthly') else 14 if freq in ('w','weekly') else 7
        stale = observation_age > limit or retrieval_age > (192 if group[0]=='CFTC' else 36)
        details[key] = {'observation':parse_date(latest[4]).date().isoformat(),'age_days':observation_age,'retrieval_age_hours':retrieval_age,'status':'Stale' if stale else 'Current'}
        if stale:
            errors.append(key + ': stale observation or upstream retrieval')
    return details, errors


def cell(value):
    return {'userEnteredValue':{'numberValue':value} if isinstance(value,(int,float)) else {'stringValue':str(value)}}


def update(sheet_id, start_row, start_col, rows):
    width = len(rows[0])
    return {'updateCells':{'range':{'sheetId':sheet_id,'startRowIndex':start_row,'endRowIndex':start_row+len(rows),'startColumnIndex':start_col,'endColumnIndex':start_col+width},'rows':[{'values':[cell(v) for v in r]} for r in rows],'fields':'userEnteredValue'}}


def write_verified(book, ws, rows, log, log_row):
    meta = book.fetch_sheet_metadata(params={'fields':'sheets(properties(sheetId,title),tables(tableId,name,range))'})
    sheet = next(s for s in meta['sheets'] if s['properties']['sheetId']==ws.id)
    tables = {t['name']:t for t in sheet.get('tables',[])}
    if not {'FTMacroObservations','FTMacroRunLog'} <= tables.keys():
        raise ValueError('Missing native macro tables')
    requests = []
    needed = max(7+len(rows),log_row)
    if needed > ws.row_count:
        requests.append({'appendDimension':{'sheetId':ws.id,'dimension':'ROWS','length':needed-ws.row_count+50}})
    if rows:
        requests.append(update(ws.id,7,0,rows))
    # A provisional record cannot claim completion before readback.
    pending = list(log); pending[2] = 'Partial'; pending[7] = 'Write committed; readback pending. ' + str(log[7])
    requests.append(update(ws.id,log_row-1,17,[pending]))
    for name,end in [('FTMacroObservations',7+len(rows)),('FTMacroRunLog',log_row)]:
        t=tables[name]; region=dict(t['range']); region['endRowIndex']=max(region.get('endRowIndex',8),end)
        requests.append({'updateTable':{'table':{'tableId':t['tableId'],'range':region},'fields':'range'}})
    book.batch_update({'requests':requests})
    if rows:
        got = ws.get(f'A8:O{7+len(rows)}',value_render_option='UNFORMATTED_VALUE')
        if [normalize(r) for r in got] != rows:
            raise RuntimeError('Macro observation readback mismatch; provisional log remains Partial')
    book.batch_update({'requests':[update(ws.id,log_row-1,17,[log])]})
    got = ws.get(f'R{log_row}:Y{log_row}',value_render_option='UNFORMATTED_VALUE')
    if not got or got[0] != log:
        raise RuntimeError('Macro run log readback mismatch')


def main():
    import ft_macro_pipeline as p
    import pilot_macro_relay as relay
    now = datetime.now(TZ)
    if now > p.GAME_END:
        print(json.dumps({'status':'Stopped','reason':'FT game ended'})); return 0
    book = p.google_client().open_by_key(p.SPREADSHEET_ID)
    ws = book.worksheet(p.MACRO_SHEET)
    if ws.get('A7:O7')[0] != DATA_HEADERS or ws.get('R7:Y7')[0] != LOG_HEADERS:
        raise RuntimeError('Macro destination headers mismatch')
    existing = ws.get(f'A8:O{ws.row_count}',value_render_option='UNFORMATTED_VALUE')
    expected = {('FRED',s) for s in p.FRED_SERIES} | {('EIA',s) for s in relay.EIA} | {('CFTC',s) for s in list(p.CFTC_COMMODITIES)+list(p.CFTC_TFF)}
    incoming, errors = [], []
    for source,collect in [('FRED',relay.fred_rows),('EIA',relay.eia_rows),('CFTC',p.cftc_rows)]:
        try:
            result = collect(now.isoformat()); incoming.extend(result[0]); errors.extend(result[1])
        except Exception as exc:
            errors.append(source + ': collection failed (' + type(exc).__name__ + ')')
    rows,added,revised,refreshed,merge_errors = merge_rows(existing,incoming,expected,now)
    errors.extend(merge_errors)
    freshness,age_errors = diagnostics(rows,expected,now)
    errors = list(dict.fromkeys(errors+age_errors))
    status = 'Failed' if not refreshed else 'Partial' if errors else 'Complete'
    notes = json.dumps({'errors':errors,'series':freshness,'preserved_series':[':'.join(g) for g in sorted(expected-refreshed)],'rows_retained':len(rows),'readback':'verified before final status','upstream':'Pilot FRED/EIA; direct CFTC. Apps Script FT macro writers must remain inactive.'},ensure_ascii=False)
    log = [serial(now.isoformat()),'FRED / EIA / CFTC coordinator',status,len(expected),added,revised,len(errors),notes]
    logs = ws.get(f'R8:R{ws.row_count}',value_render_option='UNFORMATTED_VALUE')
    log_row = 8+max((i+1 for i,r in enumerate(logs) if r and r[0] not in ('',None)),default=0)
    write_verified(book,ws,rows,log,log_row)
    print(json.dumps({'status':status,'checked':len(expected),'added':added,'revised':revised,'rows_retained':len(rows),'log_row':log_row,'errors':errors,'readback':'verified'}))
    return 0 if status == 'Complete' else 1


if __name__ == '__main__':
    sys.exit(main())

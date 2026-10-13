"""Verified writes confined to Kalshi Feed in the active FT Game workbook."""
from __future__ import annotations
import json
import os
from datetime import datetime

from ft_github_signal_writer import google_client, update
from ft_kalshi_collector import (GAME_END, HEADERS, MAX_MARKETS, TORONTO, VERSION,
                                KalshiClient, collect, history_rows, merge_history, serial)

SPREADSHEET_ID = '1ItUi3uYPK4AvuHQ6PHIZTRvQnVhMiLONefTpVLEzGGE'
SHEET = 'Kalshi Feed'
LOG_HEADERS = ['Run at', 'Status', 'Series checked', 'Markets', 'Eligible context',
               'Research flags', 'Failures', 'Run ID', 'Notes']
TABLES = {
    'FTKalshiCurrent': (6, 0, HEADERS, 'A7:AD7'),
    'FTKalshiHistory': (34, 0, HEADERS, 'A35:AD35'),
    'FTKalshiRunLog': (6, 31, LOG_HEADERS, 'AF7:AN7'),
}


def validate(book, ws):
    if book.id != SPREADSHEET_ID or ws.title != SHEET:
        raise ValueError('Kalshi destination is not the active FT Game Kalshi Feed')
    meta = book.fetch_sheet_metadata(params={'fields':'sheets(properties(sheetId,title),tables(tableId,name,range))'})
    matches = [s for s in meta.get('sheets',[]) if s['properties']['sheetId']==ws.id]
    if len(matches)!=1:
        raise ValueError('Kalshi sheet metadata missing')
    tables = {t['name']:t for t in matches[0].get('tables',[])}
    for name,(row,col,headers,a1) in TABLES.items():
        if ws.get(a1) != [headers]:
            raise ValueError(f'{name}: headers mismatch')
        table = tables.get(name,{})
        rg = table.get('range',{})
        if (not table.get('tableId') or rg.get('sheetId')!=ws.id
            or rg.get('startRowIndex',0)!=row or rg.get('startColumnIndex',0)!=col
            or rg.get('endColumnIndex')!=col+len(headers)
            or not row+1 <= rg.get('endRowIndex',0) <= ws.row_count):
            raise ValueError(f'{name}: table bounds mismatch')
    if tables['FTKalshiCurrent']['range']['endRowIndex'] > 27:
        raise ValueError('Current table would overlap reserved layout')
    return tables


def equal_rows(got, expected, width):
    # Sheets trims trailing empty cells/rows. Compare normalized rectangles.
    got = [list(r)+['']*(width-len(r)) for r in got]
    got += [['']*width for _ in range(max(0,len(expected)-len(got)))]
    return got == expected


def next_log_row(ws):
    rows = ws.get(f'AF8:AN{ws.row_count}', value_render_option='UNFORMATTED_VALUE')
    occupied=[i for i,r in enumerate(rows) if any(v not in ('',None) for v in r)]
    return 9+max(occupied) if occupied else 8


def format_request(sheet_id,start,end,col,pattern,kind='NUMBER'):
    return {'repeatCell':{'range':{'sheetId':sheet_id,'startRowIndex':start,'endRowIndex':end,
        'startColumnIndex':col,'endColumnIndex':col+1},
        'cell':{'userEnteredFormat':{'numberFormat':{'type':kind,'pattern':pattern}}},
        'fields':'userEnteredFormat.numberFormat'}}


def persist(book,ws,current,history,log,log_row):
    tables=validate(book,ws)
    if len(current)>MAX_MARKETS:
        raise ValueError('More than 20 current markets')
    current=[list(r) for r in current]+[['']*len(HEADERS) for _ in range(MAX_MARKETS-len(current))]
    need=max(35+len(history),log_row)
    req=[]
    if need>ws.row_count:
        req.append({'appendDimension':{'sheetId':ws.id,'dimension':'ROWS','length':need-ws.row_count+100}})
    req.append(update(ws.id,7,0,current))
    if history:
        req.append(update(ws.id,35,0,history))
    pending=list(log);pending[1]='Partial';pending[8]='Write committed; readback pending. '+str(log[8])
    req += [update(ws.id,log_row-1,31,[pending]), update(ws.id,2,0,[['Last run',log[0],'Partial',log[3]]])]
    for start,end in [(7,27),(35,max(36,35+len(history)))]:
        for col in [0,8,16,18,19]:
            req.append(format_request(ws.id,start,end,col,'dd mmm yyyy HH:mm:ss','DATE_TIME'))
        for col in [6,9,10]:
            req.append(format_request(ws.id,start,end,col,'0.0%','PERCENT'))
        for col in [7,11,17]:
            req.append(format_request(ws.id,start,end,col,'0.0'))
    req.append(format_request(ws.id,log_row-1,log_row,31,'dd mmm yyyy HH:mm:ss','DATE_TIME'))
    req.append(format_request(ws.id,2,3,1,'dd mmm yyyy HH:mm:ss','DATE_TIME'))
    for name,end in [('FTKalshiCurrent',27),('FTKalshiHistory',max(36,35+len(history))),('FTKalshiRunLog',max(8,log_row))]:
        t=tables[name]; rg=dict(t['range']);rg['endRowIndex']=max(rg['endRowIndex'],end) if name=='FTKalshiRunLog' else end
        req.append({'updateTable':{'table':{'tableId':t['tableId'],'range':rg},'fields':'range'}})
    book.batch_update({'requests':req})
    if not equal_rows(ws.get('A8:AD27',value_render_option='UNFORMATTED_VALUE'),current,len(HEADERS)):
        raise RuntimeError('Current readback mismatch; log remains Partial')
    if history and not equal_rows(ws.get(f'A36:AD{35+len(history)}',value_render_option='UNFORMATTED_VALUE'),history,len(HEADERS)):
        raise RuntimeError('History readback mismatch; log remains Partial')
    verified=validate(book,ws)
    for name,end in [('FTKalshiCurrent',27),('FTKalshiHistory',max(36,35+len(history))),('FTKalshiRunLog',log_row)]:
        if verified[name]['range']['endRowIndex']<end:
            raise RuntimeError('Native table does not cover written data')
    book.batch_update({'requests':[update(ws.id,log_row-1,31,[log]),update(ws.id,2,0,[['Last run',log[0],log[1],log[3]]])]})
    if not equal_rows(ws.get(f'AF{log_row}:AN{log_row}',value_render_option='UNFORMATTED_VALUE'),[log],len(LOG_HEADERS)):
        raise RuntimeError('Run log readback mismatch')


def main():
    now=datetime.now(TORONTO)
    if now>GAME_END:
        print(json.dumps({'status':'Stopped','reason':'FT game ended'}));return 0
    book=google_client().open_by_key(SPREADSHEET_ID)
    ws=book.worksheet(SHEET)
    validate(book,ws)
    previous=history_rows(ws.get(f'A36:AD{ws.row_count}',value_render_option='UNFORMATTED_VALUE'))
    log_row=next_log_row(ws)
    run_id=os.environ.get('GITHUB_RUN_ID','manual')+':'+os.environ.get('GITHUB_RUN_ATTEMPT','1')
    log=[serial(now),'Started',0,0,0,0,0,run_id,'Collection started; current rows may be from prior run.']
    if log_row>ws.row_count:
        book.batch_update({'requests':[{'appendDimension':{'sheetId':ws.id,'dimension':'ROWS','length':100}}]})
    book.batch_update({'requests':[update(ws.id,log_row-1,31,[log]),update(ws.id,2,0,[['Last run',log[0],'Started',0]])]})
    try:
        result=collect(KalshiClient(),now,previous)
        rows=result['rows'];history=merge_history(previous,rows)
        log=[serial(now),result['status'],result['series_checked'],len(rows),
             sum(r[20]=='Eligible context' for r in rows),sum(r[21]=='RESEARCH MOVE' for r in rows),
             len(result['failures']),run_id,json.dumps({
                'version':VERSION,'requests':result['requests'],'failures':result['failures'],
                'coverage_gaps':result['gaps'],'semantics':'Context only; no scoring or trading. No market is not a negative signal.',
                'readback':'verified before final status'})]
        persist(book,ws,rows,history,log,log_row)
    except Exception as exc:
        # Leave old captures dated; failure status invalidates them for the new review.
        failure=list(log);failure[1]='Failed';failure[6]=max(1,failure[6]);failure[8]=f'{type(exc).__name__}: collection or verification failed; inspect Actions log.'
        book.batch_update({'requests':[update(ws.id,log_row-1,31,[failure]),update(ws.id,2,0,[['Last run',log[0],'Failed',0]])]})
        print(json.dumps({'status':'Failed','error':type(exc).__name__,'message':'Collection or verification failed; no Complete status recorded.'}));return 1
    print(json.dumps({'status':log[1],'markets':log[3],'eligible_context':log[4],'research_flags':log[5],
                      'failures':log[6],'readback':'verified','log_row':log_row}))
    return 1 if log[1] in ('Partial','Failed') else 0


if __name__=='__main__':
    raise SystemExit(main())

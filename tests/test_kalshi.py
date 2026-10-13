import copy
import unittest
from datetime import datetime, timedelta
from unittest.mock import Mock, patch

import ft_kalshi_collector as k
import ft_kalshi_writer as w

NOW=datetime(2026,10,13,13,0,tzinfo=k.TORONTO)


def market(ticker='KXFEDDECISION-26OCT-H0'):
    return {'ticker':ticker,'event_ticker':'KXFEDDECISION-26OCT','market_type':'binary',
        'status':'active','title':'Fed holds rates?','yes_sub_title':'No change',
        'close_time':'2026-10-28T17:59:00Z','expected_expiration_time':'2026-10-28T18:05:00Z',
        'expiration_time':'2027-01-27T18:01:00Z','notional_value_dollars':'1.0000',
        'yes_bid_dollars':'0.64','yes_ask_dollars':'0.66',
        'volume_24h_fp':'250.50','open_interest_fp':'1000.00',
        'yes_bid_size_fp':'100.00','yes_ask_size_fp':'100.00',
        'rules_primary':'Resolves Yes if rates do not change.','rules_secondary':'Cancellation means Yes.'}


def snap(m=None,now=NOW,previous=(),trade=None):
    return k.snapshot(m or market(),'Fed','Rates','KXFEDDECISION',now,
                      trade or now-timedelta(minutes=1),previous)


class MarketTests(unittest.TestCase):
    def test_decimals_and_legacy_cents(self):
        self.assertEqual(k.quotes(market()),(.64,.66,.65))
        self.assertEqual(k.quotes({'yes_bid':40,'yes_ask':44}),(.4,.44,.42))
        self.assertEqual(k.quotes({'yes_bid_dollars':'bad','yes_bid':40,'yes_ask':44}),(None,None,None))

    def test_invalid_quotes_never_zero_probability(self):
        for bad in [None,'NaN','Infinity',True,-.1,1.2]:
            m=market();m['yes_bid_dollars']=bad
            self.assertEqual(snap(m)[6],'')
        m=market();m['yes_bid_dollars']='.9';m['yes_ask_dollars']='.6'
        self.assertEqual(snap(m)[6],'')

    def test_extreme_thin_and_wide_markets_limited(self):
        for key,val in [('volume_24h_fp','99'),('open_interest_fp','99'),
                        ('yes_bid_size_fp','0'),('yes_ask_size_fp',None),
                        ('yes_bid_dollars','0'),('yes_ask_dollars','1'),
                        ('rules_primary',''),('notional_value_dollars','2'),('is_provisional',True)]:
            m=market();m[key]=val
            self.assertEqual(snap(m)[20],'Limited',key)
            self.assertNotEqual(snap(m)[21],'RESEARCH MOVE')
        m=market();m['yes_ask_dollars']='.8'
        self.assertIn('Spread',snap(m)[22])

    def test_no_recent_trade_limits_context(self):
        row=k.snapshot(market(),'Fed','Rates','KXFEDDECISION',NOW,None,[])
        self.assertEqual(row[20],'Limited')

    def test_horizon_uses_expected_expiry_not_longstop(self):
        self.assertTrue(k.in_horizon(market(),NOW))
        for key,val in [('close_time','2026-10-12T18:00:00Z'),
                        ('expected_expiration_time','2026-12-01T18:00:00Z'),
                        ('expected_expiration_time',None),('status','settled'),
                        ('mve_collection_ticker','COMBO')]:
            m=market();m[key]=val;self.assertFalse(k.in_horizon(m,NOW),key)

    def test_baseline_and_25pp_move(self):
        self.assertEqual(snap()[21],'BASELINE NEEDED')
        m=market();m['yes_bid_dollars']='.39';m['yes_ask_dollars']='.41'
        old=snap(m,NOW-timedelta(days=1))
        r=snap(previous=[old]);self.assertEqual(r[7],25);self.assertEqual(r[21],'RESEARCH MOVE')
        self.assertEqual(r[8],old[0])

    def test_same_day_rerun_not_movement_baseline(self):
        self.assertEqual(snap(previous=[snap(now=NOW-timedelta(hours=1))])[21],'BASELINE NEEDED')

    def test_rules_changed_and_stale_baseline_never_move(self):
        old=snap(now=NOW-timedelta(days=1))
        for field in ['rules_primary','rules_secondary','yes_sub_title','close_time']:
            m=market();m[field]='changed'
            self.assertNotEqual(snap(m,previous=[old])[21],'RESEARCH MOVE')
        self.assertEqual(snap(previous=[snap(now=NOW-timedelta(days=5))])[21],'BASELINE STALE')

    def test_weak_previous_observation_not_baseline_for_flag(self):
        old=snap(now=NOW-timedelta(days=1));old[20]='Limited';old[6]=.1
        self.assertEqual(snap(previous=[old])[21],'QUALITY LIMITED')

    def test_fractional_contracts_preserved(self):
        self.assertEqual(snap()[12],250.5)

    def test_history_idempotence_and_chronology(self):
        old=snap(now=NOW-timedelta(days=1));today=snap()
        a=k.merge_history([today,old],[today])
        self.assertEqual(len(a),2);self.assertEqual(a[0],old)
        newer=snap(now=NOW+timedelta(minutes=10))
        b=k.merge_history(a,[newer]);self.assertEqual(len(b),2);self.assertEqual(b[1][0],newer[0])
        with self.assertRaises(ValueError):k.history_rows([old,old])

    def test_selection_is_capped_and_stable_within_quality(self):
        ms=[market('KXFEDDECISION-26OCT-'+str(i)) for i in range(6)]
        chosen=k.select_markets(ms,NOW,4,[ms[-1]['ticker']])
        self.assertEqual(len(chosen),4);self.assertEqual(chosen[0]['ticker'],ms[-1]['ticker'])


class ClientTests(unittest.TestCase):
    def test_all_market_pages_and_repeated_cursor(self):
        c=k.KalshiClient(Mock());c.get=Mock(side_effect=[{'markets':[market()],'cursor':'x'},{'markets':[market()],'cursor':''}])
        self.assertEqual(len(c.markets('KXFEDDECISION')),1)
        self.assertEqual(c.get.call_args_list[1].args[1]['cursor'],'x')
        c.get=Mock(return_value={'markets':[],'cursor':'x'})
        with self.assertRaisesRegex(ValueError,'Repeated'):c.markets('KXFEDDECISION')

    def test_pagination_cap_is_failure_not_complete(self):
        c=k.KalshiClient(Mock());c.get=Mock(side_effect=[{'markets':[],'cursor':str(i)} for i in range(1,4)])
        with self.assertRaisesRegex(RuntimeError,'incomplete'):c.markets('KXFEDDECISION')

    def test_recent_trade_ignores_wrong_market_block_future_and_old(self):
        c=k.KalshiClient(Mock());ticker=market()['ticker']
        trades=[{'ticker':ticker,'created_time':(NOW-timedelta(minutes=10)).isoformat(),'is_block_trade':False},
                {'ticker':'OTHER','created_time':NOW.isoformat()},
                {'ticker':ticker,'created_time':NOW.isoformat(),'is_block_trade':True},
                {'ticker':ticker,'created_time':(NOW+timedelta(minutes=1)).isoformat()},
                {'ticker':ticker,'created_time':(NOW-timedelta(days=2)).isoformat()}]
        c.get=Mock(return_value={'trades':trades,'cursor':'more'})
        self.assertEqual(c.recent_trade(ticker,NOW),NOW-timedelta(minutes=10))

    def test_get_only_and_no_redirects(self):
        session=Mock();resp=Mock(status_code=200);resp.json.return_value={};session.get.return_value=resp
        c=k.KalshiClient(session);c.get('/markets')
        self.assertFalse(session.get.call_args.kwargs['allow_redirects'])
        self.assertNotIn('Authorization',session.get.call_args.kwargs['headers'])
        with self.assertRaises(ValueError):c.get('/portfolio/orders')
        with self.assertRaises(ValueError):c.get('https://evil.example')

    def test_429_retries_bounded(self):
        session=Mock();resp=Mock(status_code=429,headers={'Retry-After':'999'});session.get.return_value=resp
        with patch.object(k.time,'sleep') as sleep:
            with self.assertRaises(RuntimeError):k.KalshiClient(session).get('/markets')
        self.assertEqual(session.get.call_count,3);self.assertTrue(all(c.args[0]<=5 for c in sleep.call_args_list))

    def test_end_guard_has_no_network(self):
        c=Mock();r=k.collect(c,k.GAME_END+timedelta(seconds=1));self.assertEqual(r['status'],'Stopped');c.markets.assert_not_called()

    def test_partial_failure_preserves_successes(self):
        c=Mock(requests=3);c.markets.side_effect=[[market()],RuntimeError('unavailable')]
        c.recent_trade.return_value=NOW-timedelta(minutes=1)
        with patch.object(k,'SERIES',k.SERIES[:2]):r=k.collect(c,NOW)
        self.assertEqual(r['status'],'Partial');self.assertEqual(len(r['rows']),1);self.assertEqual(len(r['failures']),1)


class Sheet:
    id=270000001;title=w.SHEET;row_count=100
    def __init__(self):
        self.cells={}
        for _,(r,c,headers,_) in w.TABLES.items():
            for i,v in enumerate(headers):self.cells[r,c+i]=v
    def get(self,a1,**kwargs):
        import re
        a,r,b,s=re.fullmatch(r'([A-Z]+)(\d+):([A-Z]+)(\d+)',a1).groups()
        def col(x):
            v=0
            for c in x:v=v*26+ord(c)-64
            return v-1
        rows=[[self.cells.get((i,j),'') for j in range(col(a),col(b)+1)] for i in range(int(r)-1,int(s))]
        while rows and not any(v!='' for v in rows[-1]):rows.pop()
        return rows


class Book:
    id=w.SPREADSHEET_ID
    def __init__(self,ws):
        self.ws=ws;self.calls=[]
        self.tables={n:{'name':n,'tableId':n,'range':{'sheetId':ws.id,'startRowIndex':r,
            'endRowIndex':r+2,'startColumnIndex':c,'endColumnIndex':c+len(h)}} for n,(r,c,h,_) in w.TABLES.items()}
    def fetch_sheet_metadata(self,**kw):
        return {'sheets':[{'properties':{'sheetId':self.ws.id},'tables':list(self.tables.values())}]}
    def batch_update(self,body):
        self.calls.append(copy.deepcopy(body))
        for req in body['requests']:
            if 'updateCells' in req:
                q=req['updateCells'];self.assert_destination(q['range']['sheetId'])
                for dr,r in enumerate(q['rows']):
                    for dc,c in enumerate(r['values']):
                        self.ws.cells[q['range']['startRowIndex']+dr,q['range']['startColumnIndex']+dc]=next(iter(c['userEnteredValue'].values()))
            elif 'updateTable' in req:
                q=req['updateTable']['table'];self.tables[q['tableId']]['range']=q['range']
            elif 'appendDimension' in req:self.ws.row_count+=req['appendDimension']['length']
    def assert_destination(self,sheet_id):
        if sheet_id!=self.ws.id:raise AssertionError('Cross-sheet mutation')


class WriterTests(unittest.TestCase):
    def test_write_readback_tables_and_rerun_clear_old_current(self):
        ws=Sheet();b=Book(ws);r=snap();log=[k.serial(NOW),'Complete',7,1,1,0,0,'test','verified']
        ws.cells[26,0]=123 # prior current data must clear
        w.persist(b,ws,[r],[r],log,8)
        self.assertEqual(ws.cells[26,0],'');self.assertEqual(ws.get('AF8:AN8'),[log])
        self.assertEqual(b.tables['FTKalshiCurrent']['range']['endRowIndex'],27)
        self.assertEqual(b.tables['FTKalshiHistory']['range']['endRowIndex'],36)
        for call in b.calls:
            for req in call['requests']:
                if 'updateCells' in req:self.assertEqual(req['updateCells']['range']['sheetId'],ws.id)

    def test_wrong_destination_or_schema_blocks_all_writes(self):
        for wrong in ['book','header']:
            ws=Sheet();b=Book(ws)
            if wrong=='book':b.id='retired-workbook'
            else:ws.cells[6,0]='bad'
            with self.assertRaises(ValueError):w.persist(b,ws,[],[],[0,'Failed',0,0,0,0,1,'test','bad'],8)
            self.assertEqual(b.calls,[])

    def test_bad_readback_does_not_mark_complete(self):
        ws=Sheet();b=Book(ws);real=ws.get
        ws.get=lambda a1,**kw: [['mismatch']] if a1=='A8:AD27' else real(a1,**kw)
        with self.assertRaisesRegex(RuntimeError,'readback mismatch'):
            w.persist(b,ws,[snap()],[snap()],[0,'Complete',7,1,1,0,0,'test','good'],8)
        self.assertEqual(ws.cells[7,32],'Partial')

    def test_log_append_after_last_occupied_row(self):
        ws=Sheet();ws.cells[7,31]=1;ws.cells[9,31]=2
        self.assertEqual(w.next_log_row(ws),11)

    def test_trailing_empty_cells_normalize(self):
        self.assertTrue(w.equal_rows([[1]],[[1,''],['','']],2))

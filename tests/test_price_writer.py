"""Offline regression tests for daily FT close writer. No network, no credentials."""
import unittest
import copy
import json
from datetime import datetime, date, timedelta
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

import ft_price_writer as p


class DailyPriceTests(unittest.TestCase):
    def test_queued_zlab_mapping_and_unknown_symbol_remain_explicit(self):
        book=Mock()
        sheets={n:Mock() for n in ["PRICE GATES","PRICE MONITOR","Portfolio","CANDIDATE POOL"]}
        book.worksheet.side_effect=lambda n:sheets[n]
        for sheet in sheets.values():
            sheet.get.return_value=[]
        sheets["CANDIDATE POOL"].row_count=150
        sheets["Portfolio"].get.return_value=[
            ["ZLAB","Zai Lab ADS",0,.10],["UNKNOWN","Unmapped",.05,.05],["TOTAL","",.05,.15]]
        faults=[]
        mapped=p.tracking(book,mapping_faults=faults)
        self.assertEqual(mapped["ZLAB"],"NASDAQ:ZLAB")
        self.assertNotIn("UNKNOWN",mapped)
        self.assertEqual(len(faults),1)
        self.assertIn("held/queued UNKNOWN",faults[0])
        with self.assertRaisesRegex(RuntimeError,"UNKNOWN"):
            p.tracking(book)

    def test_unmapped_symbol_keeps_other_prices_but_never_certifies_complete(self):
        now=datetime(2026,11,12,8,tzinfo=p.TORONTO)
        end=date(2026,11,11)
        book=Mock();ws=Mock();ws.row_count=12000;ws.id=1
        book.worksheet.return_value=ws
        logs=[]
        def get(rng,**kw):
            return [p.DATA_COLUMNS] if rng=="A4:J4" else logs
        ws.get.side_effect=get
        def tracking(book,*,mapping_faults):
            mapping_faults.append("Portfolio!A8:D100: held/queued UNKNOWN has no verified symbol mapping")
            return {"AAA":"NYSE:AAA"}
        def save(ws,when,mode,state,total,failures,row_number=None):
            logs[:]=[[p.serial(when),mode,state,total," | ".join(failures)]]
            return 5
        fetched=("AAA","NYSE:AAA","US","USD",{end:(100,"https://prices.example")},end,"")
        with (patch.object(p,"auth_book",return_value=book),
              patch.object(p,"tracking",side_effect=tracking),
              patch.object(p,"save_log",side_effect=save),
              patch.object(p,"existing_keys",return_value=(set(),{},4,set())),
              patch.object(p,"fetch_ticker",return_value=fetched),
              patch.object(p,"append_rows") as append,
              patch.object(p,"mark_fault_notes")):
            with self.assertRaisesRegex(RuntimeError,"Incomplete verified price coverage"):
                p.main("pretrade",now)
        self.assertEqual(append.call_args.args[2][0][1],"AAA")
        self.assertEqual(logs[0][2:4],["Partial",2])
        self.assertIn("UNKNOWN",logs[0][4])

    def split_fixture(self,ticker="SOXX"):
        now=datetime(2026,11,12,8,tzinfo=p.TORONTO)
        capture=p.serial(datetime(2026,11,4,20,tzinfo=p.TORONTO))
        dates=[date(2026,10,26)+timedelta(days=n) for n in range(5)]
        rows=[[p.serial(day),ticker,"NASDAQ:"+ticker,300+3*n,"USD","US",
               "https://prices.example/history",capture,"VERIFIED",f"{day}|{ticker}"]
              for n,day in enumerate(dates)]
        bars={day:(100+n,"https://prices.example/history") for n,day in enumerate(dates)}
        return now,rows,bars

    def test_confirmed_splits_are_auditable_idempotent_and_remove_false_loss(self):
        for ticker in ("SOXX","ITA"):
            now,rows,bars=self.split_fixture(ticker)
            original=copy.deepcopy(rows)
            plan=p.split_plan(rows,ticker,bars,now)
            self.assertEqual(len(plan),5)
            self.assertEqual(plan[0][1][3],100)
            self.assertEqual(json.loads(plan[0][2])["original_row"],rows[0])
            self.assertEqual(json.loads(plan[0][2])["issuer_filing"],p.CONFIRMED_SPLITS[ticker][2])
            adjusted=[row for _,row,_ in plan]
            self.assertEqual(p.split_plan(adjusted,ticker,bars,now),[])
            self.assertEqual(rows,original)
            self.assertAlmostEqual(104/adjusted[-1][3]-1,0)
            self.assertLess(104/rows[-1][3]-1,-.6)

    def test_split_needs_confirmed_event_and_agreeing_history(self):
        now,rows,bars=self.split_fixture()
        self.assertEqual(p.split_plan(rows,"UNKNOWN",bars,now),[])
        self.assertEqual(p.split_plan(rows,"SOXX",bars,now.replace(day=4)),[])
        with self.assertRaisesRegex(RuntimeError,"five matching"):
            p.split_plan(rows,"SOXX",dict(list(bars.items())[:4]),now)
        bars[date(2026,10,26)]=(130,"https://prices.example/history")
        with self.assertRaisesRegex(RuntimeError,"does not explain"):
            p.split_plan(rows,"SOXX",bars,now)

    def test_split_never_divides_postsplit_or_later_imports_by_guess(self):
        now,rows,bars=self.split_fixture()
        later=copy.deepcopy(rows[0]);later[7]=p.serial(now)
        post=copy.deepcopy(rows[0]);post[0]=p.serial(date(2026,11,5));post[9]="2026-11-05|SOXX"
        post[3]=100
        plan=p.split_plan(rows+[later,post],"SOXX",bars,now)
        self.assertEqual(len(plan),5)
        self.assertTrue(all(n<=9 for n,_,_ in plan))

    def test_split_preserves_small_fallback_vendor_difference_but_rejects_material_gap(self):
        now,rows,bars=self.split_fixture()
        rows[-1][3]=312.45  # 0.144% cross-provider difference after the 3:1 split.
        plan=p.split_plan(rows,"SOXX",bars,now)
        self.assertEqual(plan[-1][1][3],104.15)
        self.assertEqual(json.loads(plan[-1][2])["original_row"][3],312.45)
        rows[-1][3]=315  # 0.96% exceeds the split-specific 0.25% allowance.
        with self.assertRaisesRegex(RuntimeError,"normalized archive"):
            p.split_plan(rows,"SOXX",bars,now)

    def test_split_write_must_read_back_and_changes_only_price_provenance(self):
        now,rows,bars=self.split_fixture()
        plan=p.split_plan(rows,"SOXX",bars,now)
        ws=Mock();ws.id=7;ws.row_count=100
        ws.get.side_effect=[rows,[row for _,row,_ in plan]]
        changed=p.reconcile_confirmed_split(ws,"SOXX",bars,now)
        self.assertEqual(len(changed),5)
        requests=ws.spreadsheet.batch_update.call_args.args[0]["requests"]
        self.assertEqual({r["updateCells"]["range"]["startColumnIndex"] for r in requests},{3,6})
        self.assertTrue(all(r["updateCells"]["range"]["sheetId"]==7 for r in requests))
        ws.get.side_effect=[rows,rows]
        with self.assertRaisesRegex(RuntimeError,"readback mismatch"):
            p.reconcile_confirmed_split(ws,"SOXX",bars,now)

    def test_final_receipt_uses_completion_not_started_time(self):
        started=datetime(2026,10,15,13,50,tzinfo=p.TORONTO)
        completed=datetime(2026,10,15,13,54,tzinfo=p.TORONTO)
        self.assertEqual(p.completion_receipt_time(started,False,lambda zone: completed),completed)
        self.assertEqual(p.completion_receipt_time(started,True,lambda zone: completed),started)

    def test_recovered_ticker_note_clears_without_touching_system_note(self):
        book=Mock()
        desk=Mock()
        desk.id=42
        desk.get.return_value=[["PSX — HOLD 13%"],["FANG — HOLD 12%"],
                               ["SYSTEM — ⚠ BACKEND"]]
        book.worksheet.return_value=desk
        p.mark_fault_notes(book,{"PSX":"DAILY CLOSES!A5:J5: stale"})
        requests=desk.spreadsheet.batch_update.call_args.args[0]["requests"]
        self.assertEqual(len(requests),2)
        self.assertEqual(requests[0]["updateCells"]["rows"][0]["values"][0]["note"],
                         "DAILY CLOSES!A5:J5: stale")
        self.assertEqual(requests[1]["updateCells"]["rows"][0]["values"][0],{})

    def test_holiday_and_early_close(self):
        zone = ZoneInfo("America/New_York")
        self.assertEqual(p.last_completed(datetime(2026,11,26,19,tzinfo=zone),zone,p.clock(16)),date(2026,11,25))
        self.assertEqual(p.last_completed(datetime(2026,11,27,13,10,tzinfo=zone),zone,p.clock(16)),date(2026,11,25))
        self.assertEqual(p.last_completed(datetime(2026,11,27,14,tzinfo=zone),zone,p.clock(16)),date(2026,11,27))

    def test_readback_rejects_missing_or_changed_prices(self):
        self.assertTrue(p.rows_match([[46309.123456789,123.25]],[[46309.123456790,123.25]]))
        self.assertFalse(p.rows_match([[46309,123.24]],[[46309,123.25]]))
        self.assertFalse(p.rows_match([],[[46309,123.25]]))

    def test_live_quote_never_certifies_close(self):
        self.assertEqual(p.get_google_fallback(None,"MSFT",None,"NASDAQ:MSFT")[0],[])

    def test_candidate_universe_and_exited_monitors_are_included(self):
        book=Mock()
        sheets={n:Mock() for n in ["PRICE GATES","PRICE MONITOR","Portfolio","CANDIDATE POOL"]}
        book.worksheet.side_effect=lambda n:sheets[n]
        sheets["PRICE GATES"].get.return_value=[["PSX"]]
        sheets["PRICE MONITOR"].get.return_value=[["BENCH","CRWV","NASDAQ:CRWV"],["SPY","","NYSEARCA:SPY"]]
        sheets["CANDIDATE POOL"].get.return_value=[["WMT","Retail","Watch","NASDAQ:WMT"]]
        sheets["Portfolio"].get.return_value=[["PSX","Phillips",.13,.13],["TOTAL","",.13,.13]]
        self.assertTrue({"PSX","CRWV","SPY","WMT","CRAK","XOP"}.issubset(p.tracking(book)))
        self.assertTrue(set(p.extra_symbols()).issubset(p.tracking(book)))

    def test_existing_verified_archive_can_certify_same_session(self):
        d=date(2026,10,14)
        good=[p.serial(d),"BHP:LSE","LON:BHP",3165.0,"GBX","LSE",
              "https://query1.finance.yahoo.com/v8/finance/chart/BHP.L",
              p.serial(datetime(2026,10,14,18,tzinfo=p.TORONTO)),
              "VERIFIED","2026-10-14|BHP:LSE"]
        ws=Mock()
        ws.get.return_value=[good]
        keys,prices,last,verified=p.existing_keys(ws)
        self.assertEqual(last,5)
        self.assertEqual(prices["2026-10-14|BHP:LSE"],3165.0)
        self.assertIn("2026-10-14|BHP:LSE",verified)

        # Any missing quality, wrong date/venue, or absent URL prevents
        # an old or unverified row from masking a missing upstream close.
        for field,bad in ((0,p.serial(date(2026,10,13))),
                          (4,"GBP"),(5,"US"),(6,""),
                          (8,"Unverified"),(3,0)):
            damaged=list(good)
            damaged[field]=bad
            ws.get.return_value=[damaged]
            _,_,_,trusted=p.existing_keys(ws)
            self.assertNotIn("2026-10-14|BHP:LSE",trusted)

    def test_market_closes_are_exchange_specific(self):
        now = datetime(2026, 10, 14, 14, 10, tzinfo=ZoneInfo("America/Toronto"))
        self.assertEqual(p.last_completed(now, ZoneInfo("America/New_York"), p.clock(16)), date(2026,10,13))
        self.assertEqual(p.last_completed(now, ZoneInfo("Europe/London"), p.clock(16,30)), date(2026,10,14))

    def test_london_gbx_not_usd_or_gbp(self):
        self.assertEqual(p.venue_info("TLW:LSE", "LON:TLW")[:2], ("LSE","GBX"))
        self.assertEqual(p.yahoo_symbol("BHP:LSE"), "BHP.L")
        self.assertEqual(p.venue_info("NVDA", "NASDAQ:NVDA")[:2], ("US","USD"))

    def test_yahoo_close_not_intraday(self):
        d=datetime(2026,10,14,14,10,tzinfo=ZoneInfo("America/Toronto"))
        payload={"chart":{"error":None,"result":[{"meta":{"currency":"USD"},
            "timestamp":[1791900000,1791986400],"indicators":{"quote":[{"close":[123.5,125.0]}]}}]}}
        resp=Mock()
        resp.json.return_value=payload
        resp.url="https://query1.finance.yahoo.com/v8/finance/chart/NVDA"
        with patch.object(p.HTTP,"get",return_value=resp):
            ticker,_,_,_,bars,end,err=p.fetch_ticker("NVDA","NASDAQ:NVDA",d)
        self.assertEqual(ticker,"NVDA")
        self.assertEqual(end,date(2026,10,13))
        self.assertTrue(all(x<=date(2026,10,13) for x in bars))
        self.assertFalse(err)

    def test_second_yahoo_host_supplies_missing_current_session(self):
        zone=ZoneInfo("America/New_York")
        d13=int(datetime(2026,10,13,9,30,tzinfo=zone).timestamp())
        d14=int(datetime(2026,10,14,9,30,tzinfo=zone).timestamp())
        def response(stamps):
            res=Mock()
            res.json.return_value={"chart":{"error":None,"result":[{
                "meta":{"currency":"USD"}, "timestamp":stamps,
                "indicators":{"quote":[{"close":[100.0+i for i in range(len(stamps))]}]}
            }]}}
            res.url="https://query2.finance.yahoo.com/"
            return res
        with patch.object(p.HTTP,"get",side_effect=[response([d13]),response([d13,d14])]) as req:
            bars,why=p.yahoo_history("SPY",datetime(2026,10,14,20,tzinfo=p.TORONTO),
                                     required_session=date(2026,10,14))
        self.assertEqual(req.call_count,2)
        self.assertEqual(max(r[0] for r in bars),date(2026,10,14))
        self.assertEqual(why,"")

    def test_stale_yahoo_uses_independently_dated_stooq_close_only(self):
        now=datetime(2026,10,14,20,tzinfo=p.TORONTO)
        y=[(date(2026,10,13),100.0,"https://query1.finance.yahoo.com/")]
        alt=[(date(2026,10,13),99.0,"https://stooq.com/"),
             (date(2026,10,14),98.0,"https://stooq.com/")]
        with (patch.object(p,"yahoo_history",return_value=(y,"stale Yahoo")) as yh,
              patch.object(p,"stooq_history",return_value=(alt,"")) as st):
            *_,bars,end,err=p.fetch_ticker("SPY","NYSEARCA:SPY",now)
        yh.assert_called_once_with("SPY",now,required_session=date(2026,10,14))
        st.assert_called_once()
        self.assertEqual(end,date(2026,10,14))
        self.assertEqual(bars[date(2026,10,13)][0],100.0)
        self.assertEqual(bars[date(2026,10,14)],(98.0,"https://stooq.com/"))

    def test_no_provider_current_close_remains_unverified(self):
        now=datetime(2026,10,14,20,tzinfo=p.TORONTO)
        y=[(date(2026,10,13),100.0,"https://query1.finance.yahoo.com/")]
        with (patch.object(p,"yahoo_history",return_value=(y,"stale")),
              patch.object(p,"stooq_history",return_value=([],"Stooq stale")),
              patch.object(p,"stockanalysis_bars",return_value=([],"independent source unavailable"))):
            *_,bars,end,err=p.fetch_ticker("SPY","NYSEARCA:SPY",now)
        self.assertNotIn(end,bars)
        self.assertIn("Stooq stale",err)

    def test_london_close_does_not_accept_untrusted_stooq_mapping(self):
        now=datetime(2026,10,14,20,tzinfo=p.TORONTO)
        y=[(date(2026,10,13),100.0,"https://query1.finance.yahoo.com/")]
        with patch.object(p,"yahoo_history",return_value=(y,"stale")):
            *_,bars,end,err=p.fetch_ticker("BHP:LSE","LON:BHP",now)
        self.assertNotIn(end,bars)
        self.assertIn("not trusted",err)

    def test_wrong_currency_is_rejected(self):
        payload={"chart":{"error":None,"result":[{"meta":{"currency":"EUR"},
            "timestamp":[1791900000],"indicators":{"quote":[{"close":[123.5]}]}}]}}
        resp=Mock()
        resp.json.return_value=payload
        resp.url="https://query1.finance.yahoo.com"
        with patch.object(p.HTTP,"get",return_value=resp):
            rows,why=p.yahoo_history("MSFT",datetime(2026,10,14,tzinfo=p.TORONTO))
        self.assertEqual(rows,[])
        self.assertIn("currency",why)


    def test_candidate_scan_uses_live_grid_not_old_row_104(self):
        book=Mock()
        sheets={n:Mock() for n in ["PRICE GATES","PRICE MONITOR","Portfolio","CANDIDATE POOL"]}
        book.worksheet.side_effect=lambda n:sheets[n]
        sheets["PRICE GATES"].get.return_value=[["PSX"]]
        sheets["PRICE MONITOR"].get.return_value=[]
        sheets["CANDIDATE POOL"].row_count=150
        sheets["CANDIDATE POOL"].get.return_value=[["PGR","Insurance","Scout","NYSE:PGR"]]
        sheets["Portfolio"].get.return_value=[["PSX","Phillips",.05,.05],["TOTAL","",.05,.05]]
        self.assertIn("PGR",p.tracking(book))
        sheets["CANDIDATE POOL"].get.assert_called_once_with("A5:D150")

    def test_price_run_log_grows_past_200_without_overwrite(self):
        ws=Mock()
        ws.id=77
        ws.row_count=200
        ws.get.return_value=[[float(n)] for n in range(196)]
        row=p.save_log(ws,datetime(2026,10,23,tzinfo=p.TORONTO),"close","Partial",3,["test"])
        self.assertEqual(row,201)
        ws.add_rows.assert_called_once()
        self.assertEqual(ws.update.call_args.kwargs["range_name"],"L201:P201")

    def test_existing_archive_read_uses_actual_grid(self):
        ws=Mock()
        ws.row_count=14000
        ws.get.return_value=[]
        p.existing_keys(ws)
        ws.get.assert_called_once_with("A5:J14000",value_render_option="UNFORMATTED_VALUE")



if __name__=="__main__":
    unittest.main()


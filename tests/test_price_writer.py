"""Offline regression tests for daily FT close writer. No network, no credentials."""
import unittest
from datetime import datetime, date
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

import ft_price_writer as p


class DailyPriceTests(unittest.TestCase):
    def test_final_receipt_uses_completion_not_started_time(self):
        started=datetime(2026,10,15,13,50,tzinfo=p.TORONTO)
        completed=datetime(2026,10,15,13,54,tzinfo=p.TORONTO)
        self.assertEqual(p.completion_receipt_time(started,False,lambda zone: completed),completed)
        self.assertEqual(p.completion_receipt_time(started,True,lambda zone: completed),started)

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
        sheets["Portfolio"].get.return_value=[["PSX","Phillips",.13,.13]]
        self.assertEqual(set(p.tracking(book)),{"PSX","CRWV","SPY","WMT","CRAK","XOP"})

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
              patch.object(p,"stooq_history",return_value=([],"Stooq stale"))):
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


if __name__=="__main__":
    unittest.main()

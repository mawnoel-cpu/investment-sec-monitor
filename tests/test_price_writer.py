"""Offline regression tests for daily FT close writer. No network, no credentials."""
import unittest
from datetime import datetime, date
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

import ft_price_writer as p


class DailyPriceTests(unittest.TestCase):
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

"""Offline regression tests for daily FT close writer. No network, no credentials."""
import unittest
from datetime import datetime, date
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

import ft_price_writer as p


class DailyPriceTests(unittest.TestCase):
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

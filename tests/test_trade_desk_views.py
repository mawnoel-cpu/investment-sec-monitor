import unittest
from datetime import datetime
import ft_trade_desk as views
import ft_pipeline_health as health
import ft_price_writer as prices


class HoldingViewRegressionTests(unittest.TestCase):
    def fixture(self):
        weights = [("PSX",.13),("FANG",.12),("MSFT",.05),("VRTX",.05),
                   ("LLY",.10),("LMT",.05),("AMGN",.10),("RTX",.10),("NOW",.05)]
        portfolio = [[t,t,w,w] for t,w in weights]
        body = [list(r) for r in portfolio]
        total = ["TOTAL SELL",0,"","TOTAL BUY",0,"","TARGET INVESTED",.75,"","TARGET CASH",.25]
        gates = [[t,100,"","","","","",70,"HOLD / WATCH","OK"] for t,_ in weights]
        return portfolio,body+[total],gates

    def test_nine_holdings_and_seventy_five_percent_pass(self):
        self.assertEqual(health.view_health(*self.fixture()),[])

    def test_old_forty_five_percent_summary_fails_both_totals(self):
        portfolio,analysis,gates = self.fixture()
        analysis[-1][7],analysis[-1][10] = .45,.55
        faults = health.view_health(portfolio,analysis,gates)
        self.assertEqual(len(faults),2)
        self.assertTrue(any("allocation total mismatch" in f for f in faults))

    def test_missing_new_holding_and_price_gate_cannot_pass(self):
        portfolio,analysis,gates = self.fixture()
        analysis = [r for r in analysis if r[0]!="RTX"]
        gates = [r for r in gates if r[0]!="NOW"]
        faults = health.view_health(portfolio,analysis,gates)
        self.assertTrue(any("RTX missing" in f for f in faults))
        self.assertTrue(any("NOW missing" in f for f in faults))

    def test_new_row_preserves_existing_human_targets_and_queued_order(self):
        portfolio,analysis,_ = self.fixture()
        analysis[2][3] = 0  # A deliberate existing human sell target must survive.
        analysis = [r for r in analysis if r[0]!="RTX"]
        requests = views.view_requests(1,2,analysis,portfolio)
        inserted = [r["insertDimension"] for r in requests if "insertDimension" in r]
        self.assertEqual(len(inserted),1)
        self.assertEqual(inserted[0]["range"]["endIndex"]-inserted[0]["range"]["startIndex"],1)
        target_writes = [r["updateCells"] for r in requests if "updateCells" in r
                         and r["updateCells"]["start"]["sheetId"]==1
                         and r["updateCells"]["start"]["columnIndex"]==3]
        self.assertEqual(len(target_writes),1)
        self.assertEqual(target_writes[0]["rows"][0]["values"][0]["userEnteredValue"],{"numberValue":.10})
        self.assertFalse(any("insertDimension" in r for r in views.view_requests(1,2,self.fixture()[1],portfolio)))

    def test_unrecognized_table_fails_instead_of_overwriting_history(self):
        portfolio,analysis,_ = self.fixture()
        with self.assertRaisesRegex(ValueError,"TOTAL SELL"):
            views.view_requests(1,2,analysis[:-1],portfolio)

    def test_displayed_text_percentage_is_not_silently_excluded(self):
        portfolio,analysis,gates = self.fixture()
        analysis[4][3] = "10%"
        self.assertTrue(any("nonnumeric target" in f for f in health.view_health(portfolio,analysis,gates)))
        requests = views.view_requests(1,2,analysis,portfolio)
        normalized = [r["updateCells"] for r in requests if "updateCells" in r
                      and r["updateCells"]["start"]=={"sheetId":1,"rowIndex":7,"columnIndex":3}]
        self.assertEqual(normalized[0]["rows"][0]["values"][0]["userEnteredValue"],{"numberValue":.1})

    def test_account_total_is_checked_separately_from_market_momentum(self):
        now = datetime(2026,10,21,17,tzinfo=prices.TORONTO)
        stamp = prices.serial(datetime(2026,10,21,15,59,tzinfo=prices.TORONTO))
        row = [prices.serial(now.date()),90778,"Verified — screenshot"]+[""]*19+[stamp]
        note,faults = health.performance_status(now,[row],90778,90778)
        self.assertIn("-9.22%",note)
        self.assertEqual(faults,[])
        self.assertEqual(len(health.performance_status(now,[row],91271,101167)[1]),2)
        # Old manually supplied data is disclosed, never an automatic veto on selling.
        later = datetime(2026,10,22,17,tzinfo=prices.TORONTO)
        note,faults = health.performance_status(later,[row],90778,90778)
        self.assertIn("OLDER ACCOUNT SNAPSHOT",note)
        self.assertEqual(faults,[])


if __name__ == "__main__":
    unittest.main()

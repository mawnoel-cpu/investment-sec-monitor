import unittest
from datetime import datetime
from unittest.mock import patch
import ft_stress_panel as s
import ft_financial_stress as f
import ft_pipeline_health as h
import ft_price_writer as p


class RepairRegressionTests(unittest.TestCase):
    def fixture(self):
        rows=[[] for _ in range(98)]
        names=[("PSX",.05),("FANG",.05),("AMGN",.15),("XOM",.10),
               ("EOG",.05),("LLY",.10),("VRTX",.05),("LMT",.05),
               ("RTX",.10),("WMT",.10),("CB",.05),("CME",.10),("PGR",.05)]
        for n,(t,w) in enumerate(names,3):
            rows[n]=[t,t,w,w,"HOLD","Human rationale"]
        rows[16]=["TOTAL SELL"]
        for n,label in ((73,"AI spending"),(74,"Financing stress"),(75,"Rate / valuation stress")):
            rows[n]=[label,0,0,0,-.01,-.01,0]
        rows[77]=["Oil/refining reversal (hypothetical)",.25,.25]
        rows[80]=["Ticker","Held","Proposed","AI-linked flag"]
        for n,(t,w) in enumerate(names[:3],81):
            rows[n]=[t,w,w,0,0,0,-.03,-.04,-.04]
        rows[93]=["Assumptions inherited"]
        return rows

    def test_prose_writer_never_touches_same_ticker_below_boundary(self):
        rows=[["PSX","Company",.05,.05,"HOLD","Keep reason"],["TOTAL SELL"],
              ["PSX",.05,.05,0,0,0]]
        a={"PSX":{"options":"hold","scope":"partial","reversal":"reassess"}}
        with patch.object(f,"summary",return_value="verified summary"):
            updates=f.reason_updates(rows,a,datetime(2026,10,24))
        self.assertEqual([r for r,_ in updates],[4])
        self.assertEqual(rows[-1][-1],0)

    def test_missing_boundary_rejects_before_writing(self):
        with self.assertRaisesRegex(ValueError,"boundary missing"):
            f.reason_updates([["PSX"]],{},datetime(2026,10,24))

    def test_old_panel_missing_ten_holdings_and_energy_fails(self):
        rows=self.fixture(); rows[77][1:3]=[.1,.1]
        faults,gaps=s.health(rows)
        self.assertEqual(len([x for x in faults if "missing/duplicate" in x]),10)
        self.assertEqual(len([x for x in faults if "energy" in x]),2)

    def test_repair_expands_and_maps_all_thirteen_without_inventing_shocks(self):
        rows=self.fixture()
        rows[81][5]="0\nFINANCIAL STRESS [2026-10-23]: corrupt"
        requests=s.requests(1,rows)
        insert=[r for r in requests if "insertDimension" in r]
        self.assertEqual(len(insert),1)
        self.assertEqual(insert[0]["insertDimension"]["range"]["endIndex"]-
                         insert[0]["insertDimension"]["range"]["startIndex"],1)
        cells={(r["updateCells"]["start"]["rowIndex"]+1,
                r["updateCells"]["start"]["columnIndex"]+1):
               r["updateCells"]["rows"][0]["values"][0]
               for r in requests if "updateCells" in r}
        self.assertEqual(cells[82,6]["userEnteredValue"],{"numberValue":0})
        self.assertEqual(cells[85,7],{})  # XOM shock unknown, never zero
        self.assertIn('"XOM"',cells[78,2]["userEnteredValue"]["formulaValue"])
        self.assertIn('"UNKNOWN"',cells[74,5]["userEnteredValue"]["formulaValue"])
        self.assertEqual(cells[94,1]["userEnteredValue"],{"stringValue":"PGR"})
        self.assertFalse(any(n<72 for n,c in cells)) # no decisions/orders changed

    def test_non_numeric_flags_are_faults_and_blanks_are_explicit_gaps(self):
        rows=self.fixture(); rows[81][5]="0\nFINANCIAL STRESS [x]"
        faults,_=s.health(rows)
        self.assertTrue(any("F82" in x for x in faults))
        rows[81][5]=""
        _,gaps=s.health(rows)
        self.assertIn("F82",gaps)

    def test_weekend_audit_retains_friday_without_backdating_actual_clock(self):
        now=datetime(2026,10,24,12,tzinfo=p.TORONTO)
        self.assertEqual(str(h.decision_clock(now).date()),"2026-10-23")
        self.assertEqual(str(now.date()),"2026-10-24")

    def test_started_on_time_completed_late_is_missed(self):
        now=datetime(2026,10,23,15,tzinfo=p.TORONTO);day=int(p.serial(now))
        review=[day+14.3/24,"Daily",day+14.8/24,"p","r","SAVED","s","h","c","Daily:2026-10-23","Sources!A46:I46",day+.5,"{}",day+14.8/24]
        status=h.delivery_status(now,[],[],[review],review_ok=True)
        self.assertIn("Reviews!N8=14:48",status)

    def test_manual_morning_intake_does_not_certify_scheduled_delivery(self):
        now=datetime(2026,10,23,15,tzinfo=p.TORONTO);day=int(p.serial(now))
        row=[day+7/24,"Partial",10,10,5,0,0,"FINALIZED additional recovery",day+7.25/24]
        self.assertFalse(h.scheduled_intake(row))
        self.assertIn("scheduled research",h.delivery_status(now,[],[row],[]))

    def test_selected_candidates_cannot_replace_baseline_snapshots(self):
        now=datetime(2026,10,23,15,tzinfo=p.TORONTO)
        row=[""]*13;row[9]="Daily:2026-10-23";row[12]='{"candidates":["CRM","JPM"]}'
        self.assertEqual(h.required_symbols([["PSX","PSX",.05,.05]],[["NVDA"],["AVGO"]],[row],now),
                         {"PSX","NVDA","AVGO","CRM","JPM"})

    def test_zero_or_missing_price_cannot_certify_snapshot(self):
        from test_pipeline_health import PersistenceTests
        for col,value in ((11,0),(11,""),(14,0),(15,"")):
            args=list(PersistenceTests().fixture())
            args[3][0][col]=value
            self.assertEqual(h.evaluate(*args)[1],"INCOMPLETE")


if __name__=="__main__":
    unittest.main()

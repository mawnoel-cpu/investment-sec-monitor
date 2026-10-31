"""Fault injection on synthetic fixtures only; no workbook, Gmail or orders."""
import ast
import copy
import inspect
import json
import unittest
from datetime import date, datetime, timedelta
from unittest.mock import patch

import ft_decision_inputs as inputs
import ft_financial_stress as finance
import ft_pipeline_health as health
import ft_price_writer as prices
import ft_rotation_replay as replay
import ft_sector_rotation as rotation
import ft_trade_desk as desk
from ft_rotation_universe import SECTORS, INDUSTRIES

NOW = datetime(2026,10,20,14,35,tzinfo=prices.TORONTO)
END = date(2026,10,19)


def market(end=END):
    days = rotation.session_window(end,65)
    return {ticker:{day:100+i*.1 for i,day in enumerate(days)}
            for ticker in ["SPY","AAA","BBB","CCC","DDD","EEE","FFF",*SECTORS,*INDUSTRIES]}


def positions():
    return [dict(ticker=t,held=w,queued=w) for t,w in
            zip(["AAA","BBB","CCC","DDD","EEE","FFF"],[.15,.15,.15,.10,.10,.10])]


def review():
    start = prices.serial(NOW.replace(minute=20))
    row = [start,"Daily",start,"portfolio","risk","SAVED — assessed",
           "sources","verified","", "Daily:2026-10-20"]+[""]*16
    row[11],row[13] = start+.001,start+.008
    for n,(_,tc,rc) in enumerate(health.CHECKPOINT_STAGES,1):
        row[tc],row[rc] = start+n*.001,"PASS"
    return row


class AdversarialWorkflowTests(unittest.TestCase):
    def setUp(self):
        # Any accidental live write/network entry is a test failure.
        self.guard = patch.object(prices,"auth_book",side_effect=AssertionError("Live access forbidden"))
        self.guard.start()
        self.addCleanup(self.guard.stop)

    def test_combined_stale_price_missing_primary_and_queued_order(self):
        hist,held = market(),positions()
        del hist["AAA"][END]
        held[2]["queued"] = .10
        orders = [[0,0,"CCC",.15,.10,"Submitted — FT pending"]]
        original = copy.deepcopy((hist,held,orders))
        proposed = rotation.proposals(hist,held,orders,{},[],NOW)
        by_name = {row[0]:row for row in proposed}
        self.assertEqual(by_name["AAA"][3],"UNRESOLVED")
        self.assertEqual(by_name["BBB"][3],"HOLD")  # price-only, no completed issuer certificate
        self.assertEqual(by_name["CCC"][4],.10)
        self.assertIn("DO NOT RESUBMIT",by_name["CCC"][16])
        issuer = inputs.company_inputs("BBB",[],{},NOW)
        self.assertIsNone(issuer["score"])
        self.assertIsNone(finance.assess("BBB",{},by_name["BBB"],[],NOW)["total"])
        row = review()
        row[13],row[17],row[24] = "", "FAILED", "Issuer original unavailable; stale AAA; existing CCC order retained"
        self.assertFalse(health.checkpoint_complete(row,NOW))
        self.assertIn("EVIDENCE=FAILED",health.checkpoint_diagnostic(row))
        intake=[[row[0],"Partial",1,1,0,0,0,"Finalized",row[0]]]
        status=health.evaluate(NOW,intake,[row],[],{p["ticker"] for p in held},row[11])
        self.assertNotIn(status[1],{"SAVED","NEXT-DAY INPUTS"})
        late=health.evaluate(NOW.replace(minute=50),intake,[row],[],
                             {p["ticker"] for p in held},row[11])
        self.assertTrue(late[2])
        self.assertEqual(original,(hist,held,orders))

    def test_malformed_position_cannot_disappear_from_coverage(self):
        for value in ("15%",float("nan"),float("inf"),-.01,.26,True):
            with self.subTest(value=value),self.assertRaises(ValueError):
                rotation.parse_positions([["AAA","A",.15,.15],["BBB","B",value,.10]])

    def test_duplicate_ticker_cannot_be_certified(self):
        with self.assertRaises(ValueError):
            rotation.parse_positions([["AAA","A",.15,.15],["AAA","A",.10,.10]])

    def test_partial_execution_still_blocks_resubmission(self):
        orders = [[0,0,"AAA",.15,.10,"Partially executed — reconcile remaining order"]]
        self.assertIn("AAA",rotation.pending_orders(orders))
        out = rotation.proposals(market(),positions(),orders,{},[],NOW)
        self.assertIn("DO NOT RESUBMIT",out[0][16])

    def test_cancelled_and_executed_orders_do_not_create_false_queue(self):
        for status in ("Cancelled — user confirmed","Executed — user confirmed"):
            self.assertNotIn("AAA",rotation.pending_orders([[0,0,"AAA",.15,.15,status]]))

    def test_exchange_holiday_is_not_a_verified_session(self):
        holiday = date(2026,11,26)
        row = [prices.serial(holiday),"AAA","NYSE:AAA",100,"USD","US",
               "https://source.example",prices.serial(holiday)+.8,"VERIFIED",f"{holiday}|AAA"]
        hist,_,faults = rotation.load_archive([row])
        self.assertNotIn(holiday,hist.get("AAA",{}))
        self.assertTrue(faults)

    def test_every_interrupted_checkpoint_stays_incomplete(self):
        for completed in range(5):
            row=review()
            row[13]=""
            for _,tc,rc in health.CHECKPOINT_STAGES[completed:]:
                row[tc],row[rc]="",""
            before=copy.deepcopy(row)
            self.assertFalse(health.checkpoint_complete(row,NOW))
            health.checkpoint_diagnostic(row)
            self.assertEqual(before,row)

    def test_out_of_order_pass_stamps_are_not_completion(self):
        row=review()
        row[16],row[18]=row[18],row[16]
        self.assertFalse(health.checkpoint_complete(row,NOW))
        # The live formula must enforce the same ordered-stage contract.
        formula = next(q["updateCells"]["rows"][0]["values"][0]["userEnteredValue"]["formulaValue"]
                       for q in desk.readiness_requests(1)
                       if q["updateCells"]["start"]=={"sheetId":1,"rowIndex":26,"columnIndex":1})
        self.assertIn("ordered",formula)
        self.assertIn("complete,AND(ordered,",formula)

    def test_changed_holdings_fail_existing_proposal_receipt(self):
        held=positions()
        out=rotation.proposals(market(),held,[],{},[],NOW)
        stamp=prices.serial(NOW)
        receipt=[["ROTATION STATUS","COMPLETE"],["COMPLETED AT",stamp],
                 ["PRICE SESSION",prices.serial(END)]]
        live=[[p["ticker"],p["ticker"],p["held"],p["queued"]] for p in held]
        live[0][2]=.10
        faults=health.rotation_health(NOW,receipt,out,live,[[stamp,"LIVE","COMPLETE"]])
        self.assertTrue(any("AAA" in x for x in faults))

    def test_unsupported_candidate_never_becomes_ready(self):
        row=[""]*59
        row[0],row[3],row[18],row[19]="BBB","NYSE:BBB","YES","https://issuer.example"
        result=rotation.rank_candidates([row],market(),END,NOW)
        self.assertNotEqual(result[0]["state"],"READY FOR HUMAN REVIEW")
        self.assertIn("cash edge unqualified",result[0]["gaps"])

    def test_cash_and_minimum_names_hold_under_many_losses(self):
        for cash in (0,.05,.20,.25):
            hist=market()
            held=positions()
            for position in held:
                position["held"]=position["queued"]=(1-cash)/6
            for symbol in ["AAA","BBB","CCC"]:
                for i,day in enumerate(sorted(hist[symbol])[-11:]):
                    hist[symbol][day]=110-i*3
            out=rotation.proposals(hist,held,[],{},[],NOW)
            weights=[row[4] for row in out]
            self.assertGreaterEqual(sum(weights),.75-1e-8)
            self.assertGreaterEqual(sum(w>0 for w in weights),5)
            self.assertTrue(all(0<=w<=.25 for w in weights))

    def test_write_or_read_failure_cannot_certify_installation(self):
        class Book:
            id=1
            def worksheet(self,name):return self
            def batch_update(self,body):raise RuntimeError("synthetic write interruption")
        with self.assertRaisesRegex(RuntimeError,"write interruption"):
            desk.sync_readiness(Book())
        book=Book()
        book.batch_update=lambda body:None
        book.get=lambda *a,**k:[]
        with self.assertRaisesRegex(RuntimeError,"readback mismatch"):
            desk.sync_readiness(book)

    def test_rerun_is_idempotent_and_preserves_first_signal(self):
        a=dict(lag="SOXX",lead="XLV",d3=5,d5=7,lag3=-3,lead3=2,lag5=-5,lead5=2,breadth="unknown")
        first=rotation.warning_rows([a],[],END,NOW,[],rotation.DEFAULTS,"original")
        rerun=rotation.warning_rows([dict(a,d3=20)],first,END,NOW,[],rotation.DEFAULTS,"changed")
        self.assertEqual(first,rerun)

    def test_dst_holiday_and_game_end_execution(self):
        before=datetime(2026,10,30,14,35,tzinfo=prices.TORONTO)
        self.assertEqual(rotation.execution_window(before)[1],"2026-11-02T09:30:00-05:00")
        thanksgiving_eve=datetime(2026,11,25,14,35,tzinfo=prices.TORONTO)
        self.assertIn("2026-11-27T09:30",rotation.execution_window(thanksgiving_eve)[1])
        final=datetime(2026,11,27,14,35,tzinfo=prices.TORONTO)
        self.assertTrue(rotation.execution_window(final)[1].startswith("NONE"))

    def test_later_prices_cannot_rewrite_earlier_signals(self):
        hist=market()
        start=date(2026,10,1)
        before=replay.replay(hist,start,END)
        for series in hist.values():series[date(2026,10,20)]=1000000
        after=replay.replay(hist,start,END)
        self.assertEqual(before,after)

    def test_new_evidence_forces_recheck_without_mutating_saved_review(self):
        row=review()
        frozen=copy.deepcopy(row)
        intake=[[row[0],"Partial",1,1,0,0,0,"Finalized",row[0]]]
        snapshot=[row[0]+.001,row[9],"AAA",.15,.15]+[""]*5+["HOLD",100,"USD","SPY",100,"PRIMARY:issuer","","","VERIFIED PRIOR CLOSE"]
        out=health.evaluate(NOW,intake,[row],[snapshot],{"AAA"},row[11]+.001)
        self.assertEqual(out[1],"NEW INPUTS")
        self.assertEqual(row,frozen)

    def test_sensitivity_is_non_mutating_and_does_not_claim_returns(self):
        hist=market()
        row=review()[:]
        snapshot=[row[0],row[9],"AAA",.15,.15,.5,.4,0]+[""]*11
        original=copy.deepcopy((hist,[snapshot],rotation.DEFAULTS))
        result=replay.stress_diagnostics(hist,[snapshot],date(2026,10,1),END)
        self.assertEqual(original,(hist,[snapshot],rotation.DEFAULTS))
        self.assertIsNone(result["returns"])
        self.assertIsNone(result["drawdown"])
        base=next(x for x in result["threshold_sensitivity"] if x["factor"]==1)
        self.assertEqual(base["changed_sessions"],0)
        # Future history outside the end date cannot change any comparison.
        for series in hist.values():series[date(2026,10,20)]=999999
        self.assertEqual(result,replay.stress_diagnostics(hist,[snapshot],date(2026,10,1),END))

    def test_opposing_company_facts_are_not_overridden_by_a_price_rally(self):
        row=[""]*26
        for i,v in {0:"PRIMARY:SYNTHETIC",1:"BBB",2:prices.serial(NOW)-1,
                    4:"https://issuer.example",6:-1,7:3,9:"Valid",10:1,11:-1,
                    15:prices.serial(NOW)-.1,16:"Full"}.items():row[i]=v
        company=inputs.company_inputs("BBB",[row],{},NOW)
        self.assertLess(company["score"],0)
        favorable_price=rotation.proposals(market(),positions(),[],{},[],NOW)[1]
        self.assertEqual(favorable_price[3],"HOLD")
        # A price-only HOLD is not a positive company score or an action certificate.
        self.assertEqual(company["ids"],"PRIMARY:SYNTHETIC")
        row[9]="Unverified"
        self.assertIsNone(inputs.company_inputs("BBB",[row],{},NOW)["score"])


if __name__=="__main__":
    unittest.main()

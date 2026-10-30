import copy
from datetime import date, datetime, time
import unittest
from unittest.mock import Mock, patch

import ft_price_writer as prices
import ft_sector_rotation as s
import ft_pipeline_health as health
from ft_rotation_universe import SECTORS


def fixture(end=date(2026, 10, 15)):
    days = s.session_window(end, 35)
    return {t: {d: 100 + i for i, d in enumerate(days)} for t in ["SPY", "AMGN", "MSFT", "CRWV", *SECTORS]}


class SectorRotationTests(unittest.TestCase):
    def test_proposal_notes_follow_ticker_after_exit_and_separated_holdings(self):
        def proposal(ticker, reason):
            row = [""] * 17
            row[0], row[1], row[3], row[5], row[10] = ticker, .1, "HOLD", reason, "VERIFIED PRIOR CLOSE"
            return row
        rows = [["PSX — HOLD 5%"], ["MSFT — EXITED 0%"], ["CASH — 0%"], ["SYSTEM — CHECKED"], [], ["CME — HOLD 10%"]]
        result = s.proposal_note_updates(42, rows, [proposal("PSX", "PSX evidence"), proposal("CME", "CME evidence")])
        notes = {r["updateCells"]["range"]["startRowIndex"]: r["updateCells"]["rows"][0]["values"][0]["note"] for r in result}
        self.assertEqual(set(notes), {1, 2, 6})
        self.assertIn("A2051:Q2051", notes[1])
        self.assertIn("PSX evidence", notes[1])
        self.assertIn("No current held-position proposal", notes[2])
        self.assertIn("A2052:Q2052", notes[6])
        self.assertIn("CME evidence", notes[6])
        self.assertNotIn("PSX evidence", notes[6])

    def test_calendar_gap_is_missing_not_shorter_return(self):
        h=fixture(); end=date(2026,10,15)
        del h["AMGN"][date(2026,10,12)]
        self.assertIsNone(s.returns(h,"AMGN",end,5))

    def test_future_prices_cannot_change_signal(self):
        h=fixture();end=date(2026,10,15); before=s.group_metrics(h,end)
        for series in h.values():series[date(2026,10,16)]=999999
        self.assertEqual(before,s.group_metrics(h,end))

    def test_three_session_warning_does_not_wait_for_breadth(self):
        g={"SOXX":{"r3":-3,"r5":-2,"rs3":-2},"XLV":{"r3":1.1,"r5":1,"rs3":2,"breadth":{}}}
        a=s.detect_warnings(g)
        self.assertEqual(len(a),1);self.assertAlmostEqual(a[0]["d3"],4.1)

    def test_five_session_threshold_independent(self):
        g={"SOXX":{"r3":-1,"r5":-5,"rs3":-1},"XLV":{"r3":1,"r5":1.1,"rs3":1}}
        self.assertEqual(len(s.detect_warnings(g)),1)

    def test_first_warning_frozen_repeated_run_deduplicated(self):
        now=datetime(2026,10,16,10,tzinfo=prices.TORONTO)
        a={"lag":"SOXX","lead":"XLV","d3":4.1,"d5":3,"lag3":-3,"lead3":1.1,"lag5":-2,"lead5":1,"breadth":"unavailable"}
        rows=s.warning_rows([a],[],date(2026,10,15),now,[],s.DEFAULTS,"hash")
        later=s.warning_rows([{**a,"d3":8}],rows,date(2026,10,16),now,[],s.DEFAULTS,"newhash")
        self.assertEqual(len(later),1);self.assertEqual(rows[0][:14],later[0][:14])

    def test_failed_prices_never_silently_hold(self):
        now=datetime(2026,10,16,14,tzinfo=prices.TORONTO)
        rows=s.proposals({},[{"ticker":"MSFT","held":.15,"queued":.15}],[],{},[],now)
        self.assertEqual(rows[0][3],"UNRESOLVED");self.assertEqual(rows[0][10],"FAILED")

    def test_same_session_reactivation_preserves_distinct_episode(self):
        now=datetime(2026,10,16,10,tzinfo=prices.TORONTO)
        a={"lag":"SOXX","lead":"XLV","d3":4.1,"d5":3,"lag3":-3,"lead3":1.1,"lag5":-2,"lead5":1,"breadth":"unavailable"}
        first=s.warning_rows([a],[],date(2026,10,15),now,[],s.DEFAULTS,"hash")
        cleared=s.warning_rows([],first,date(2026,10,15),now,[],s.DEFAULTS,"hash")
        again=s.warning_rows([a],cleared,date(2026,10,15),now,[],s.DEFAULTS,"hash")
        self.assertEqual(len(again),2)
        self.assertEqual(again[0][13],"CLEARED")
        self.assertNotEqual(again[0][0],again[1][0])

    def test_pending_buy_prevents_duplicate_and_inferred_fill(self):
        now=datetime(2026,10,16,14,tzinfo=prices.TORONTO)
        p=[{"ticker":"MSFT","held":.15,"queued":.20},{"ticker":"AMGN","held":.05,"queued":.10}]
        orders=[[0,0,"MSFT",.15,.2,"Submitted — FT pending"],[0,0,"AMGN",.05,.1,"Submitted — FT pending"]]
        before=copy.deepcopy((p,orders));out=s.proposals(fixture(),p,orders,{},[],now)
        self.assertEqual(before,(p,orders));self.assertTrue(all(r[11].startswith("PENDING") for r in out))
        self.assertEqual(out[0][1],.15);self.assertEqual(out[0][4],.20)
        self.assertTrue(all("DO NOT RESUBMIT" in r[16] for r in out))

    def test_cash_trim_does_not_require_candidate(self):
        now=datetime(2026,10,16,14,tzinfo=prices.TORONTO);h=fixture()
        d=sorted(h["CRWV"])
        for i,day in enumerate(d[-11:]):h["CRWV"][day]=130-i*2
        positions=[{"ticker":"CRWV","held":.10,"queued":.10}]+[{"ticker":t,"held":.15,"queued":.15} for t in ("MSFT","AMGN","XLV","XLP","XLE")]
        out=s.proposals(h,positions,[],{},[],now)
        self.assertEqual(out[0][3],"ROTATE");self.assertEqual(out[0][4],0)
        self.assertIn("independently of replacements",out[0][5])

    def test_cash_limit_caps_trim_without_masking_risk(self):
        now=datetime(2026,10,16,14,tzinfo=prices.TORONTO);h=fixture()
        for i,day in enumerate(sorted(h["CRWV"])[-11:]):h["CRWV"][day]=130-i*2
        p=[{"ticker":"CRWV","held":.10,"queued":.10}]+[{"ticker":t,"held":.134,"queued":.134} for t in ("MSFT","AMGN","XLV","XLP","XLE")]
        out=s.proposals(h,p,[],{},[],now)
        self.assertAlmostEqual(out[0][4],.08);self.assertEqual(out[0][3],"TRIM")
        self.assertIn("RULE CONSTRAINT",out[0][15])

    def test_small_holding_trim_respects_minimum_without_unintended_exit(self):
        now=datetime(2026,10,16,14,tzinfo=prices.TORONTO);h=fixture()
        h["CRWV"][date(2026,10,15)]=h["CRWV"][date(2026,10,8)]*.95
        p=[{"ticker":"CRWV","held":.08,"queued":.08}]+[{"ticker":t,"held":.15,"queued":.15} for t in ("MSFT","AMGN","XLV","XLP","XLE")]
        out=s.proposals(h,p,[],{},[],now)
        self.assertEqual(out[0][3],"TRIM");self.assertEqual(out[0][4],.05)

    def test_missing_execution_dates_never_inferred(self):
        self.assertEqual(s.execution_window(datetime(2026,10,9,14,tzinfo=prices.TORONTO))[1],"2026-10-12T09:30:00-04:00")
        self.assertIn("2026-11-27T09:30",s.execution_window(datetime(2026,11,25,14,tzinfo=prices.TORONTO))[1])
        self.assertIn("after 27 Nov",s.execution_window(datetime(2026,11,27,12,tzinfo=prices.TORONTO))[1])

    def test_london_dst_is_separate(self):
        order,execution=s.execution_window(datetime(2026,10,23,14,tzinfo=prices.TORONTO),"LSE")
        self.assertEqual(execution,"2026-10-26T08:00:00+00:00")

    def test_failed_primary_uses_independent_dated_close(self):
        now=datetime(2026,10,16,10,tzinfo=prices.TORONTO)
        with patch.object(prices,"yahoo_history",return_value=([],"429")),patch.object(prices,"stooq_history",return_value=([],"unavailable")),patch.object(prices,"stockanalysis_bars",return_value=([(date(2026,10,15),100,101,99,100.5,"https://stockanalysis.com/stocks/msft/history/")],"")):
            *_,bars,end,err=prices.fetch_ticker("MSFT","NASDAQ:MSFT",now)
        self.assertEqual(bars[end][0],100.5);self.assertIn("429",err)

    def test_unverified_eligibility_cannot_be_ready(self):
        r=[""]*43;r[0]="AMGN";r[3]="NASDAQ:AMGN";r[18]="VERIFY"
        out=s.rank_candidates([r],fixture(),date(2026,10,15),datetime(2026,10,16,tzinfo=prices.TORONTO))
        self.assertTrue(out[0]["state"].startswith("RESERVE"))

    def test_duplicate_archive_row_quarantined(self):
        day=date(2026,10,15);r=[prices.serial(day),"MSFT","NASDAQ:MSFT",100,"USD","US","https://source.test",0,"VERIFIED",str(day)+"|MSFT"]
        h,_,errors=s.load_archive([r,r]);self.assertNotIn(day,h["MSFT"]);self.assertTrue(errors)

    def test_exactly_eleven_sectors(self):
        self.assertEqual(len(SECTORS),11)
        self.assertEqual(set(SECTORS),{"XLK","XLC","XLY","XLP","XLE","XLF","XLV","XLI","XLB","XLU","XLRE"})

    def test_london_afternoon_uses_common_completed_session(self):
        now=datetime(2026,10,16,14,5,tzinfo=prices.TORONTO)
        h=fixture(); h["BHP:LSE"]=dict(h["SPY"])
        h["BHP:LSE"][date(2026,10,16)]=150
        out=s.proposals(h,[{"ticker":"BHP:LSE","held":.1,"queued":.1}],[],{},[],now)
        self.assertEqual(out[0][10],"VERIFIED PRIOR CLOSE")
        self.assertEqual(out[0][7],prices.serial(date(2026,10,15)))

    def test_missing_valuation_cannot_be_ready(self):
        now=datetime(2026,10,16,tzinfo=prices.TORONTO)
        r=[""]*59
        for i,v in {0:"AMGN",3:"NASDAQ:AMGN",7:prices.serial(date(2026,11,3)),11:"risk",16:"Verified",17:prices.serial(now),18:"YES",19:"https://issuer.test",25:"Pass",38:"qualitative edge",39:"Positive"}.items():r[i]=v
        out=s.rank_candidates([r],fixture(),date(2026,10,15),now)
        self.assertIn("valuation review missing",out[0]["gaps"])
        self.assertIn("CASH ranks ahead",out[0]["cash_rank"])

    def test_candidate_compares_with_held_names_outside_candidate_pool(self):
        now=datetime(2026,10,16,tzinfo=prices.TORONTO);h=fixture()
        h["BHP:LSE"]={d:200-v for d,v in h["SPY"].items()}
        row=[""]*59;row[0]="AMGN";row[3]="NASDAQ:AMGN"
        out=s.rank_candidates([row],h,date(2026,10,15),now,positions=[{"ticker":"BHP:LSE","held":.1}])
        self.assertIn("BHP:LSE",out[0]["cash_rank"])

    def test_cash_comparison_excludes_executed_exits_from_weakest_incumbent(self):
        now=datetime(2026,10,29,22,35,tzinfo=prices.TORONTO)
        h=fixture(date(2026,10,29))
        h["PSX"]={d:200-i*3 for i,d in enumerate(sorted(h["SPY"]))}
        h["NVDA"]=dict(h["SPY"])
        row=[""]*59;row[0]="NVDA";row[3]="NASDAQ:NVDA";row[18]="YES"
        portfolio=[{"ticker":"PSX","held":0,"queued":0},
                   {"ticker":"CME","held":0,"queued":0},
                   {"ticker":"AMGN","held":.15,"queued":.15}]
        out=s.rank_candidates([row],h,date(2026,10,29),now,positions=portfolio)
        self.assertEqual(len(out),1)
        self.assertIn("weakest actual incumbent AMGN",out[0]["cash_rank"])
        self.assertNotIn("PSX",out[0]["cash_rank"])
        self.assertNotIn("CME",out[0]["cash_rank"])

    def test_delayed_job_cannot_backdate_on_time_delivery(self):
        now=datetime(2026,10,16,14,55,tzinfo=prices.TORONTO)
        stamp=prices.serial(now)
        receipt=[["ROTATION STATUS","COMPLETE"],["COMPLETED AT",stamp],["PRICE SESSION",prices.serial(date(2026,10,15))]]
        faults=health.rotation_health(now,receipt,[],[],[[stamp,"LIVE","COMPLETE"]])
        self.assertTrue(any("LATE" in f for f in faults))

    def test_early_verified_close_fallback_meets_rotation_deadline(self):
        now=datetime(2026,10,16,14,55,tzinfo=prices.TORONTO)
        stamp=prices.serial(now.replace(hour=8,minute=15))
        receipt=[["ROTATION STATUS","COMPLETE"],["COMPLETED AT",stamp],["PRICE SESSION",prices.serial(date(2026,10,15))]]
        self.assertEqual(health.rotation_health(now,receipt,[],[],[[stamp,"LIVE","COMPLETE"]]),[])


    def test_partial_primary_research_is_provisional_not_auto_buy(self):
        now=datetime(2026,10,16,tzinfo=prices.TORONTO)
        row=[""]*59
        row[0]="AMGN";row[3]="NASDAQ:AMGN";row[18]="YES"
        row[19]="https://www.sec.gov/issuer";row[16]="Verified"
        row[17]=prices.serial(now)
        # Missing valuation/catalyst/edge remains a caveat, not an invisible pick.
        out=s.rank_candidates([row],fixture(),date(2026,10,15),now)
        self.assertTrue(out[0]["state"].startswith("PROVISIONAL RESEARCH"))
        self.assertIn("valuation review missing",out[0]["gaps"])
        self.assertIn("CASH ranks ahead",out[0]["cash_rank"])


if __name__=="__main__":unittest.main()

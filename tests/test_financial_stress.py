"""Synthetic logic cases only: no claim of predictive or trading profitability."""
import copy
from datetime import datetime, timedelta
import json
import unittest

import ft_financial_stress as f
import ft_price_writer as p
import ft_sector_rotation as r

NOW=datetime(2026,10,20,13,50,tzinfo=p.TORONTO)


def evidence(**changes):
    d=dict(ticker="AAA",kind="fundamentals",profile="hyperscaler",unit="USD_millions",
           period_end="2026-09-30",period_months=3,ocf=100,capex=40,capex_basis="cash_PPE")
    d.update(changes)
    row=[""]*26
    for i,v in {1:"AAA",2:p.serial(NOW)-2,4:"https://issuer.example/results",9:"Context",15:p.serial(NOW)-1,
                16:"Data",20:"FIN:AAA:Q3",21:json.dumps(d),22:p.serial(NOW)-1,23:p.serial(NOW)+10,
                24:"hyperscaler; cash PPE; USDm",25:"Growth and liquidity counter distressed interpretation"}.items():row[i]=v
    return row


def signals():
    rows=[]
    for key in ("BAMLH0A0HYM2","BAMLC0A0CM","DFII10","NFCI"):
        row=[""]*15
        for i,v in {0:key,5:p.serial(NOW)-2,11:"Amber",12:p.serial(NOW)-1,13:"https://fred.stlouisfed.org/series/"+key}.items():row[i]=v
        rows.append(row)
    return rows


def proposal(t="AAA",weight=.15,weak=False):
    order,execution=r.execution_window(NOW)
    return [t,weight,weight,"HOLD",weight,"price-risk reduction justified; desired cut 5%" if weak else "retain exposure",
            p.serial(NOW),p.serial(NOW)-1,order,execution,"VERIFIED PRIOR CLOSE","CLEAR","XLK",-5 if weak else 2,-4 if weak else 1,"Within limits","HOLD"]


def assessment(material=False):
    row=evidence(financing_event="covenant_breach",event_verified=True) if material else evidence()
    a=f.assess("AAA",f.financial_inputs([row],NOW)[0],proposal(),signals(),NOW)
    return a


def positions(cash=.25):
    # Six synthetic holdings; no real allocation data in public repository.
    return [{"ticker":t,"held":w,"queued":w} for t,w in zip(["AAA","BBB","CCC","DDD","EEE","FFF"],[.15,.15,.15,.10,.10,.35-cash])]


class FinancialEvidenceTests(unittest.TestCase):
    def test_missing_cds_is_unknown_not_zero_or_renormalized(self):
        a=assessment()
        self.assertIsNone(a["total"])
        self.assertEqual(a["coverage"],.65)
        self.assertEqual(a["contributions"],dict(financing=None,sustainability=6,price=3.75,macro=4.5))
        self.assertTrue(a["cds"].startswith("UNKNOWN"))

    def test_verified_alternative_completes_without_pretending_to_be_cds(self):
        obs,_=f.financial_inputs([evidence(obligations_scope="all_material_12m_obligations",available_liquidity=150,obligations_12m=100)],NOW)
        a=f.assess("AAA",obs,proposal(),signals(),NOW)
        self.assertEqual(a["total"],28.2)
        self.assertEqual(a["coverage"],1)
        self.assertIn("ALTERNATIVE, not CDS",a["basis"])
        self.assertFalse(a["material"])

    def test_no_score_only_trade(self):
        a=assessment();a["total"]=95
        ps=positions();q=[proposal(x["ticker"],x["held"]) for x in ps]
        assessments={x[0]:dict(a,ticker=x[0]) for x in q}
        out=r.financial_review(q,assessments,[],ps,[],NOW)
        self.assertTrue(all(x[4]==x[1] for x in out))

    def test_expired_future_delayed_and_invalid_inputs_excluded(self):
        for idx,bad in [(23,p.serial(NOW)-.01),(22,p.serial(NOW)+.01),(15,p.serial(NOW)+1),(9,"Unverified"),(16,"Headline only"),(21,"{bad")]:
            row=evidence();row[idx]=bad
            observations,faults=f.financial_inputs([row],NOW)
            self.assertFalse(observations);self.assertTrue(faults)
        # Historical publication does not imply historical capture/verification.
        self.assertFalse(f.financial_inputs([evidence()],NOW-timedelta(days=3))[0])

    def test_duplicate_and_correlated_facts_not_counted_twice(self):
        row=evidence();other=copy.deepcopy(row);other[20]="reprint";other[4]="https://other.example/reprint"
        obs,faults=f.financial_inputs([row,other],NOW)
        self.assertEqual(len(obs),1);self.assertFalse(faults)
        self.assertEqual(f.assess("AAA",obs,proposal(),signals(),NOW)["coverage"],.65)
        # Multiple correlated credit warnings use maximum, not addition.
        d=dict(obligations_scope="all_material_12m_obligations",available_liquidity=90,obligations_12m=100,
               financing_event="covenant_breach",event_verified=True)
        self.assertEqual(f.financing(d,d)[0],95)

    def test_conflicting_same_period_facts_excluded(self):
        obs,faults=f.financial_inputs([evidence(),evidence(ocf=1)],NOW)
        self.assertFalse(obs);self.assertIn("conflicting",faults[0])

    def test_hedging_and_illiquid_cds_are_not_distress(self):
        credit=dict(cds_5y_bps=700,change_bps=200,quote_quality="verified",period_end="2026-10-19",independent_credit_confirmation=True)
        self.assertEqual(f.financing(None,credit)[0],85)
        for flag in ("hedging_only","thin_market"):
            self.assertIsNone(f.financing(None,dict(credit,**{flag:True}))[0])
        credit.pop("independent_credit_confirmation")
        self.assertIsNone(f.financing(None,credit)[0])

    def test_industry_and_comparable_basis(self):
        d=json.loads(evidence(profile="refiner")[21])
        self.assertIsNone(f.sustainability(d)[0])
        d.update(period_months=6);self.assertEqual(f.sustainability(d)[0],20)
        d=json.loads(evidence(ocf=100,capex=98,previous=dict(ocf=90,capex=45,period_months=3,capex_basis="cash_PPE"))[21])
        self.assertEqual(f.sustainability(d)[0],55)
        self.assertFalse(f.sustainability(d)[2]) # investment alone, still cash-positive
        d["previous"]["capex_basis"]="noncash_leases"
        self.assertEqual(f.sustainability(d)[0],45)

    def test_guidance_cut_and_adverse_price_are_independent_reasons(self):
        obs,_=f.financial_inputs([evidence(cash_guidance_cut_pct=25,guidance_basis="matched annual FCF midpoint, issuer release")],NOW)
        self.assertTrue(f.assess("AAA",obs,proposal(weak=True),signals(),NOW)["material"])
        self.assertFalse(f.assess("AAA",obs,proposal(),signals(),NOW)["material"])

    def test_new_sec_filing_invalidates_without_new_collector(self):
        obs,_=f.financial_inputs([evidence()],NOW)
        row=[p.serial(NOW)-.1,"AAA","Issuer","1","10-Q",p.serial(NOW)-1,"2026-10-18"]
        kept,gaps=f.exclude_superseded(obs,[row],NOW)
        self.assertFalse(kept);self.assertTrue(gaps)
        row[4]="4"
        self.assertEqual(f.exclude_superseded(obs,[row],NOW)[0],obs)

    def test_pharma_acquisition_cash_not_hidden_by_low_ppe(self):
        d=json.loads(evidence(profile="pharma",acquisition_cash=80,purchased_iprd_cash=10)[21])
        score,why,material=f.sustainability(d)
        self.assertEqual(score,45);self.assertFalse(material);self.assertIn("one-off",why)

    def test_replay_does_not_backdate_today_extraction(self):
        from ft_rotation_replay import financial_replay
        from test_sector_rotation import fixture
        old=NOW.replace(day=15)
        history=[[p.serial(old),"Daily:2026-10-15","MSFT",.15,.15]]
        result=financial_replay(fixture(),[evidence()],history,old.date(),old.date())
        self.assertEqual(result["cases_with_issuer_facts"],0)
        self.assertEqual(result["status"],"UNVALIDATED")
        self.assertIsNone(result["cases"][0]["qualified_replacement"])
        self.assertIsNone(result["cases"][0]["cash_minus_hold_5_pp"])

    def test_replay_prefers_frozen_finance_and_rejects_future_snapshot(self):
        from ft_rotation_replay import financial_replay
        from test_sector_rotation import fixture
        from datetime import date
        a=assessment(True);a["ticker"]="MSFT"
        row=[p.serial(NOW),"Daily:2026-10-20","MSFT",.15,.15]+[""]*14
        row[18]="VERIFIED PRIOR CLOSE 2026-10-19 FIN_JSON="+json.dumps(a)
        h=fixture(date(2026,10,19))
        out=financial_replay(h,[],[row],NOW.date(),NOW.date())
        self.assertEqual(out["cases"][0]["financing"],95)
        self.assertEqual(out["cases"][0]["historical_coverage"],"frozen verified FIN_JSON")
        a["asof"]+=1;row[18]="FIN_JSON="+json.dumps(a)
        out=financial_replay(h,[],[row],NOW.date(),NOW.date())
        self.assertEqual(out["cases_with_issuer_facts"],0)

    def test_stale_macro_does_not_stop_existing_price_severity(self):
        rows=signals();rows[0][12]=p.serial(NOW)-5
        a=f.assess("AAA",{},proposal(weak=True),rows,NOW)
        self.assertEqual(a["scores"]["price"],70)
        self.assertIsNone(a["scores"]["macro"])
        self.assertEqual(a["coverage"],.25)

    def test_health_checks_holdings_arithmetic_and_readback(self):
        a=assessment();q=proposal();rows=f.output_rows({"AAA":a},[q])
        receipt=[["FINANCING CALCULATION","VERIFIED"],["FINANCING AS OF",p.serial(NOW)]]
        self.assertFalse(f.health(NOW,receipt,rows,[("AAA",.15,.15)]))
        rows[0][5]=999
        self.assertIn("arithmetic",f.health(NOW,receipt,rows,[("AAA",.15,.15)])[0])
        self.assertTrue(f.health(NOW,receipt,rows,[("AAA",.20,.20)]))
        self.assertTrue(f.health(NOW,[["FINANCING CALCULATION","FAILED"]],rows,[("AAA",.15,.15)]))


class AllocationExamples(unittest.TestCase):
    def run_case(self,cash=.25,material=False,ready=False,weak=False,orders=()):
        ps=positions(cash);proposed=[proposal(x["ticker"],x["held"],weak and x["ticker"]=="AAA") for x in ps]
        assessments={x[0]:dict(assessment(material and x[0]=="AAA"),ticker=x[0]) for x in proposed}
        ranked=[dict(ticker="NEW",state="READY FOR HUMAN REVIEW",sector="XLV",cash="Verified valuation and catalyst offer positive cash edge",m=dict(r5=2,rs5=1))] if ready else []
        return r.financial_review(proposed,assessments,ranked,ps,orders,NOW),ps

    def test_independently_justified_hold(self):
        out,_=self.run_case()
        self.assertEqual(out[0][3:5],["HOLD",.15])

    def test_independently_justified_trim_to_cash(self):
        out,_=self.run_case(cash=.20,material=True)
        self.assertEqual(out[0][3:5],["TRIM",.10])
        self.assertAlmostEqual(1-sum(x[4] for x in out),.25)

    def test_independently_justified_paired_rotation(self):
        out,_=self.run_case(material=True,ready=True)
        self.assertEqual(out[0][3:5],["ROTATE",.10])
        self.assertEqual(out[-1][0],"NEW");self.assertEqual(out[-1][4],.05)
        self.assertAlmostEqual(1-sum(x[4] for x in out),.25)
        self.assertIn("SIMULTANEOUS",out[0][15])

    def test_cash_ceiling_blocks_standalone_trim(self):
        out,_=self.run_case(material=True)
        self.assertEqual(out[0][4],.15);self.assertIn("BLOCKED",out[0][15]);self.assertIn("NO TRADE",out[0][16])

    def test_existing_price_rule_operates_without_financing(self):
        out,_=self.run_case(cash=.20,weak=True)
        self.assertEqual(out[0][4],.10)

    def test_pending_order_no_duplicate(self):
        out,_=self.run_case(material=True,ready=True,orders=[["","","AAA","","","Submitted — FT pending"]])
        self.assertEqual(out[0][4],.15);self.assertEqual(len(out),6)

    def test_unheld_queued_buy_remains_in_proposed_cash_total(self):
        from test_sector_rotation import fixture
        when=NOW.replace(day=16)
        ps=[dict(ticker="MSFT",held=.15,queued=.15),dict(ticker="AMGN",held=0,queued=.05)]
        out=r.proposals(fixture(),ps,[["","","AMGN","","","Submitted — FT pending"]],{},[],when)
        self.assertEqual(len(out),2)
        self.assertEqual(out[1][4],.05)
        self.assertIn("DO NOT RESUBMIT",out[1][16])
        self.assertAlmostEqual(sum(x[4] for x in out),.20)

    def test_deadline_and_holidays(self):
        nov25=NOW.replace(month=11,day=25)
        self.assertIn("2026-11-27",r.execution_window(nov25)[1])
        self.assertTrue(r.execution_window(nov25.replace(day=27))[1].startswith("NONE"))


if __name__=="__main__":unittest.main()

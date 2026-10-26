import unittest
from datetime import datetime, date
import ft_decision_inputs as d
import ft_sector_rotation as r
import ft_price_writer as p
import ft_rotation_replay as replay


class DecisionInputsTest(unittest.TestCase):
    def setUp(self):
        self.now=datetime(2026,10,26,8,10,tzinfo=p.TORONTO)
        self.row=['ID','EOG',p.serial(date(2026,10,1)),'fact','https://issuer.test/q3',1,1,30,25,'Valid',1,1,'Issuer','Earnings','',p.serial(date(2026,10,2)),'Data']

    def test_valid_long_horizon_reaches_company_not_seven_day_cutoff(self):
        self.assertEqual(d.company_inputs('EOG',[self.row],{},self.now)['score'],.5)

    def test_missing_and_calendar_are_unknown_neutral_is_explicit(self):
        self.assertIsNone(d.company_inputs('EOG',[],{},self.now)['score'])
        self.row[6]=0;self.row[11]=0
        self.assertIsNone(d.company_inputs('EOG',[self.row],{},self.now)['score'])
        typed={('EOG','fundamentals'):{'company_direction':0,'company_rationale':'mixed verified facts','source':'https://issuer.test/q3','key':'typed'}}
        self.assertEqual(d.company_inputs('EOG',[],typed,self.now)['score'],0)

    def test_duplicates_macro_expiry_and_future_capture_excluded(self):
        other=list(self.row);other[4]+='?newsletter=abc'
        self.assertEqual(d.company_inputs('EOG',[self.row,other],{},self.now)['count'],1)
        for i,v in [(1,'Macro / EOG'),(1,'EOGX'),(7,7),(9,'Unverified'),(15,p.serial(date(2026,10,27)))]:
            row=list(self.row);row[i]=v
            self.assertIsNone(d.company_inputs('EOG',[row],{},self.now)['score'])

    def test_cash_conversion_duration_and_split_basis(self):
        obs={'profile':'retail','source':'https://issuer.test/q2','ocf':20,'capex':5,'capex_basis':'cash_PPE',
             'period_months':6,'period_end':'2026-07-31','income':{'revenue':100,'previous_revenue':80,'operating_income':10,'previous_operating_income':4,'net_income':5,'period_months':3,'period_end':'2026-07-31','basis':'GAAP'},
             'valuation':{'denominator':4,'basis':'forward adjusted EPS','share_basis_verified':False}}
        result=d.fundamentals(obs,100)
        self.assertEqual(result['revenue'],25);self.assertEqual(result['margin_change'],5)
        self.assertEqual(result['fcf'],15);self.assertIsNone(result['conversion']);self.assertIsNone(result['multiple'])
        obs['income']['period_months']=6;obs['valuation']['share_basis_verified']=True
        result=d.fundamentals(obs,100);self.assertEqual(result['conversion'],4);self.assertEqual(result['multiple'],25)
        obs['profile']='insurance';self.assertIsNone(d.fundamentals(obs,100)['fcf'])

    def test_sixty_sessions_optional_not_gate_for_existing_warning(self):
        days=r.session_window(date(2026,10,23),20)
        hist={'EOG':{day:100+i for i,day in enumerate(days)},'SPY':{day:100 for day in days}}
        m=r.metric(hist,'EOG',days[-1]);self.assertIsNotNone(m['score']);self.assertIsNone(m['r60'])

    def test_all_new_holdings_mapped_and_unknown_not_coerced_to_zero(self):
        req=d.formula_updates(1,[['OLD']],['EOG','PGR'],[])
        strings=str(req)
        self.assertIn('EOG',strings);self.assertIn('PGR',strings);self.assertIn('ISBLANK',strings)
        self.assertNotIn('B5:$B$28',strings)

    def test_replay_never_imputes_or_backdates(self):
        row=[p.serial(datetime(2026,10,7,18,tzinfo=p.TORONTO)),'Daily:2026-10-07','EOG',.05,.05,.5,.5,0,0,0,'HOLD',100,'USD','SPY',100,'ID','','','Late recovery; not verified official closes']
        missing=list(row);missing[7]=''
        result=replay.model_comparison([row,missing],date(2026,9,1),date(2026,10,23))
        self.assertEqual(result['complete_frozen_inputs'],1);self.assertEqual(result['comparable_prewindow_cases'],0)
        self.assertIsNone(result['simulated_returns']);self.assertEqual(result['missing_inputs']['macro'],1)
        self.assertEqual(result['cases'][0]['earliest_execution'][:10],'2026-10-09')


if __name__=='__main__':unittest.main()

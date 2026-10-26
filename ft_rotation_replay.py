"""Read-only, no-lookahead replay of RULE-015; never writes a workbook or order.

Usage: python ft_rotation_replay.py --archive private-closes.json
       --supplement private-extra-closes.json --ohlc private-ohlc.json
       --start 2026-09-30 --end 2026-10-15 --output private-replay.json

Input archives are DAILY CLOSES value arrays (or {values: rows}). OHLC bars
are {ticker: {bars: [[ISO date,open,high,low,close,source],...]}}. Historical
prices retrieved later reproduce what was observable, not proof a live feed
or FT eligibility check existed then. Output is diagnostic, not actual P&L.
"""
import argparse
from datetime import date, datetime, time, timedelta
import hashlib
import json
from pathlib import Path

import ft_sector_rotation as r
import ft_price_writer as p
import ft_financial_stress as f


def read_rows(path):
    data=json.loads(Path(path).read_text())
    return data.get("values",[]) if isinstance(data,dict) else data


def replay(hist,start,end,config=None):
    config=config or r.DEFAULTS
    baseline_day=r.prior_session(start)
    baseline={t:{d:v for d,v in series.items() if d<=baseline_day} for t,series in hist.items()}
    previous={(a["lag"],a["lead"]) for a in r.detect_warnings(r.group_metrics(baseline,baseline_day),config)}
    episodes=[];daily=[]
    days=[d for d in sorted(hist.get("SPY",{})) if start<=d<=end]
    for day in days:
        # Physically remove all later prices before any detection calculation.
        known={t:{d:v for d,v in series.items() if d<=day} for t,series in hist.items()}
        metrics=r.group_metrics(known,day)
        warnings=r.detect_warnings(metrics,config)
        pairs={(a["lag"],a["lead"]) for a in warnings}
        decision_day=r.next_session(day)
        decision=datetime.combine(decision_day,time(14,45),p.TORONTO)
        order,execution=r.execution_window(decision)
        for a in warnings:
            if (a["lag"],a["lead"]) not in previous:
                episodes.append({"signal_session":str(day),"first_decision":decision.isoformat(),
                                 "order_window":order,"earliest_execution":execution,**a})
        def spread(n):
            a,b=metrics["XLV"][f"r{n}"],metrics["SOXX"][f"r{n}"]
            return a-b if a is not None and b is not None else None
        daily.append({"session":str(day),"warning_pairs":len(warnings),
                      "new_episodes":len(pairs-previous),"metrics":metrics,
                      "healthcare_semiconductor_3d_pp":spread(3),
                      "healthcare_semiconductor_5d_pp":spread(5)})
        previous=pairs
    # Outcome scoring is explicitly AFTER signal creation. Never feeds back
    # into thresholds, rankings, timing, eligibility or simulated decisions.
    for episode in episodes:
        day=date.fromisoformat(episode["signal_session"])
        for n in (1,5,10):
            outcome_day=day
            for _ in range(n):outcome_day=r.next_session(outcome_day)
            outcome=None
            if outcome_day<=end:
                lead,lag=hist.get(episode["lead"],{}),hist.get(episode["lag"],{})
                if all(day in x and outcome_day in x for x in (lead,lag)):
                    outcome=100*(lead[outcome_day]/lead[day]-lag[outcome_day]/lag[day])
            episode[f"forward_{n}d_relative_pp"]=outcome
            episode[f"false_alarm_{n}d"]=outcome<=0 if outcome is not None else None
    scores={}
    for n in (1,5,10):
        matured=[e for e in episodes if e[f"false_alarm_{n}d"] is not None]
        scores[str(n)]={"matured":len(matured),"false":sum(e[f"false_alarm_{n}d"] for e in matured),
                        "censored":len(episodes)-len(matured)}
    return {"method":"Prices truncated at each close; initial 4pp/6pp thresholds, no optimization; fixed breadth samples constructed16Oct and not used as alert gate.",
            "outcome_definition":"False alarm = leading sector fails to outperform lagging group over next1/5/10 closes. Correlated pair episodes, not independent trades; incomplete horizons censored.",
            "daily":daily,"episodes":episodes,"false_alarm_counts":scores}


def financial_replay(hist,evidence,history,start,end):
    """Replay only frozen holding snapshots and contemporaneously verified facts.

    Current holdings, today's Signals and today's replacement ranking are never
    substituted for historical inputs. Outcomes are public-close diagnostics,
    not FT executions, portfolio P&L or a tradable counterfactual.
    """
    cases=[]
    for row in history:
        if not row or not f.number(row[0]) or not start<=p.day_of(row[0])<=end or not f.number(f.value(row,3)) or row[3]<=0:
            continue
        when=(p.EPOCH+timedelta(days=row[0])).replace(tzinfo=p.TORONTO)
        day=p.last_completed(when,*p.venue_info("SPY","")[2:])
        known={t:{d:v for d,v in series.items() if d<=day} for t,series in hist.items()}
        obs,faults=f.financial_inputs(evidence,when)
        t=row[2];data=obs.get((t,"fundamentals"));credit=obs.get((t,"credit"))
        fs,_,confirmed,_=f.financing(data,credit);ss,_,deteriorating=f.sustainability(data)
        metrics=r.group_metrics(known,day);alerts=r.detect_warnings(metrics)
        position={"ticker":t,"held":row[3],"queued":f.value(row,4,row[3])}
        prop=r.proposals(known,[position],[],metrics,alerts,when)[0]
        ps=f.price_severity(prop)
        material=confirmed or (deteriorating and ps is not None and ps>=45)
        # Future daily receipts can freeze a qualified destination in their
        # FIN_JSON suffix. Absent that evidence, no retrospective replacement.
        destination=None;frozen=False
        marker="FIN_JSON="
        if marker in str(f.value(row,18)):
            try:
                snapshot=json.loads(str(row[18]).split(marker,1)[1])
                scores=snapshot.get("scores",{})
                valid=(snapshot.get("ticker")==t and f.number(snapshot.get("asof"))
                       and int(row[0])<=snapshot["asof"]<=row[0] and snapshot.get("hash")
                       and set(scores)==set(f.WEIGHTS)
                       and all(v is None or f.number(v) and 0<=v<=100 for v in scores.values()))
                if valid:
                    # Frozen verified assessment survives later source expiry
                    # or revisions; never substitute today's rewritten facts.
                    fs=scores["financing"];ss=scores["sustainability"]
                    material=bool(snapshot.get("material"));frozen=True
                    candidate=snapshot.get("qualified_replacement")
                    if candidate and candidate.get("all_gates_passed") and candidate.get("verified_at",float('inf'))<=row[0]:
                        destination=candidate["ticker"]
            except (ValueError,KeyError,TypeError):pass
        case={"ticker":t,"decision_captured":when.isoformat(),"price_session":str(day),
              "financing":fs,"sustainability":ss,"price":ps,"financial_material":material,
              "added_to_price_warning":bool(material and (ps is None or ps<70)),
              "qualified_replacement":destination,"historical_coverage":"frozen verified FIN_JSON" if frozen else "verified issuer facts" if data or credit else "UNAVAILABLE; do not backdate today's extraction"}
        for n in (5,10):
            future=day
            for _ in range(n):future=r.next_session(future,prices_venue(t))
            series=hist.get(t,{})
            ret=100*(series[future]/series[day]-1) if future<=end and day in series and future in series else None
            replacement=hist.get(destination,{}) if destination else {}
            rot=100*(replacement[future]/replacement[day]-1)-ret if ret is not None and day in replacement and future in replacement else None
            case[f"hold_{n}_close_return_pct"]=ret
            case[f"cash_minus_hold_{n}_pp"]=-ret if material and ret is not None else None
            case[f"replacement_minus_hold_{n}_pp"]=rot if material else None
            case[f"false_alarm_{n}"]=ret>=0 if material and ret is not None else None
            case[f"missed_warning_{n}"]=ret<=r.DEFAULTS["trim_5d_pct"] if (data or frozen) and not material and ret is not None else None
        cases.append(case)
    return {"status":"UNVALIDATED", "method":"Captured/verified/publication/expiry gates; frozen holdings only; unchanged thresholds; 5/10 public-close diagnostics, never FT execution prices.",
            "limitations":"No historical CDS or typed financing records means no test of incremental predictive value. Missing point-in-time replacement qualification prohibits hindsight selection. Cash advantage is unscaled and is not an executable trim or portfolio return.",
            "cases":cases,"cases_with_issuer_facts":sum(c["financing"] is not None or c["sustainability"] is not None for c in cases),
            "incremental_financial_warnings":sum(c["added_to_price_warning"] for c in cases)}


def prices_venue(ticker):
    return p.venue_info(ticker,"")[0]


def model_comparison(history,start,end):
    """Frozen-input sensitivity; do not fabricate executable historical trades.

    Missing inputs are excluded. Late recovery captures remain late, even if
    their key says Daily. Today's facts/holdings cannot fill historical blanks.
    FT next-open restrictions are reported for each original capture. Price
    data without timestamp provenance cannot establish executable performance.
    """
    cases=[];missing={k:0 for k in ('company','price','macro')};total=0
    def action(c,price,score):
        if price<=-.65 or score<=-.35 and c<=0:return 'REVIEW TRIM'
        if score>=.35 and c>=0:return 'REVIEW ADD'
        return 'HOLD'
    for row in history:
        if not f.number(f.value(row,0)) or not start<=p.day_of(row[0])<=end:continue
        total+=1
        inputs=[f.value(row,i) for i in (5,6,7)]
        good=True
        for name,x in zip(missing,inputs):
            if not f.number(x) or not -1<=x<=1:missing[name]+=1;good=False
        if not good:continue
        when=(p.EPOCH+timedelta(days=row[0])).replace(tzinfo=p.TORONTO)
        company,price,macro=inputs
        base=.45*company+.35*price+.20*macro
        alt=.50*company+.40*price+.10*macro
        coverage=str(f.value(row,18))
        verified=('VERIFIED PRIOR CLOSE' in coverage and not any(x in coverage.lower() for x in ('not verified','unverified price','missing15:15','late recovery','late recovery')))
        prewindow=time(14,20)<=when.time()<=time(14,45)
        order,execution=r.execution_window(when,prices_venue(row[2]))
        cases.append({'ticker':row[2],'capture':when.isoformat(),'key':row[1],
            'held':row[3],'company':company,'price':price,'macro':macro,
            'base':base,'alternative':alt,'base_band':action(company,price,base),
            'alternative_band':action(company,price,alt),'verified_price_provenance':verified,
            'within_review_window':prewindow,'earliest_order':order,'earliest_execution':execution})
    return {'status':'INCONCLUSIVE — production 45/35/20 retained','history_rows':total,
        'complete_frozen_inputs':len(cases),'missing_inputs':missing,
        'comparable_prewindow_cases':sum(c['within_review_window'] and c['verified_price_provenance'] for c in cases),
        'threshold_changes':sum(c['base_band']!=c['alternative_band'] for c in cases),
        'cases':cases,'simulated_returns':None,'simulated_drawdown':None,
        'false_positive_trade_rate':None,'earlier_deterioration_or_rotation_detection':None,
        'limitations':'Sparse late/recovery inputs; no complete contemporaneous portfolio decisions, verified historical order eligibility, opening fills/costs or qualified replacements. No performance or statistical-significance claim. Sector price-warning replay is separate from the macro-weight test.'}


def illustrative_returns(ohlc,entry,end):
    result={}
    for t,obj in ohlc.items():
        bars={b[0]:b for b in obj["bars"]}
        if str(entry) in bars and str(end) in bars:
            start,finish=bars[str(entry)],bars[str(end)]
            result[t]={"entry_open":start[1],"end_close":finish[4],
                       "return_pct":100*(finish[4]/start[1]-1),
                       "source":start[5],"entry":str(entry),"end":str(end)}
    return result


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--archive",required=True);ap.add_argument("--supplement")
    ap.add_argument("--ohlc");ap.add_argument("--start",default="2026-09-30")
    ap.add_argument("--end",default="2026-10-15");ap.add_argument("--output",required=True)
    ap.add_argument("--financial-evidence");ap.add_argument("--decision-history")
    args=ap.parse_args();rows=read_rows(args.archive)
    if args.supplement:rows+=read_rows(args.supplement)
    hist,_,errors=r.load_archive(rows)
    if errors:raise ValueError("Archive validation: "+"; ".join(errors[:5]))
    end=date.fromisoformat(args.end)
    result=replay(hist,date.fromisoformat(args.start),end)
    result["input_sha256"]=hashlib.sha256(json.dumps(rows,separators=(",",":")).encode()).hexdigest()
    result["archive_errors"]=errors
    if args.financial_evidence:
        if not args.decision_history:raise ValueError("Financial replay requires frozen Decision History; never use today's holdings")
        result["financial_stress"]=financial_replay(hist,read_rows(args.financial_evidence),read_rows(args.decision_history),date.fromisoformat(args.start),end)
    if args.decision_history:
        result['model_comparison']=model_comparison(read_rows(args.decision_history),date.fromisoformat(args.start),end)
    if args.ohlc:
        result["illustrative_oct12_open_to_end"]=illustrative_returns(json.loads(Path(args.ohlc).read_text()),date(2026,10,12),end)
    Path(args.output).write_text(json.dumps(result,indent=2))
    healthcare=[e for e in result["episodes"] if e["lag"]=="SOXX" and e["lead"]=="XLV"]
    print(json.dumps({"days":len(result["daily"]),"episodes":len(result["episodes"]),"healthcare_semiconductor_first":healthcare[0] if healthcare else None,"false_alarm_counts":result["false_alarm_counts"],"output":args.output}))


if __name__=="__main__":main()


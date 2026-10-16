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
from datetime import date, datetime, time
import hashlib
import json
from pathlib import Path

import ft_sector_rotation as r
import ft_price_writer as p


def read_rows(path):
    data=json.loads(Path(path).read_text())
    return data.get("values",[]) if isinstance(data,dict) else data


def replay(hist,start,end,config=None):
    config=config or r.DEFAULTS
    previous=set();episodes=[];daily=[]
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
        for n in (1,5):
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
    for n in (1,5):
        matured=[e for e in episodes if e[f"false_alarm_{n}d"] is not None]
        scores[str(n)]={"matured":len(matured),"false":sum(e[f"false_alarm_{n}d"] for e in matured),
                        "censored":len(episodes)-len(matured)}
    return {"method":"Prices truncated at each close; initial 4pp/6pp thresholds, no optimization; fixed breadth samples constructed16Oct and not used as alert gate.",
            "outcome_definition":"False alarm = leading sector fails to outperform lagging group over next1/5 closes. Correlated pair episodes, not independent trades; incomplete horizons censored.",
            "daily":daily,"episodes":episodes,"false_alarm_counts":scores}


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
    args=ap.parse_args();rows=read_rows(args.archive)
    if args.supplement:rows+=read_rows(args.supplement)
    hist,_,errors=r.load_archive(rows)
    if errors:raise ValueError("Archive validation: "+"; ".join(errors[:5]))
    end=date.fromisoformat(args.end)
    result=replay(hist,date.fromisoformat(args.start),end)
    result["input_sha256"]=hashlib.sha256(json.dumps(rows,separators=(",",":")).encode()).hexdigest()
    result["archive_errors"]=errors
    if args.ohlc:
        result["illustrative_oct12_open_to_end"]=illustrative_returns(json.loads(Path(args.ohlc).read_text()),date(2026,10,12),end)
    Path(args.output).write_text(json.dumps(result,indent=2))
    healthcare=[e for e in result["episodes"] if e["lag"]=="SOXX" and e["lead"]=="XLV"]
    print(json.dumps({"days":len(result["daily"]),"episodes":len(result["episodes"]),"healthcare_semiconductor_first":healthcare[0] if healthcare else None,"false_alarm_counts":result["false_alarm_counts"],"output":args.output}))


if __name__=="__main__":main()

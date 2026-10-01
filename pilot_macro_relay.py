from typing import Any
import ft_macro_pipeline as p
from ft_macro_coordinator import parse_date

PILOT_ID = "1NYgsQV7hZSmjjquQoyaP5ufNh3Rs_wDLjgf_TzSjpMc"
EIA = {
    "NG.NW2_EPG0_SWO_R48_BCF.W": ("US natural gas storage", "BCF", "Weekly"),
    "PET.WCESTUS1.W": ("US crude oil stocks", "Thousand barrels", "Weekly"),
    "NG.RNGWHHD.D": ("Henry Hub natural gas spot price", "$/MMBTU", "Daily"),
}

def book():
    return p.google_client().open_by_key(PILOT_ID)

def latest(rows, id_col, id_value, date_col, value_col, n=5):
    found = {}
    for row in rows:
        if len(row) <= max(id_col, date_col, value_col) or str(row[id_col]).strip() != id_value:
            continue
        d, v = row[date_col], str(row[value_col]).strip()
        if d and v and v != ".":
            found[parse_date(d).date().isoformat()] = row
    return [found[d] for d in sorted(found)[-n:]]

def fred_rows(captured: str) -> tuple[list[list[Any]], list[str]]:
    out, errors = [], []
    try:
        ws = book().worksheet("FRED Raw")
        start = max(2, ws.row_count - 15000)
        raw = ws.get(f"A{start}:H{ws.row_count}", value_render_option="UNFORMATTED_VALUE")
        for sid, (label, units0, freq0) in p.FRED_SERIES.items():
            obs = latest(raw, 1, sid, 3, 4)
            if not obs:
                errors.append(f"FRED relay {sid}: no observations")
            for r in obs:
                d, v = parse_date(r[3]).date().isoformat(), str(r[4])
                retrieved = parse_date(r[0]).isoformat()
                units = str(r[5]) if len(r) > 5 and r[5] else units0
                freq = str(r[6]) if len(r) > 6 and r[6] else freq0
                out.append([retrieved,"FRED",sid,label,d,v,units,freq,"Observation","US macro/financial conditions",f"https://fred.stlouisfed.org/series/{sid}",f"FRED:{sid}:{d}:{v}",captured[:10],"Official source via pilot collector",f"Pilot relay; source retrieved {retrieved}; relayed {captured}"])
    except Exception as e:
        errors.append(f"FRED pilot relay failed: {type(e).__name__}")
    return out, errors

def eia_rows(captured: str) -> tuple[list[list[Any]], list[str], int]:
    out, errors = [], []
    try:
        ws = book().worksheet("EIA Raw")
        raw = ws.get(f"A2:K{ws.row_count}", value_render_option="UNFORMATTED_VALUE")
        for sid, (label, units0, freq0) in EIA.items():
            obs = latest(raw, 1, sid, 3, 4)
            if not obs:
                errors.append(f"EIA relay {sid}: no observations")
            for r in obs:
                d, v = parse_date(r[3]).date().isoformat(), str(r[4])
                retrieved = parse_date(r[0]).isoformat()
                units = str(r[5]) if len(r) > 5 and r[5] else units0
                freq = str(r[6]) if len(r) > 6 and r[6] else freq0
                url = str(r[9]) if len(r) > 9 and r[9] else "https://www.eia.gov/opendata/browser/"
                out.append([retrieved,"EIA",sid,label,d,v,units,freq,"Observation","US energy",url,f"EIA:{sid}:{d}:{v}",captured[:10],"Official source via pilot collector",f"Pilot relay; source retrieved {retrieved}; relayed {captured}; electricity excluded"])
    except Exception as e:
        errors.append(f"EIA pilot relay failed: {type(e).__name__}")
    return out, errors, len(EIA)

_orig = p._number
def number_alias(item: dict[str, Any], *keys: str) -> float:
    k = []
    for x in keys:
        k.append(x)
        if x == "lev_money_positions_long_all": k.append("lev_money_positions_long")
        if x == "lev_money_positions_short_all": k.append("lev_money_positions_short")
    return _orig(item, *list(dict.fromkeys(k)))

p.fred_rows = fred_rows
p.eia_rows = eia_rows
p._number = number_alias

def main():
    # All entry points share the coordinated writer.
    from ft_macro_coordinator import main as run
    return run()

if __name__ == "__main__":
    raise SystemExit(main())

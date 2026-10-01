"""Compatibility runner for FT Game macro monitor.

Patches two source-specific issues without changing the main collector's sheet/write logic:
1) batch FRED retrieval over a bounded date range to avoid six slow sequential downloads;
2) current CFTC TFF leveraged-money field aliases.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta
from typing import Any

import requests

import ft_macro_pipeline as pipeline


def fred_rows(captured: str) -> tuple[list[list[Any]], list[str]]:
    rows: list[list[Any]] = []
    try:
        end = datetime.fromisoformat(captured).date()
        start = end - timedelta(days=900)  # comfortably covers five quarterly observations
        response = requests.get(
            "https://fred.stlouisfed.org/graph/fredgraph.csv",
            params={
                "id": ",".join(pipeline.FRED_SERIES.keys()),
                "cosd": start.isoformat(),
                "coed": end.isoformat(),
            },
            timeout=90,
            headers={"User-Agent": "FT Game Intelligence Macro Monitor/1.1"},
        )
        response.raise_for_status()
        parsed = list(csv.DictReader(io.StringIO(response.text)))

        for series_id, (label, units, frequency) in pipeline.FRED_SERIES.items():
            observations = [
                item for item in parsed if item.get(series_id) not in (None, "", ".")
            ][-5:]
            if not observations:
                raise ValueError(f"no observations returned for {series_id}")
            for item in observations:
                date = item.get("observation_date") or item.get("DATE") or ""
                value = item.get(series_id, "")
                key = f"FRED:{series_id}:{date}:{value}"
                rows.append([
                    captured,
                    "FRED",
                    series_id,
                    label,
                    date,
                    value,
                    units,
                    frequency,
                    "Observation",
                    "US macro/financial conditions",
                    f"https://fred.stlouisfed.org/series/{series_id}",
                    key,
                    captured[:10],
                    "Official source",
                    "Context only; review the series and current market regime before using in Evidence.",
                ])
        return rows, []
    except Exception as exc:  # noqa: BLE001
        return [], [f"FRED batch: {type(exc).__name__}: {exc}"]


_original_number = pipeline._number


def number_with_tff_aliases(item: dict[str, Any], *keys: str) -> float:
    expanded: list[str] = []
    for key in keys:
        expanded.append(key)
        if key == "lev_money_positions_long_all":
            expanded.extend(["lev_money_positions_long", "lev_money_positions_long_all"])
        elif key == "lev_money_positions_short_all":
            expanded.extend(["lev_money_positions_short", "lev_money_positions_short_all"])
    # preserve order while removing duplicates
    deduped = list(dict.fromkeys(expanded))
    return _original_number(item, *deduped)


pipeline.fred_rows = fred_rows
pipeline._number = number_with_tff_aliases


if __name__ == "__main__":
    pipeline.main()

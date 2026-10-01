"""Compatibility runner for FT Game macro monitor.

Uses the official FRED API when FRED_API_KEY is configured and current CFTC TFF
leveraged-money field aliases, while preserving the main collector's sheet/write logic.
"""

from __future__ import annotations

import os
from typing import Any

import requests

import ft_macro_pipeline as pipeline


def fred_rows(captured: str) -> tuple[list[list[Any]], list[str]]:
    api_key = os.environ.get("FRED_API_KEY", "").strip()
    if not api_key:
        return [], ["FRED: missing FRED_API_KEY secret"]

    rows: list[list[Any]] = []
    errors: list[str] = []
    for series_id, (label, units, frequency) in pipeline.FRED_SERIES.items():
        try:
            response = requests.get(
                "https://api.stlouisfed.org/fred/series/observations",
                params={
                    "series_id": series_id,
                    "api_key": api_key,
                    "file_type": "json",
                    "sort_order": "desc",
                    "limit": 5,
                },
                timeout=30,
                headers={"User-Agent": "FT Game Intelligence Macro Monitor/1.2"},
            )
            response.raise_for_status()
            payload = response.json()
            observations = [
                item for item in payload.get("observations", [])
                if item.get("value") not in (None, "", ".")
            ]
            if not observations:
                raise ValueError("no observations returned")
            observations.reverse()
            for item in observations:
                date = str(item.get("date", ""))
                value = item.get("value", "")
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
        except Exception as exc:  # noqa: BLE001
            errors.append(f"FRED {series_id}: {type(exc).__name__}: {exc}")
    return rows, errors


_original_number = pipeline._number


def number_with_tff_aliases(item: dict[str, Any], *keys: str) -> float:
    expanded: list[str] = []
    for key in keys:
        expanded.append(key)
        if key == "lev_money_positions_long_all":
            expanded.append("lev_money_positions_long")
        elif key == "lev_money_positions_short_all":
            expanded.append("lev_money_positions_short")
    deduped = list(dict.fromkeys(expanded))
    return _original_number(item, *deduped)


pipeline.fred_rows = fred_rows
pipeline._number = number_with_tff_aliases


if __name__ == "__main__":
    pipeline.main()

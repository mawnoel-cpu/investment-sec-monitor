"""Coordinated FT Game macro snapshot writer.

Prepared for deployment only. This module is intended to become the sole automated
writer to the FT Game ``Macro Feed`` snapshot area (A8:O250). The pilot collectors
remain upstream raw-data producers only.

Safety properties:
- respects the FT Game end date;
- refreshes each source independently;
- preserves the last-good rows for a source when that source returns no usable rows;
- reports Complete / Partial / Failed consistently;
- emits source-level diagnostics and freshness in the workflow log;
- uses a single bounded rewrite to avoid overlapping source writers.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import ft_macro_pipeline as p
import pilot_macro_relay as relay

SNAPSHOT_RANGE = "A8:O250"
LOG_START_ROW = 8
LOG_COLUMNS = "R:X"
SOURCE_ORDER = ("FRED", "EIA", "CFTC")


def _existing_rows(ws: Any) -> list[list[Any]]:
    rows = ws.get(SNAPSHOT_RANGE)
    return [row for row in rows if len(row) >= 2 and str(row[1]).strip()]


def _rows_by_source(rows: list[list[Any]]) -> dict[str, list[list[Any]]]:
    grouped = {source: [] for source in SOURCE_ORDER}
    for row in rows:
        source = str(row[1]).strip() if len(row) > 1 else ""
        if source in grouped:
            padded = list(row) + [""] * max(0, 15 - len(row))
            grouped[source].append(padded[:15])
    return grouped


def _source_age_hours(rows: list[list[Any]], now: datetime) -> float | None:
    timestamps: list[datetime] = []
    for row in rows:
        if not row:
            continue
        raw = str(row[0]).strip()
        if not raw:
            continue
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=ZoneInfo("America/Toronto"))
            timestamps.append(parsed.astimezone(ZoneInfo("America/Toronto")))
        except ValueError:
            continue
    if not timestamps:
        return None
    return round(max(0.0, (now - max(timestamps)).total_seconds() / 3600), 1)


def _append_detailed_run_log(
    ws: Any,
    *,
    now: datetime,
    status: str,
    series_checked: int,
    rows_written: int,
    preserved_sources: list[str],
    errors: list[str],
    freshness: dict[str, float | None],
) -> None:
    row = p.first_empty_log_row(ws)
    ws.update(
        range_name=f"R{row}:X{row}",
        values=[[
            now.isoformat(),
            "FRED / EIA / CFTC coordinator",
            status,
            series_checked,
            rows_written,
            ", ".join(preserved_sources) if preserved_sources else "None",
            json.dumps({"errors": errors, "age_hours": freshness}, ensure_ascii=False),
        ]],
        value_input_option="USER_ENTERED",
    )


def main() -> None:
    now = datetime.now(ZoneInfo("America/Toronto"))
    if now > p.GAME_END:
        print(json.dumps({"status": "Stopped", "reason": "FT Game ended"}))
        return

    ws = p.google_client().open_by_key(p.SPREADSHEET_ID).worksheet(p.MACRO_SHEET)
    captured = now.isoformat()
    existing = _rows_by_source(_existing_rows(ws))

    fred, fred_errors = relay.fred_rows(captured)
    eia, eia_errors, eia_checked = relay.eia_rows(captured)
    cftc, cftc_errors = p.cftc_rows(captured)

    results = {
        "FRED": (fred, fred_errors),
        "EIA": (eia, eia_errors),
        "CFTC": (cftc, cftc_errors),
    }

    final_rows: list[list[Any]] = []
    preserved_sources: list[str] = []
    refreshed_sources: list[str] = []
    all_errors: list[str] = []

    for source in SOURCE_ORDER:
        fresh_rows, errors = results[source]
        all_errors.extend(errors)
        if fresh_rows:
            final_rows.extend(fresh_rows)
            refreshed_sources.append(source)
        elif existing[source]:
            final_rows.extend(existing[source])
            preserved_sources.append(source)
            all_errors.append(f"{source}: no fresh rows; preserved last-good snapshot")
        else:
            all_errors.append(f"{source}: no fresh rows and no prior snapshot available")

    if not refreshed_sources:
        status = "Failed"
    elif all_errors:
        status = "Partial"
    else:
        status = "Complete"

    # One writer, one bounded rewrite. Sources with no usable refresh retain their
    # last-good rows rather than disappearing from decision support.
    if final_rows:
        ws.batch_clear([SNAPSHOT_RANGE])
        end_row = 7 + len(final_rows)
        ws.update(
            range_name=f"A8:O{end_row}",
            values=final_rows,
            value_input_option="USER_ENTERED",
        )

    grouped_final = _rows_by_source(final_rows)
    freshness = {
        source: _source_age_hours(grouped_final[source], now)
        for source in SOURCE_ORDER
    }
    checked = len(p.FRED_SERIES) + eia_checked + len(p.CFTC_COMMODITIES) + len(p.CFTC_TFF)

    _append_detailed_run_log(
        ws,
        now=now,
        status=status,
        series_checked=checked,
        rows_written=len(final_rows),
        preserved_sources=preserved_sources,
        errors=all_errors,
        freshness=freshness,
    )

    print(json.dumps({
        "status": status,
        "series_checked": checked,
        "rows_written": len(final_rows),
        "refreshed_sources": refreshed_sources,
        "preserved_sources": preserved_sources,
        "age_hours": freshness,
        "errors": all_errors,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()

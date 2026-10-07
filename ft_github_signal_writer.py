"""Verified GitHub Signal Feed writer for FT Game Intelligence.

The collector remains a read-only public-GitHub sensor. This module is the sole
writer to the dedicated GitHub Feed tab. It never writes Evidence, Portfolio,
Trade Log, allocations or trading decisions.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from ft_github_signal_pipeline import GAME_END, GitHubClient, TORONTO, collect_all

SPREADSHEET_ID = "1ItUi3uYPK4AvuHQ6PHIZTRvQnVhMiLONefTpVLEzGGE"
GITHUB_FEED_SHEET = "GitHub Feed"
EPOCH = datetime(1899, 12, 30)

DATA_HEADERS = [
    "Captured at", "FT ticker", "Company", "Repository", "Role", "Stars", "Forks",
    "Open issues", "Commits 7d", "Commits 28d", "Commit pace ratio",
    "Contributors 28d", "Issues opened 7d", "Issues closed 7d", "PRs opened 7d",
    "PRs closed 7d", "Releases 7d", "Releases 28d", "Latest release",
    "Latest release at", "Pushed at", "Discussions available", "Activity capped",
    "Activity state", "Verification", "Observation key", "Context",
]
LOG_HEADERS = [
    "Run at", "Status", "Repositories checked", "Snapshots", "Failures",
    "API remaining", "Notes",
]

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
]


def google_client() -> Any:
    import gspread
    from google.oauth2.service_account import Credentials

    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    if not raw:
        raise RuntimeError("Missing GOOGLE_SERVICE_ACCOUNT_JSON secret")
    credentials = Credentials.from_service_account_info(json.loads(raw), scopes=SCOPES)
    return gspread.authorize(credentials)


def validate_destination(book: Any, ws: Any):
    from feed_validation import validate_destination as validate

    return validate(book, ws, {
        "FTGitHubSignals": ("A7:AA7", DATA_HEADERS, 0),
        "FTGitHubRunLog": ("AD7:AJ7", LOG_HEADERS, 29),
    })


def parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=TORONTO)
    return parsed.astimezone(TORONTO)


def serial(value: str | datetime) -> float:
    parsed = value if isinstance(value, datetime) else parse_time(value)
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(TORONTO).replace(tzinfo=None)
    return (parsed - EPOCH).total_seconds() / 86400


def observation_key(snapshot: dict[str, Any]) -> str:
    day = parse_time(str(snapshot["captured_at"])).date().isoformat()
    return f"GITHUB:{snapshot['repository']}:{day}"


def snapshot_row(snapshot: dict[str, Any]) -> list[Any]:
    latest_release_at = snapshot.get("latest_release_at") or ""
    pushed_at = snapshot.get("pushed_at") or ""
    return [
        serial(str(snapshot["captured_at"])),
        str(snapshot["ticker"]),
        str(snapshot["company"]),
        str(snapshot["repository"]),
        str(snapshot["role"]),
        int(snapshot.get("stars") or 0),
        int(snapshot.get("forks") or 0),
        int(snapshot.get("open_issues") or 0),
        int(snapshot.get("commits_7d") or 0),
        int(snapshot.get("commits_28d") or 0),
        "" if snapshot.get("commit_pace_ratio") is None else float(snapshot["commit_pace_ratio"]),
        int(snapshot.get("contributors_28d") or 0),
        int(snapshot.get("issues_opened_7d") or 0),
        int(snapshot.get("issues_closed_7d") or 0),
        int(snapshot.get("prs_opened_7d") or 0),
        int(snapshot.get("prs_closed_7d") or 0),
        int(snapshot.get("releases_7d") or 0),
        int(snapshot.get("releases_28d") or 0),
        str(snapshot.get("latest_release") or ""),
        serial(str(latest_release_at)) if latest_release_at else "",
        serial(str(pushed_at)) if pushed_at else "",
        bool(snapshot.get("discussions_available")),
        bool(snapshot.get("activity_capped")),
        str(snapshot.get("activity_state") or ""),
        str(snapshot.get("verification") or ""),
        observation_key(snapshot),
        str(snapshot.get("context") or ""),
    ]


def merge_snapshots(
    existing: list[list[Any]],
    snapshots: list[dict[str, Any]],
) -> tuple[list[list[Any]], int, int]:
    merged: dict[str, list[Any]] = {}
    for raw in existing:
        if not raw or not any(value not in ("", None) for value in raw):
            continue
        row = list(raw) + [""] * (len(DATA_HEADERS) - len(raw))
        row = row[:len(DATA_HEADERS)]
        key = str(row[25]).strip()
        if not key.startswith("GITHUB:"):
            raise ValueError("Unexpected existing GitHub observation key")
        if key in merged:
            raise ValueError("Duplicate existing GitHub observation key")
        merged[key] = row

    added = revised = 0
    for snapshot in snapshots:
        row = snapshot_row(snapshot)
        key = str(row[25])
        old = merged.get(key)
        if old is None:
            added += 1
        elif old != row:
            revised += 1
        merged[key] = row

    rows = sorted(
        merged.values(),
        key=lambda row: (
            float(row[0]) if isinstance(row[0], (int, float)) else 0,
            str(row[3]),
        ),
    )
    return rows, added, revised


def first_empty_log_row(ws: Any) -> int:
    values = ws.get(f"AD8:AD{ws.row_count}", value_render_option="UNFORMATTED_VALUE")
    for offset in range(max(ws.row_count - 7, 0)):
        row = values[offset] if offset < len(values) else []
        if not row or row[0] in ("", None):
            return 8 + offset
    return ws.row_count + 1


def cell(value: Any) -> dict[str, Any]:
    if isinstance(value, bool):
        return {"userEnteredValue": {"boolValue": value}}
    if isinstance(value, (int, float)):
        return {"userEnteredValue": {"numberValue": value}}
    return {"userEnteredValue": {"stringValue": "" if value is None else str(value)}}


def update(sheet_id: int, start_row: int, start_col: int, rows: list[list[Any]]) -> dict[str, Any]:
    width = len(rows[0])
    return {
        "updateCells": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": start_row,
                "endRowIndex": start_row + len(rows),
                "startColumnIndex": start_col,
                "endColumnIndex": start_col + width,
            },
            "rows": [{"values": [cell(value) for value in row]} for row in rows],
            "fields": "userEnteredValue",
        }
    }


def write_verified(
    book: Any,
    ws: Any,
    rows: list[list[Any]],
    log: list[Any],
    log_row: int,
) -> None:
    tables = validate_destination(book, ws)
    requests: list[dict[str, Any]] = []
    needed = max(7 + len(rows), log_row)
    if needed > ws.row_count:
        requests.append({
            "appendDimension": {
                "sheetId": ws.id,
                "dimension": "ROWS",
                "length": needed - ws.row_count + 50,
            }
        })

    if rows:
        requests.append(update(ws.id, 7, 0, rows))
        for column in (0, 19, 20):
            requests.append({
                "repeatCell": {
                    "range": {
                        "sheetId": ws.id,
                        "startRowIndex": 7,
                        "endRowIndex": 7 + len(rows),
                        "startColumnIndex": column,
                        "endColumnIndex": column + 1,
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "numberFormat": {
                                "type": "DATE_TIME",
                                "pattern": "dd/MM/yyyy HH:mm:ss",
                            }
                        }
                    },
                    "fields": "userEnteredFormat.numberFormat",
                }
            })

    pending = list(log)
    pending[1] = "Partial"
    pending[6] = "Write committed; readback pending. " + str(log[6])
    requests.append(update(ws.id, log_row - 1, 29, [pending]))
    requests.append({
        "repeatCell": {
            "range": {
                "sheetId": ws.id,
                "startRowIndex": log_row - 1,
                "endRowIndex": log_row,
                "startColumnIndex": 29,
                "endColumnIndex": 30,
            },
            "cell": {
                "userEnteredFormat": {
                    "numberFormat": {
                        "type": "DATE_TIME",
                        "pattern": "dd/MM/yyyy HH:mm:ss",
                    }
                }
            },
            "fields": "userEnteredFormat.numberFormat",
        }
    })

    for name, end_row in (
        ("FTGitHubSignals", max(8, 7 + len(rows))),
        ("FTGitHubRunLog", max(8, log_row)),
    ):
        table = tables[name]
        region = dict(table["range"])
        region["endRowIndex"] = end_row
        requests.append({
            "updateTable": {
                "table": {"tableId": table["tableId"], "range": region},
                "fields": "range",
            }
        })

    book.batch_update({"requests": requests})

    if rows:
        got = ws.get(
            f"A8:AA{7 + len(rows)}",
            value_render_option="UNFORMATTED_VALUE",
        )
        if got != rows:
            raise RuntimeError("GitHub signal data readback mismatch; provisional log remains Partial")

    book.batch_update({"requests": [update(ws.id, log_row - 1, 29, [log])]})
    got_log = ws.get(
        f"AD{log_row}:AJ{log_row}",
        value_render_option="UNFORMATTED_VALUE",
    )
    if not got_log or got_log[0] != log:
        raise RuntimeError("GitHub signal run-log readback mismatch")


def main() -> int:
    now = datetime.now(TORONTO)
    if now > GAME_END:
        print(json.dumps({"status": "Stopped", "reason": "FT game ended"}))
        return 0

    book = google_client().open_by_key(SPREADSHEET_ID)
    ws = book.worksheet(GITHUB_FEED_SHEET)
    validate_destination(book, ws)

    existing = ws.get(
        f"A8:AA{ws.row_count}",
        value_render_option="UNFORMATTED_VALUE",
    )

    client = GitHubClient(os.environ.get("GH_SIGNAL_TOKEN", ""))
    result = collect_all(client, now)
    rows, added, revised = merge_snapshots(existing, result["snapshots"])

    capped = [
        snap["repository"]
        for snap in result["snapshots"]
        if snap.get("activity_capped")
    ]
    notes = json.dumps({
        "added": added,
        "revised": revised,
        "failures": result["failures"],
        "capped": capped,
        "discussions": "Availability flag only; counts remain disabled without deliberate GraphQL authentication.",
        "semantics": result["semantics"],
        "readback": "verified before final status",
    }, ensure_ascii=False)

    log = [
        serial(now),
        result["status"],
        result["repositories_checked"],
        len(result["snapshots"]),
        len(result["failures"]),
        "" if result["api_remaining_min"] is None else int(result["api_remaining_min"]),
        notes,
    ]
    log_row = first_empty_log_row(ws)
    write_verified(book, ws, rows, log, log_row)

    print(json.dumps({
        "status": result["status"],
        "repositories_checked": result["repositories_checked"],
        "snapshots": len(result["snapshots"]),
        "added": added,
        "revised": revised,
        "failures": result["failures"],
        "capped": capped,
        "log_row": log_row,
        "readback": "verified",
    }, ensure_ascii=False))
    return 1 if result["status"] == "Failed" else 0


if __name__ == "__main__":
    sys.exit(main())

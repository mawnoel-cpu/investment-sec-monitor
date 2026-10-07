import re
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from ft_github_signal_writer import (
    DATA_HEADERS,
    LOG_HEADERS,
    merge_snapshots,
    serial,
    snapshot_row,
    write_verified,
)


class Sheet:
    id = 230000001
    row_count = 100

    def __init__(self):
        self.cells = {}
        for start, headers in ((0, DATA_HEADERS), (29, LOG_HEADERS)):
            for offset, header in enumerate(headers):
                self.cells[(6, start + offset)] = header
        self.mutations = []

    def get(self, rg, **kwargs):
        match = re.fullmatch(r"([A-Z]+)(\d+):([A-Z]+)(\d+)", rg)
        if not match:
            raise AssertionError(rg)
        a, first, b, last = match.groups()

        def col(s):
            value = 0
            for ch in s:
                value = value * 26 + ord(ch) - 64
            return value - 1

        values = [
            [self.cells.get((r, c), "") for c in range(col(a), col(b) + 1)]
            for r in range(int(first) - 1, int(last))
        ]
        while values and not any(v not in ("", None) for v in values[-1]):
            values.pop()
        return values


class Book:
    def __init__(self, ws):
        self.ws = ws
        self.calls = []
        self.tables = {
            "FTGitHubSignals": {
                "name": "FTGitHubSignals",
                "tableId": "signals",
                "range": {
                    "sheetId": ws.id,
                    "startRowIndex": 6,
                    "endRowIndex": 8,
                    "startColumnIndex": 0,
                    "endColumnIndex": 27,
                },
            },
            "FTGitHubRunLog": {
                "name": "FTGitHubRunLog",
                "tableId": "log",
                "range": {
                    "sheetId": ws.id,
                    "startRowIndex": 6,
                    "endRowIndex": 8,
                    "startColumnIndex": 29,
                    "endColumnIndex": 36,
                },
            },
        }

    def fetch_sheet_metadata(self, **kwargs):
        return {
            "sheets": [{
                "properties": {"sheetId": self.ws.id},
                "tables": list(self.tables.values()),
            }]
        }

    def batch_update(self, body):
        self.calls.append(body)
        for req in body["requests"]:
            if "appendDimension" in req:
                self.ws.row_count += req["appendDimension"]["length"]
            elif "updateCells" in req:
                q = req["updateCells"]
                for dr, row in enumerate(q["rows"]):
                    for dc, cell in enumerate(row["values"]):
                        entered = cell.get("userEnteredValue", {})
                        if "numberValue" in entered:
                            value = entered["numberValue"]
                        elif "boolValue" in entered:
                            value = entered["boolValue"]
                        else:
                            value = entered.get("stringValue", "")
                        self.ws.cells[(
                            q["range"]["startRowIndex"] + dr,
                            q["range"]["startColumnIndex"] + dc,
                        )] = value
        # update tables in a second simple pass
        for req in body["requests"]:
            if "updateTable" in req:
                table = req["updateTable"]["table"]
                for record in self.tables.values():
                    if record["tableId"] == table["tableId"]:
                        record["range"] = table["range"]


def sample_snapshot(captured="2026-10-06T10:45:00-04:00", stars=100):
    return {
        "captured_at": captured,
        "ticker": "NVDA",
        "company": "NVIDIA",
        "repository": "NVIDIA/TensorRT-LLM",
        "role": "AI inference / GPU software ecosystem",
        "stars": stars,
        "forks": 20,
        "open_issues": 30,
        "commits_7d": 5,
        "commits_28d": 12,
        "commit_pace_ratio": 2.1,
        "contributors_28d": 8,
        "issues_opened_7d": 2,
        "issues_closed_7d": 3,
        "prs_opened_7d": 4,
        "prs_closed_7d": 5,
        "releases_7d": 1,
        "releases_28d": 2,
        "latest_release": "v1",
        "latest_release_at": "2026-10-05T12:00:00Z",
        "pushed_at": "2026-10-06T12:00:00Z",
        "discussions_available": True,
        "activity_capped": False,
        "activity_state": "Accelerating",
        "verification": "Curated official/company-controlled public repository",
        "context": "Discovery/context only",
    }


class Tests(unittest.TestCase):
    def test_snapshot_row_has_stable_daily_key_and_numeric_dates(self):
        row = snapshot_row(sample_snapshot())
        self.assertEqual(len(row), len(DATA_HEADERS))
        self.assertEqual(row[25], "GITHUB:NVIDIA/TensorRT-LLM:2026-10-06")
        self.assertIsInstance(row[0], float)
        self.assertIsInstance(row[19], float)
        self.assertIsInstance(row[20], float)
        self.assertIs(row[21], True)

    def test_same_day_rerun_upserts_instead_of_duplicate(self):
        first = snapshot_row(sample_snapshot(stars=100))
        rows, added, revised = merge_snapshots([first], [sample_snapshot(stars=101)])
        self.assertEqual(len(rows), 1)
        self.assertEqual(added, 0)
        self.assertEqual(revised, 1)
        self.assertEqual(rows[0][5], 101)

    def test_destination_validation_precedes_write(self):
        ws = Sheet()
        book = Book(ws)
        ws.cells[(6, 0)] = "Wrong"
        log = [serial(datetime(2026, 10, 6, 10, 45, tzinfo=ZoneInfo("America/Toronto"))),
               "Complete", 8, 8, 0, 28, "{}"]
        with self.assertRaisesRegex(ValueError, "headers mismatch"):
            write_verified(book, ws, [snapshot_row(sample_snapshot())], log, 8)
        self.assertEqual(book.calls, [])

    def test_verified_write_updates_tables_and_log(self):
        ws = Sheet()
        book = Book(ws)
        row = snapshot_row(sample_snapshot())
        log = [serial(datetime(2026, 10, 6, 10, 45, tzinfo=ZoneInfo("America/Toronto"))),
               "Complete", 8, 1, 0, 28, "{}"]
        write_verified(book, ws, [row], log, 8)
        self.assertEqual(ws.get("A8:AA8"), [row])
        self.assertEqual(ws.get("AD8:AJ8"), [log])
        self.assertEqual(book.tables["FTGitHubSignals"]["range"]["endRowIndex"], 8)
        self.assertEqual(book.tables["FTGitHubRunLog"]["range"]["endRowIndex"], 8)


if __name__ == "__main__":
    unittest.main()

import re
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from ft_sec_pipeline import date_serial, first_empty_run_row, write_filings_verified, append_run_log


class Sheet:
    id = 2
    row_count = 100

    def __init__(self):
        self.cells = {}
        self.corrupt = False

    def get(self, rg, **kwargs):
        a, first, b, last = re.fullmatch(r'([A-Z]+)(\d+):([A-Z]+)(\d+)', rg).groups()
        col = lambda s: sum((ord(c)-64)*26**i for i, c in enumerate(reversed(s))) - 1
        values = [[self.cells.get((r, c), '') for c in range(col(a), col(b)+1)]
                  for r in range(int(first)-1, int(last))]
        while values and not any(v not in ('', None) for v in values[-1]):
            values.pop()
        if self.corrupt and col(a) == 0 and col(b) == 12:
            return []
        return values


class Book:
    def __init__(self, ws):
        self.ws = ws
        self.tables = {n: {'name': n, 'tableId': n, 'range': {'sheetId': 2, 'endRowIndex': 8}}
                       for n in ('FTSECFilings', 'FTSECRunLog')}

    def fetch_sheet_metadata(self, **kwargs):
        return {'sheets': [{'properties': {'sheetId': 2}, 'tables': list(self.tables.values())}]}

    def batch_update(self, body):
        for req in body['requests']:
            if 'updateCells' in req:
                q = req['updateCells']
                for dr, row in enumerate(q['rows']):
                    for dc, cell in enumerate(row['values']):
                        self.ws.cells[(q['range']['startRowIndex']+dr, q['range']['startColumnIndex']+dc)] = next(iter(cell['userEnteredValue'].values()))
            if 'updateTable' in req:
                table = req['updateTable']['table']
                self.tables[table['tableId']]['range'] = table['range']


class Tests(unittest.TestCase):
    def test_timestamp_is_numeric_toronto_time(self):
        a = date_serial('2026-10-02T18:00:00+00:00')
        b = date_serial(datetime(2026, 10, 2, 14, tzinfo=ZoneInfo('America/Toronto')))
        self.assertEqual(a, b)
        self.assertIsInstance(a, float)

    def test_log_gap_not_displaced_by_stray_far_down_cell(self):
        ws = Sheet()
        ws.cells[(7, 15)] = 46296
        ws.cells[(90, 15)] = 'stray'
        self.assertEqual(first_empty_run_row(ws), 9)

    def test_filing_write_extends_native_table_and_checks_values(self):
        ws = Sheet()
        book = Book(ws)
        row = [46296.5, 'MU', '0001', 'Micron', '8-K', 46295, '', 'Title', 'https://sec.gov/filing', 'document', 'new', 'SEC|1|1', 'Discovery only']
        write_filings_verified(book, ws, [row, row])
        self.assertEqual(book.tables['FTSECFilings']['range']['endRowIndex'], 9)
        self.assertEqual(ws.get('A8:M9'), [row, row])

    def test_failed_filing_readback_raises(self):
        ws = Sheet()
        ws.corrupt = True
        with self.assertRaisesRegex(RuntimeError, 'readback mismatch'):
            write_filings_verified(Book(ws), ws, [[1] * 13])

    def test_verified_log_complete_uses_contiguous_row(self):
        ws = Sheet()
        ws.cells[(7, 15)] = 46296
        book = Book(ws)
        append_run_log(book, ws, 'Success', 12, 1, 4, 0, 'Original filing still requires intake verification')
        self.assertEqual(book.tables['FTSECRunLog']['range']['endRowIndex'], 9)
        self.assertEqual(ws.cells[(8, 16)], 'Success')
        self.assertIsInstance(ws.cells[(8, 15)], float)


if __name__ == '__main__':
    unittest.main()

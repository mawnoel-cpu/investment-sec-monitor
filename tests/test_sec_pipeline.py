import re
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from ft_sec_pipeline import (date_serial, first_empty_run_row, write_filings_verified, append_run_log,
                             portfolio_universe, DATA_HEADERS, LOG_HEADERS)


class Sheet:
    id = 2
    row_count = 100

    def __init__(self):
        self.cells = {}
        for start, headers in ((0, DATA_HEADERS), (15, LOG_HEADERS)):
            for offset, header in enumerate(headers):
                self.cells[(6, start + offset)] = header
        self.mutations = []
        self.corrupt = False

    def add_rows(self, count):
        self.mutations.append(count)
        self.row_count += count

    def get(self, rg, **kwargs):
        a, first, b, last = re.fullmatch(r'([A-Z]+)(\d+):([A-Z]+)(\d+)', rg).groups()
        col = lambda s: sum((ord(c)-64)*26**i for i, c in enumerate(reversed(s))) - 1
        values = [[self.cells.get((r, c), '') for c in range(col(a), col(b)+1)]
                  for r in range(int(first)-1, int(last))]
        while values and not any(v not in ('', None) for v in values[-1]):
            values.pop()
        if self.corrupt and int(first) >= 8 and col(a) == 0 and col(b) == 12:
            return []
        return values


class Book:
    def __init__(self, ws):
        self.ws = ws
        self.calls = []
        self.tables = {n: {'name': n, 'tableId': n, 'range': {
            'sheetId': 2, 'startRowIndex': 6, 'endRowIndex': 8,
            'startColumnIndex': start, 'endColumnIndex': end}}
            for n, start, end in (('FTSECFilings', 0, 13), ('FTSECRunLog', 15, 22))}

    def fetch_sheet_metadata(self, **kwargs):
        return {'sheets': [{'properties': {'sheetId': 2}, 'tables': list(self.tables.values())}]}

    def batch_update(self, body):
        self.calls.append(body)
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
    def test_destination_guards_precede_both_writers_and_row_expansion(self):
        for writer in (lambda b, w: write_filings_verified(b, w, [[1] * 13] * 101),
                       lambda b, w: append_run_log(b, w, 'Success', 1, 0, 0, 0, 'test')):
            for table in ('FTSECFilings', 'FTSECRunLog'):
                for field, value in (('sheetId', 99), ('startRowIndex', 7),
                                     ('startColumnIndex', 1), ('endColumnIndex', 99),
                                     ('endRowIndex', 101), ('endRowIndex', 6),
                                     ('endRowIndex', None)):
                    with self.subTest(table=table, field=field, value=value):
                        ws = Sheet()
                        book = Book(ws)
                        book.tables[table]['range'][field] = value
                        with self.assertRaises(ValueError):
                            writer(book, ws)
                        self.assertEqual(book.calls, [])
                        self.assertEqual(ws.mutations, [])
            for column in (0, 15):
                for header in ('wrong', ''):
                    ws = Sheet()
                    book = Book(ws)
                    ws.cells[(6, column)] = header
                    with self.assertRaisesRegex(ValueError, 'headers mismatch'):
                        writer(book, ws)
                    self.assertEqual(book.calls, [])
            ws = Sheet()
            book = Book(ws)
            del book.tables['FTSECRunLog']
            with self.assertRaisesRegex(ValueError, 'Missing native table'):
                writer(book, ws)
            self.assertEqual(book.calls, [])

    def portfolio(self):
        ws = Sheet()
        ws.cells = {(6, i): v for i, v in enumerate(['Ticker', 'Company', 'Current', 'Queued target'])}
        for number, label in ((20, 'TOTAL'), (21, 'Weight check'), (22, 'Holdings'), (23, 'Position cap'),
                              (25, 'Rotation rules — conditional reviews, not standing orders')):
            ws.cells[(number - 1, 0)] = label
        ws.cells[(19, 2)] = 1
        ws.cells[(21, 2)] = 12
        return ws

    def test_empty_universe_and_layout_rejected_without_mutation(self):
        ws = self.portfolio()
        before = dict(ws.cells)
        with self.assertRaisesRegex(ValueError, 'empty SEC portfolio universe'):
            portfolio_universe(ws)
        self.assertEqual(ws.cells, before)
        ws.cells[(6, 0)] = 'Wrong'
        with self.assertRaisesRegex(ValueError, 'Portfolio headers mismatch'):
            portfolio_universe(ws)
        self.assertEqual(ws.mutations, [])

    def test_supported_holdings_and_summaries(self):
        ws = self.portfolio()
        ws.cells[(7, 0)] = 'MU:NSQ'
        ws.cells[(18, 0)] = 'AAPL:NSQ'
        before = dict(ws.cells)
        self.assertEqual(portfolio_universe(ws), ['MU:NSQ', 'AAPL:NSQ'])
        self.assertEqual(ws.cells, before)

    def test_outside_holdings_rejected_without_mutation(self):
        for number in (20, 24, 100):
            for ticker in ('MU:NSQ', 'AAPL', 'aapl'):
                ws = self.portfolio()
                ws.cells[(7, 0)] = 'MU:NSQ'
                ws.cells[(number - 1, 0)] = ticker
                ws.cells[(number - 1, 2)] = .1
                before = dict(ws.cells)
                with self.assertRaisesRegex(ValueError, 'outside supported'):
                    portfolio_universe(ws)
                self.assertEqual(ws.cells, before)
                self.assertEqual(ws.mutations, [])

    def test_rotation_and_review_tables_are_not_holdings(self):
        ws = self.portfolio()
        ws.cells[(7, 0)] = 'MU:NSQ'
        for number, values in ((26, ['Source', 'Possible trim', 'Destination', 'Initial size']),
                               (27, ['CRWV', '10% → 5%', 'NVDA', '0% → 5%']),
                               (32, ['BHP:LSE', '10% → 5%', 'Qualified reserve', 'Initial 5%']),
                               (37, ['09:40', 'America/Toronto', 'Next-open execution', 'Verify actual weights'])):
            for i, v in enumerate(values):
                ws.cells[(number - 1, i)] = v
        self.assertEqual(portfolio_universe(ws), ['MU:NSQ'])

    def test_main_validation_errors_never_collect_or_write(self):
        import ft_sec_pipeline as pipeline
        from unittest.mock import Mock, patch
        for problem in ('empty', 'outside', 'header', 'bounds'):
            portfolio = self.portfolio()
            ws = Sheet()
            book = Book(ws)
            if problem == 'outside':
                portfolio.cells[(7, 0)] = 'MU:NSQ'
                portfolio.cells[(23, 0)] = 'AAPL:NSQ'
            elif problem == 'header':
                ws.cells[(6, 0)] = 'Wrong'
            elif problem == 'bounds':
                book.tables['FTSECFilings']['range']['startRowIndex'] = 7
            book.worksheet = lambda name: portfolio if name == 'Portfolio' else ws
            google = Mock()
            google.open_by_key.return_value = book
            with (
                patch.object(pipeline, 'google_client', return_value=google),
                patch.object(pipeline, 'SecClient') as sec,
                patch.object(pipeline, 'ticker_map') as mapping,
                patch.object(pipeline, 'datetime') as clock,
            ):
                clock.now.return_value = datetime(2026, 10, 2, tzinfo=ZoneInfo('America/Toronto'))
                with self.assertRaises(ValueError):
                    pipeline.main()
                mapping.assert_not_called()
                sec.return_value.get_json.assert_not_called()
            self.assertEqual(book.calls, [])
            self.assertEqual(ws.mutations, [])

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
        row = [46296.5, 'MU', 'Micron', '0001', '8-K', 46295, '', '1', 'Title', 'https://sec.gov/filing', 'Discovery', 'SEC|1|1', 'Discovery only']
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

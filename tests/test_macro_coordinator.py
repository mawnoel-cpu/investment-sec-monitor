import unittest
from datetime import datetime
from ft_macro_coordinator import merge_rows, diagnostics, serial, normalize, write_verified, TZ, DATA_HEADERS, LOG_HEADERS, cftc_measures

NOW = datetime(2026, 10, 1, 18, tzinfo=TZ)


def row(
    sid='A',
    date='2026-09-30',
    value=1,
    captured='2026-10-01T08:00:00-04:00',
    source='FRED',
    frequency='Daily',
):
    return [
        captured, source, sid, sid, date, value, 'Percent', frequency,
        'Observation', 'US', 'https://fred.stlouisfed.org/series/' + sid,
        'old-value-key', '', 'Verified data', '',
    ]


class FakeBook:
    def __init__(self, ws):
        self.ws = ws
        self.calls = []
        self.tables = [{
            'name': n, 'tableId': n,
            'range': {'sheetId': 1, 'startRowIndex': 6, 'endRowIndex': 8,
                      'startColumnIndex': start, 'endColumnIndex': end}}
            for n, start, end in [('FTMacroObservations', 0, 15), ('FTMacroRunLog', 17, 25)]]

    def fetch_sheet_metadata(self, **kwargs):
        return {
            'sheets': [{
                'properties': {'sheetId': 1},
                'tables': self.tables,
            }]
        }

    def batch_update(self, body):
        self.calls.append(body)
        for req in body['requests']:
            if 'updateCells' in req:
                r = req['updateCells']
                values = [
                    [next(iter(c['userEnteredValue'].values())) for c in x['values']]
                    for x in r['rows']
                ]
                if r['range']['startColumnIndex'] == 0:
                    self.ws.data = values
                else:
                    self.ws.log = values


class FakeWS:
    id = 1
    row_count = 100

    def __init__(self, corrupt=False):
        self.data = []
        self.log = []
        self.corrupt = corrupt

    def get(self, rg, **kwargs):
        if rg == 'A7:O7':
            return [DATA_HEADERS]
        if rg == 'R7:Y7':
            return [LOG_HEADERS]
        if rg.startswith('A'):
            return [] if self.corrupt else self.data
        return self.log


class Tests(unittest.TestCase):
    def cftc_rows(self, contract='023651', date='2026-09-29', value=1):
        rows = []
        for measure in sorted(cftc_measures(contract)):
            r = row(sid=contract, source='CFTC', date=date, value=value, frequency='Weekly')
            r[8] = measure
            rows.append(r)
        return rows

    def test_cftc_complete_collections_for_both_report_types(self):
        for contract in ('023651', '043602'):
            incoming = self.cftc_rows(contract)
            rs, added, revised, refreshed, errors = merge_rows([], incoming, {('CFTC', contract)}, NOW)
            self.assertEqual((len(rs), added, revised), (5, 5, 0))
            self.assertEqual(refreshed, {('CFTC', contract)})
            self.assertFalse(errors)
            self.assertFalse(diagnostics(rs, {('CFTC', contract)}, NOW)[1])

    def test_each_missing_cftc_measure_preserves_prior_collection(self):
        for contract in ('023651', '043602'):
            good = self.cftc_rows(contract)
            for missing in range(5):
                incoming = self.cftc_rows(contract, value=2)
                del incoming[missing]
                rs, added, revised, refreshed, errors = merge_rows(good, incoming, {('CFTC', contract)}, NOW)
                self.assertEqual(rs, [normalize(r) for r in good])
                self.assertEqual((added, revised), (0, 0))
                self.assertFalse(refreshed)
                self.assertTrue(errors)

    def test_cftc_dates_cannot_complete_each_other(self):
        incoming = self.cftc_rows()
        incoming[-1][4] = '2026-09-22'
        rs, _, _, refreshed, errors = merge_rows([], incoming, {('CFTC', '023651')}, NOW)
        self.assertEqual(rs, [])
        self.assertFalse(refreshed)
        self.assertTrue(errors)

    def test_invalid_duplicate_or_unexpected_measure_rejects_collection(self):
        for mode in ('invalid', 'duplicate', 'unexpected'):
            incoming = self.cftc_rows(value=2)
            if mode == 'invalid':
                incoming[-1][5] = 'nan'
            elif mode == 'duplicate':
                incoming.append(list(incoming[0]))
            else:
                incoming[-1][8] = 'Observation'
            good = self.cftc_rows()
            rs, _, _, refreshed, errors = merge_rows(good, incoming, {('CFTC', '023651')}, NOW)
            self.assertEqual(rs, [normalize(r) for r in good])
            self.assertFalse(refreshed)
            self.assertTrue(errors)

    def test_incomplete_new_date_preserves_good_date_and_allows_other_sources(self):
        good = self.cftc_rows(date='2026-09-22')
        incoming = self.cftc_rows()[:1] + [row()]
        rs, added, revised, refreshed, errors = merge_rows(good, incoming, {('CFTC', '023651'), ('FRED', 'A')}, NOW)
        self.assertEqual(rs[:5], [normalize(r) for r in good])
        self.assertEqual((added, revised), (1, 0))
        self.assertEqual(refreshed, {('FRED', 'A')})
        self.assertTrue(errors)  # Coordinator chooses Partial when a source refreshed.

    def test_diagnostics_flags_incomplete_stored_collection(self):
        self.assertTrue(diagnostics([normalize(self.cftc_rows()[0])], {('CFTC', '023651')}, NOW)[1])

    def test_destination_validation_before_any_batch(self):
        for index in (0, 1):
            for field, value in (('sheetId', 2), ('startRowIndex', 7), ('startColumnIndex', 1),
                                 ('endColumnIndex', 99), ('endRowIndex', 101), ('endRowIndex', 6)):
                ws = FakeWS()
                book = FakeBook(ws)
                book.tables[index]['range'][field] = value
                with self.assertRaisesRegex(ValueError, 'bounds'):
                    write_verified(book, ws, [], [0] * 8, 101)
                self.assertEqual(book.calls, [])
        from unittest.mock import patch
        for header in ('A7:O7', 'R7:Y7'):
            ws = FakeWS()
            book = FakeBook(ws)
            original = ws.get
            with patch.object(ws, 'get', side_effect=lambda rg, **kw: [] if rg == header else original(rg, **kw)):
                with self.assertRaisesRegex(ValueError, 'headers mismatch'):
                    write_verified(book, ws, [], [0] * 8, 8)
            self.assertEqual(book.calls, [])

    def test_mocked_main_keeps_incomplete_cftc_partial_and_prior_values(self):
        import ft_macro_coordinator as coordinator
        import ft_macro_pipeline as pipeline
        from unittest.mock import Mock, patch
        good = self.cftc_rows()
        ws = FakeWS()
        ws.data = good
        book = FakeBook(ws)
        book.worksheet = lambda name: ws
        google = Mock()
        google.open_by_key.return_value = book
        with (
            patch.object(pipeline, 'google_client', return_value=google),
            patch.dict(pipeline.FRED_SERIES, {}, clear=True),
            patch.dict(pipeline.EIA_SERIES, {}, clear=True),
            patch.dict(pipeline.CFTC_COMMODITIES, {'023651': ('Gas', 'Contracts')}, clear=True),
            patch.dict(pipeline.CFTC_TFF, {}, clear=True),
            patch.object(pipeline, 'fred_rows', return_value=([], [])),
            patch.object(pipeline, 'eia_rows', return_value=([], [])),
            patch.object(pipeline, 'cftc_rows', return_value=(self.cftc_rows(value=99)[:1], [])),
            patch.object(coordinator, 'datetime', wraps=datetime) as clock,
        ):
            clock.now.return_value = NOW
            self.assertEqual(coordinator.main(), 0)
        self.assertEqual(ws.data, [normalize(r) for r in good])
        self.assertEqual(ws.log[0][2], 'Partial')
        self.assertEqual(ws.log[0][4:6], [0, 0])
        self.assertIn('incomplete measure collection', ws.log[0][7])

    def test_main_rejects_destination_before_collectors_or_writes(self):
        import ft_macro_coordinator as coordinator
        import ft_macro_pipeline as pipeline
        from unittest.mock import Mock, patch
        ws = FakeWS()
        book = FakeBook(ws)
        book.tables[0]['range']['startRowIndex'] = 7
        book.worksheet = lambda name: ws
        google = Mock()
        google.open_by_key.return_value = book
        with (
            patch.object(pipeline, 'google_client', return_value=google),
            patch.object(pipeline, 'fred_rows') as fred,
            patch.object(pipeline, 'eia_rows') as eia,
            patch.object(pipeline, 'cftc_rows') as cftc,
            patch.object(coordinator, 'datetime', wraps=datetime) as clock,
        ):
            clock.now.return_value = NOW
            with self.assertRaisesRegex(ValueError, 'bounds'):
                coordinator.main()
            for collector in (fred, eia, cftc):
                collector.assert_not_called()
        self.assertEqual(book.calls, [])

    def test_total_failure_status_preserved(self):
        from ft_macro_coordinator import run_status
        self.assertEqual(run_status(set(), ['source unavailable']), 'Failed')
        self.assertEqual(run_status({('FRED', 'A')}, []), 'Complete')

    def test_partial_series_failure_retains_other_series(self):
        rs, add, rev, seen, err = merge_rows(
            [row('A'), row('B')],
            [row('A', value=2)],
            {('FRED', 'A'), ('FRED', 'B')},
            NOW,
        )
        self.assertEqual(len(rs), 2)
        self.assertEqual(rs[1][5], 1)
        self.assertTrue(err)
        self.assertEqual((add, rev), (0, 1))

    def test_revision_does_not_duplicate(self):
        rs, add, rev, _, _ = merge_rows(
            [row()], [row(value=2)], {('FRED', 'A')}, NOW
        )
        self.assertEqual((len(rs), add, rev), (1, 0, 1))

    def test_total_failure_preserves_dates(self):
        rs, _, _, seen, err = merge_rows([row()], [], {('FRED', 'A')}, NOW)
        self.assertEqual(rs[0][0], serial(row()[0]))
        self.assertFalse(seen)
        self.assertTrue(err)

    def test_future_row_rejected(self):
        rs, _, _, _, err = merge_rows(
            [row()], [row(date='2026-10-02')], {('FRED', 'A')}, NOW
        )
        self.assertEqual(len(rs), 1)
        self.assertTrue(err)

    def test_old_observation_not_fresh_after_copy(self):
        _, err = diagnostics(
            [normalize(row(date='2026-09-01'))], {('FRED', 'A')}, NOW
        )
        self.assertTrue(err)

    def test_old_upstream_retrieval_remains_stale(self):
        _, err = diagnostics(
            [normalize(row(captured='2026-09-20T08:00:00-04:00'))],
            {('FRED', 'A')},
            NOW,
        )
        self.assertTrue(err)

    def test_weekly_eia_retrieval_uses_weekly_cadence(self):
        weekly = normalize(row(
            sid='NG.NW2_EPG0_SWO_R48_BCF.W',
            date='2026-09-25',
            value=3351,
            captured='2026-09-25T12:00:00-04:00',
            source='EIA',
            frequency='Weekly',
        ))
        details, err = diagnostics(
            [weekly], {('EIA', 'NG.NW2_EPG0_SWO_R48_BCF.W')}, NOW
        )
        self.assertFalse(err)
        self.assertEqual(
            details['EIA:NG.NW2_EPG0_SWO_R48_BCF.W']['status'], 'Current'
        )

    def test_atomic_write_and_full_eight_column_log(self):
        ws = FakeWS()
        b = FakeBook(ws)
        log = [serial(NOW.isoformat()), 'coordinator', 'Complete', 1, 1, 0, 0, 'verified']
        write_verified(b, ws, [normalize(row())], log, 8)
        self.assertEqual(len(b.calls), 2)
        self.assertEqual(ws.log[0], log)
        self.assertTrue(any(
            'updateCells' in r and r['updateCells']['range']['startColumnIndex'] == 0
            for r in b.calls[0]['requests']
        ))

    def test_readback_failure_cannot_claim_complete(self):
        ws = FakeWS(True)
        b = FakeBook(ws)
        log = [serial(NOW.isoformat()), 'coordinator', 'Complete', 1, 1, 0, 0, 'verified']
        with self.assertRaises(RuntimeError):
            write_verified(b, ws, [normalize(row())], log, 8)
        self.assertEqual(ws.log[0][2], 'Partial')
        self.assertEqual(len(b.calls), 1)

    def test_cftc_leading_zero_restored(self):
        r = row()
        r[1] = 'CFTC'
        r[2] = 23651
        self.assertEqual(normalize(r)[2], '023651')

    def test_unverified_relay_cannot_be_current(self):
        raw = row()
        raw[13] = 'Needs verification'
        details, err = diagnostics([normalize(raw)], {('FRED', 'A')}, NOW)
        self.assertTrue(err)
        self.assertEqual(details['FRED:A']['status'], 'Unverified')

    def test_new_storage_release_required_after_thursday(self):
        raw = row(sid='NG.NW2_EPG0_SWO_R48_BCF.W', date='2026-09-18', source='EIA', frequency='Weekly')
        details, err = diagnostics([normalize(raw)], {('EIA', raw[2])}, NOW)
        self.assertTrue(err)
        self.assertEqual(details['EIA:' + raw[2]]['normal_latest_due'], '2026-09-25')

    def test_friday_cftc_release_changes_observation_due(self):
        raw = row(sid='023651', date='2026-09-22', source='CFTC', frequency='Weekly')
        group = {('CFTC', '023651')}
        before = datetime(2026, 10, 2, 15, 0, tzinfo=TZ)
        after = datetime(2026, 10, 2, 16, 0, tzinfo=TZ)
        self.assertFalse(diagnostics([normalize(r) for r in self.cftc_rows(date='2026-09-22')], group, before)[1])
        self.assertTrue(diagnostics([normalize(raw)], group, after)[1])

    def test_monday_cftc_due_is_previous_tuesday(self):
        raw = row(sid='023651', date='2026-09-29', captured='2026-10-02T18:00:00-04:00', source='CFTC', frequency='Weekly')
        rows = self.cftc_rows()
        for r in rows:
            r[0] = '2026-10-02T18:00:00-04:00'
        details, err = diagnostics([normalize(r) for r in rows], {('CFTC', '023651')}, datetime(2026, 10, 5, 8, tzinfo=TZ))
        self.assertFalse(err)
        self.assertEqual(details['CFTC:023651']['normal_latest_due'], '2026-09-29')


if __name__ == '__main__':
    unittest.main()

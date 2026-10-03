import unittest
from datetime import datetime
from ft_macro_coordinator import merge_rows, diagnostics, serial, normalize, write_verified, TZ

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

    def fetch_sheet_metadata(self, **kwargs):
        return {
            'sheets': [{
                'properties': {'sheetId': 1},
                'tables': [
                    {
                        'name': n,
                        'tableId': n,
                        'range': {'sheetId': 1, 'endRowIndex': 8},
                    }
                    for n in ['FTMacroObservations', 'FTMacroRunLog']
                ],
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
        if rg.startswith('A'):
            return [] if self.corrupt else self.data
        return self.log


class Tests(unittest.TestCase):
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
        self.assertFalse(diagnostics([normalize(raw)], group, before)[1])
        self.assertTrue(diagnostics([normalize(raw)], group, after)[1])

    def test_monday_cftc_due_is_previous_tuesday(self):
        raw = row(sid='023651', date='2026-09-29', captured='2026-10-02T18:00:00-04:00', source='CFTC', frequency='Weekly')
        details, err = diagnostics([normalize(raw)], {('CFTC', '023651')}, datetime(2026, 10, 5, 8, tzinfo=TZ))
        self.assertFalse(err)
        self.assertEqual(details['CFTC:023651']['normal_latest_due'], '2026-09-29')


if __name__ == '__main__':
    unittest.main()

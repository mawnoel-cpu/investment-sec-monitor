import unittest
from unittest.mock import patch
from types import SimpleNamespace
import xlrd
import ft_macro_pipeline as pipeline


class Sheet:
    ncols = 2

    def __init__(self, header):
        self.cells = [[('', xlrd.XL_CELL_TEXT), (header, xlrd.XL_CELL_TEXT)],
                      [(46290, xlrd.XL_CELL_DATE), (3415, xlrd.XL_CELL_NUMBER)]]
        self.nrows = len(self.cells)

    def cell_value(self, r, c):
        return self.cells[r][c][0]

    def cell_type(self, r, c):
        return self.cells[r][c][1]


class Sources(unittest.TestCase):
    def book(self, header):
        return SimpleNamespace(datemode=0, sheets=lambda: [Sheet(header)])

    def test_storage_identity_and_units_are_required(self):
        good = self.book('Lower 48 States Natural Gas Working Underground Storage (Billion Cubic Feet)')
        self.assertEqual(pipeline.eia_workbook_observations(good, 'NG.NW2_EPG0_SWO_R48_BCF.W'), [('2026-09-25', 3415.0)])
        for bad in ('Working gas storage capacity (Billion Cubic Feet)', 'Lower 48 Working Underground Storage (Million Cubic Feet)'):
            with self.assertRaises(ValueError):
                pipeline.eia_workbook_observations(self.book(bad), 'NG.NW2_EPG0_SWO_R48_BCF.W')

    def test_spr_category_cannot_be_substituted(self):
        with self.assertRaises(ValueError):
            pipeline.eia_workbook_observations(self.book('U.S. Stocks of Crude Oil Including SPR (Thousand Barrels)'), 'PET.WCESTUS1.W')

    def test_fred_missing_observation_is_not_zero(self):
        data = 'DATE,BAMLH0A0HYM2\n2026-09-30,.\n2026-10-01,3.24\n'
        with patch.dict(pipeline.FRED_SERIES, {'BAMLH0A0HYM2': ('HY', 'Percent', 'Daily')}, clear=True), patch.dict(pipeline.os.environ, {'FRED_API_KEY': ''}, clear=False), patch.object(pipeline, 'http_get', return_value=SimpleNamespace(text=data)):
            rows, errors = pipeline.fred_rows('2026-10-02T20:00:00-04:00')
        self.assertFalse(errors)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][5], 3.24)
        self.assertEqual(rows[0][11], 'FRED:BAMLH0A0HYM2:2026-10-01:Observation')
        self.assertEqual(rows[0][13], 'Verified data')
        self.assertIn('official fred csv', rows[0][14].lower())

    def test_fred_authenticated_api_is_preferred_when_key_is_available(self):
        response = SimpleNamespace(
            json=lambda: {'observations': [
                {'date': '2026-10-02', 'value': '3.10'},
                {'date': '2026-10-01', 'value': '3.24'},
            ]}
        )
        with patch.dict(pipeline.FRED_SERIES, {'BAMLH0A0HYM2': ('HY', 'Percent', 'Daily')}, clear=True), patch.dict(pipeline.os.environ, {'FRED_API_KEY': 'test-key'}, clear=False), patch.object(pipeline, 'http_get', return_value=response) as get:
            rows, errors = pipeline.fred_rows('2026-10-05T16:00:00-04:00')
        self.assertFalse(errors)
        self.assertEqual([row[5] for row in rows], [3.24, 3.1])
        self.assertTrue(all('Official FRED API' in row[14] for row in rows))
        self.assertEqual(get.call_args.kwargs['params']['series_id'], 'BAMLH0A0HYM2')
        self.assertEqual(get.call_args.kwargs['params']['api_key'], 'test-key')

    def test_fred_api_parser_skips_missing_values(self):
        rows = pipeline.fred_api_observations(
            {'observations': [
                {'date': '2026-10-01', 'value': '.'},
                {'date': '2026-10-02', 'value': '3.10'},
            ]},
            'BAMLH0A0HYM2',
        )
        self.assertEqual(rows, [('2026-10-02', 3.10)])

    def test_fred_csv_requires_exact_series_identity(self):
        with self.assertRaises(ValueError):
            pipeline.fred_csv_observations(
                'DATE,DFII10\n2026-10-01,2.88\n',
                'BAMLH0A0HYM2',
            )

    def test_fred_html_fallback_remains_available(self):
        html = '<h1>HY (BAMLH0A0HYM2)</h1> Units: Percent Frequency: Daily <p>2026-10-01: 3.24</p>'
        responses = [
            RuntimeError('csv unavailable'),
            SimpleNamespace(text=html),
        ]
        with patch.dict(pipeline.FRED_SERIES, {'BAMLH0A0HYM2': ('HY', 'Percent', 'Daily')}, clear=True), patch.dict(pipeline.os.environ, {'FRED_API_KEY': ''}, clear=False), patch.object(pipeline, 'http_get', side_effect=responses):
            rows, errors = pipeline.fred_rows('2026-10-02T20:00:00-04:00')
        self.assertFalse(errors)
        self.assertEqual(rows[0][5], 3.24)
        self.assertIn('series-page fallback', rows[0][14])

    def test_fred_page_identity_and_units_reject_substitution(self):
        for text in ('(DFII10) Units: Percent Frequency: Daily 2026-10-01: 3.24', '(BAMLH0A0HYM2) Units: Index Frequency: Daily 2026-10-01: 3.24'):
            with self.assertRaises(ValueError):
                pipeline.fred_page_observations(text, 'BAMLH0A0HYM2')

    def test_fred_quarter_is_observation_period_not_release_date(self):
        text = '(DRTSCILM) Units: Percent Frequency: Quarterly Q3 2026: 0.0 Updated: Aug 3, 2026'
        self.assertEqual(pipeline.fred_page_observations(text, 'DRTSCILM'), [('2026-07-01', 0.0)])

    def test_eia_storage_dates_and_units(self):
        text = '<h1>Lower 48 Natural Gas Working Underground Storage (Billion Cubic Feet)</h1><table><tr><td>2026-Sep</td><td>09/18</td><td>3,351</td><td>09/25</td><td>3,415</td></tr></table>'
        self.assertEqual(pipeline.eia_page_observations(text, 'NG.NW2_EPG0_SWO_R48_BCF.W'), [('2026-09-18', 3351.0), ('2026-09-25', 3415.0)])
        with self.assertRaises(ValueError):
            pipeline.eia_page_observations(text.replace('Billion', 'Million'), 'NG.NW2_EPG0_SWO_R48_BCF.W')

    def test_henry_hub_blank_days_are_not_zero_or_shifted(self):
        text = '<h1>Henry Hub Natural Gas Spot Price (Dollars per Million Btu)</h1><table><tr><td>2026 Sep-28 to Oct- 2</td><td>3.13</td><td>3.18</td><td></td><td></td><td></td></tr></table>'
        self.assertEqual(pipeline.eia_page_observations(text, 'NG.RNGWHHD.D'), [('2026-09-28', 3.13), ('2026-09-29', 3.18)])

    def test_tff_aliases_stay_in_same_report(self):
        self.assertEqual(pipeline._number({'lev_money_positions_long': '12'}, 'lev_money_positions_long_all', 'lev_money_positions_long'), 12)
        with self.assertRaises(KeyError):
            pipeline._number({'m_money_positions_long_all': '12'}, 'lev_money_positions_long_all', 'lev_money_positions_long')

    def test_electricity_stays_excluded(self):
        self.assertFalse(pipeline.EIA_SERIES['ELEC.GEN.ALL-US-99.M'][4])
        self.assertEqual(len([m for m in pipeline.EIA_SERIES.values() if m[4]]), 3)


if __name__ == '__main__':
    unittest.main()

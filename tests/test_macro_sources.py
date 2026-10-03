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
        data = 'observation_date,BAMLH0A0HYM2\n2026-09-30,.\n2026-10-01,3.24\n'
        with patch.dict(pipeline.FRED_SERIES, {'BAMLH0A0HYM2': ('HY', 'Percent', 'Daily')}, clear=True), patch.object(pipeline, 'http_get', return_value=SimpleNamespace(text=data)):
            rows, errors = pipeline.fred_rows('2026-10-02T20:00:00-04:00')
        self.assertFalse(errors)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][5], 3.24)
        self.assertEqual(rows[0][11], 'FRED:BAMLH0A0HYM2:2026-10-01:Observation')
        self.assertEqual(rows[0][13], 'Verified data')

    def test_tff_aliases_stay_in_same_report(self):
        self.assertEqual(pipeline._number({'lev_money_positions_long': '12'}, 'lev_money_positions_long_all', 'lev_money_positions_long'), 12)
        with self.assertRaises(KeyError):
            pipeline._number({'m_money_positions_long_all': '12'}, 'lev_money_positions_long_all', 'lev_money_positions_long')

    def test_electricity_stays_excluded(self):
        self.assertFalse(pipeline.EIA_SERIES['ELEC.GEN.ALL-US-99.M'][4])
        self.assertEqual(len([m for m in pipeline.EIA_SERIES.values() if m[4]]), 3)


if __name__ == '__main__':
    unittest.main()

import unittest
from datetime import datetime
from unittest.mock import Mock, patch
import ft_macro_pipeline as p
import ft_macro_coordinator as c


class ReleaseCalendarTests(unittest.TestCase):
    def row(self):
        return ['2026-10-14T17:00:00-04:00', 'EIA', 'NG.RNGWHHD.D', 'Henry Hub',
                '2026-10-06', 3.03, 'USD per MMBtu', 'Daily', 'Observation', 'US energy',
                'https://www.eia.gov/dnav/ng/hist/rngwhhdd.htm', 'key', '2026-10-14',
                'Verified data', 'official_release=2026-10-07; next_release=2026-10-15']

    def test_fresh_official_calendar_explains_older_observation(self):
        details, errors = c.diagnostics([self.row()], {('EIA', 'NG.RNGWHHD.D')},
                                       datetime(2026, 10, 14, 18, tzinfo=c.TZ))
        self.assertEqual(errors, [])
        self.assertEqual(details['EIA:NG.RNGWHHD.D']['status'], 'Awaiting official release')

    def test_calendar_does_not_excuse_missing_new_release_or_old_fetch(self):
        for now in (datetime(2026, 10, 15, 18, tzinfo=c.TZ), datetime(2026, 10, 16, 18, tzinfo=c.TZ)):
            self.assertTrue(c.diagnostics([self.row()], {('EIA', 'NG.RNGWHHD.D')}, now)[1])

    def test_parser_preserves_dates_and_values_separately(self):
        text = '<h1>Henry Hub Natural Gas Spot Price (Dollars per Million Btu)</h1><table><tr><td>2026 Oct- 5 to Oct- 9</td><td>2.96</td><td>3.03</td></tr></table><p>Release Date: 10/7/2026</p><p>Next Release Date: 10/15/2026</p>'
        with patch.dict(p.EIA_SERIES, {'NG.RNGWHHD.D': p.EIA_SERIES['NG.RNGWHHD.D']}, clear=True), patch.object(p, 'http_get', return_value=Mock(text=text)):
            rows, errors, _ = p.eia_rows('2026-10-14T17:00:00-04:00')
        self.assertEqual(errors, [])
        self.assertEqual(rows[-1][4:6], ['2026-10-06', 3.03])
        self.assertIn('next_release=2026-10-15', rows[-1][14])

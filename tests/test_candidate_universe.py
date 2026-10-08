import unittest
from ft_sec_pipeline import research_universe


class Source:
    def __init__(self, header, values):
        self.header = header
        self.values = values

    def get(self, region):
        return [[self.header]] if region in ('A4:A4', 'C4:C4') else self.values


class Book:
    def __init__(self, pool, bench):
        self.sources = {'CANDIDATE POOL': pool, 'ROTATION BENCH': bench}

    def worksheet(self, title):
        if self.sources[title] is None:
            raise RuntimeError('unavailable')
        return self.sources[title]


class CandidateTests(unittest.TestCase):
    def test_holdings_preserved_and_aliases_deduplicated(self):
        book = Book(Source('Ticker', [['MU'], ['MSFT'], ['msft:NSQ'], ['BHP']]),
                    Source('BACKUP', [['ANET'], ['MSFT']]))
        self.assertEqual(research_universe(book, ['MU:NSQ', 'BHP:LSE']),
                         (['MU:NSQ', 'BHP:LSE', 'MSFT', 'ANET'], []))

    def test_bad_pool_is_visible_gap_and_bench_still_collected(self):
        for pool in (None, Source('Wrong', [['MSFT']]), Source('Ticker', []),
                     Source('Ticker', [['MSFT'], ['not a ticker']])):
            with self.subTest(pool=pool):
                names, failures = research_universe(Book(pool, Source('BACKUP', [['ANET']])), ['MU'])
                self.assertEqual(names, ['MU', 'ANET'])
                self.assertEqual(len(failures), 1)
                self.assertIn('CANDIDATE POOL universe unavailable', failures[0])

    def test_unavailable_bench_does_not_hide_valid_pool(self):
        names, failures = research_universe(Book(Source('Ticker', [['MSFT']]), None), ['MU'])
        self.assertEqual(names, ['MU', 'MSFT'])
        self.assertIn('ROTATION BENCH universe unavailable', failures[0])

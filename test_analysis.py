"""Regression checks for easily misread values in the pinned WDI snapshot."""
import csv
import sqlite3
import tempfile
import unittest
from pathlib import Path

from analyze import build


class PortfolioAnalysisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.out = Path(cls.temp.name)
        cls.quality = build(cls.out)
        cls.db = sqlite3.connect(cls.out / 'build/healthcare.sqlite')

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def test_missing_beds_preserve_the_financing_observation(self):
        pair = self.db.execute('SELECT beds_per_1000, oop_share_pct FROM country_year '
                               "WHERE iso3 = 'QAT' AND year = 2020").fetchone()
        self.assertEqual(pair, (None, 5.89859581))
        with (self.out / 'reports/country_year.csv').open() as file:
            rows = list(csv.DictReader(file))
        self.assertEqual(len(rows), 60)
        qatar = next(r for r in rows if r['iso3'] == 'QAT' and r['year'] == '2020')
        self.assertEqual(qatar['beds_per_1000'], '')
        self.assertAlmostEqual(float(qatar['oop_share_pct']), pair[1])

    def test_peer_comparison_rejects_partial_latest_year(self):
        self.assertEqual(self.quality['latest_complete_year'], 2022)
        self.assertEqual(self.quality['complete_pairs'], 54)
        totals = self.db.execute('SELECT complete_pairs, financing_values FROM annual_coverage '
                                 'WHERE year = 2023').fetchone()
        self.assertEqual(totals, (2, 6))

    def test_endpoint_changes_use_common_years_and_percentage_points(self):
        row = self.db.execute("SELECT first_year, last_year, beds_change, oop_change_pp "
                              "FROM common_period_change WHERE iso3 = 'ARE'").fetchone()
        self.assertEqual(row[:2], (2015, 2022))
        self.assertAlmostEqual(row[2], 1.87 - 1.43)
        self.assertAlmostEqual(row[3], 9.71845818 - 17.78964996)


if __name__ == '__main__':
    unittest.main()

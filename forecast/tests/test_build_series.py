"""F1 tests: the official Osaka series and the checks that guard it.

Run from the repo root:  python -m unittest discover -s forecast/tests -v

`forecast/` is deliberately not a Python package, so `python manage.py test`
does not collect these (later phases need pandas, which Django must not need).
"""
import csv
import datetime as dt
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

FORECAST_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FORECAST_DIR))

import build_series as bs  # noqa: E402

RAW = bs.RAW_DIR


class RealSeries(unittest.TestCase):
    """The committed raw files produce a complete series that matches the organiser."""

    @classmethod
    def setUpClass(cls):
        cls.rows = bs.build()
        cls.by_date = {r["date"]: r for r in cls.rows}

    def test_every_expo_day_once(self):
        self.assertEqual(len(self.rows), 184)
        self.assertEqual(self.rows[0]["date"], "2025-04-13")
        self.assertEqual(self.rows[-1]["date"], "2025-10-13")
        self.assertEqual(len(self.by_date), 184)

    def test_totals_match_the_final_official_cumulative(self):
        self.assertEqual(sum(int(r["visitors_total"]) for r in self.rows), 29_017_924)
        self.assertEqual(sum(int(r["ad_pass_entries"]) for r in self.rows), 3_438_938)
        self.assertEqual(sum(int(r["visitors_general"]) for r in self.rows), 25_578_986)

    def test_general_is_total_minus_ad_on_every_row(self):
        for r in self.rows:
            self.assertEqual(int(r["visitors_general"]),
                             int(r["visitors_total"]) - int(r["ad_pass_entries"]), r["date"])

    def test_every_row_is_official_and_traceable(self):
        for r in self.rows:
            self.assertEqual(r["origin"], "official")
            self.assertTrue(r["source_url"].startswith("https://www.expo2025.or.jp/news/news-2025"), r["date"])
            self.assertTrue(r["retrieved_on"])

    def test_independent_fact_34_days_above_200k_from_10_september(self):
        # expo_data page: 34 consecutive days above 200,000 visitors, starting 10 Sep.
        # Not used to build the series, so it is an independent check of the transcription.
        best = run = 0
        start = best_start = None
        for r in self.rows:
            if int(r["visitors_total"]) > 200_000:
                run += 1
                start = start or r["date"]
                if run > best:
                    best, best_start = run, start
            else:
                run, start = 0, None
        self.assertEqual((best, best_start), (34, "2025-09-10"))

    def test_the_team_sheets_four_official_days_agree(self):
        # CoverCast_datasets.xlsx, sheet daily_visitors, rows marked official (read in F0).
        # Three are rounded in the sheet; 13 Oct is exact.
        g = {d: int(self.by_date[d]["visitors_general"]) for d in self.by_date}
        self.assertEqual(round(g["2025-04-15"], -2), 47_800)
        self.assertEqual(round(g["2025-08-30"], -3), 187_000)
        self.assertEqual(round(g["2025-09-22"], -2), 225_300)
        self.assertEqual(g["2025-10-13"], 207_889)

    def test_csv_on_disk_is_current(self):
        self.assertEqual(bs.main(["--check"]), 0)


class Guards(unittest.TestCase):
    """Each check refuses the mistake it exists for (sabotage on a copy of the raw files)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        shutil.copytree(RAW, self.tmp / "raw")
        self.raw = self.tmp / "raw"

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def edit(self, name, old, new):
        p = self.raw / f"{name}.txt"
        text = p.read_text(encoding="utf-8")
        self.assertIn(old, text, "test setup: text to replace not found")
        p.write_text(text.replace(old, new, 1), encoding="utf-8")

    def assertRefused(self, fragment):
        with self.assertRaises(bs.SeriesError) as ctx:
            bs.build(self.raw)
        self.assertIn(fragment, str(ctx.exception))

    def test_a_mistyped_daily_number_breaks_the_weekly_total(self):
        self.edit("news-20250922-05", "9月16日（火）;233,900", "9月16日（火）;233,090")
        self.assertRefused("add up to")

    def test_a_mistyped_ad_number_breaks_the_weekly_total(self):
        self.edit("news-20250922-05", "9月16日（火）;233,900;20,283", "9月16日（火）;233,900;20,238")
        self.assertRefused("daily AD add up to")

    def test_a_consistent_typo_in_one_post_breaks_the_cumulative_chain(self):
        # Change a day AND its weekly 合計 together: the post agrees with itself,
        # but the next post's printed cumulative no longer follows.
        self.edit("news-20250922-05", "9月16日（火）;233,900", "9月16日（火）;233,910")
        self.edit("news-20250922-05", "合計;1,658,841", "合計;1,658,851")
        self.assertRefused("cumulative")

    def test_a_wrong_weekday_is_refused(self):
        self.edit("news-20250922-05", "9月16日（火）", "9月16日（水）")
        self.assertRefused("printed as （水）")

    def test_a_missing_week_is_reported(self):
        (self.raw / "news-20250707-01.txt").unlink()
        self.assertRefused("cumulative")  # the chain notices first

    def test_a_missing_week_is_reported_as_missing_days_when_the_chain_is_skipped(self):
        (self.raw / "news-20250707-01.txt").unlink()
        posts = bs.load_posts(self.raw)
        with self.assertRaises(bs.SeriesError) as ctx:
            bs.check_complete(bs.merge(posts))
        self.assertIn("7 day(s) missing, first 2025-06-29", str(ctx.exception))

    def test_a_day_reported_twice_with_different_numbers_is_refused(self):
        dup = (self.raw / "news-20250922-05.txt").read_text(encoding="utf-8")
        dup = dup.replace("9月14日（日）;235,280", "9月14日（日）;235,281")
        post = bs.parse_post("dup", dup.replace("合計;1,658,841", "合計;1,658,842"))
        original = bs.parse_post("news-20250922-05", (self.raw / "news-20250922-05.txt").read_text(encoding="utf-8"))
        with self.assertRaises(bs.SeriesError) as ctx:
            bs.merge([original, post])
        self.assertIn("2025-09-14", str(ctx.exception))

    def test_a_day_reported_twice_with_the_same_numbers_is_kept_once(self):
        text = (self.raw / "news-20250922-05.txt").read_text(encoding="utf-8")
        a, b = bs.parse_post("a", text), bs.parse_post("b", text)
        merged = bs.merge([a, b])
        self.assertEqual(len(merged), 7)
        self.assertEqual(merged[dt.date(2025, 9, 14)][1].name, "a")

    def test_ad_larger_than_total_is_refused(self):
        text = (self.raw / "news-20250421-02.txt").read_text(encoding="utf-8")
        text = text.replace("4月14日（月）;70,488;16,828", "4月14日（月）;70,488;70,489")
        text = text.replace("合計;639,875;114,938", f"合計;639,875;{114_938 - 16_828 + 70_489:,}")
        with self.assertRaises(bs.SeriesError) as ctx:
            bs.parse_post("x", text)
        self.assertIn("not within total", str(ctx.exception))

    def test_rows_must_match_the_period_heading(self):
        self.edit("news-20250922-05", "9月14日～9月20日", "9月14日～9月21日")
        self.assertRefused("do not match period")

    def test_final_total_must_match(self):
        old = bs.FINAL_TOTAL
        try:
            bs.FINAL_TOTAL = old + 1
            self.assertRefused("official final")
        finally:
            bs.FINAL_TOTAL = old


class Holidays(unittest.TestCase):
    def setUp(self):
        with open(FORECAST_DIR / "data" / "jp_holidays_2025.csv", encoding="utf-8") as f:
            self.rows = list(csv.DictReader(f))

    def test_window_flag_matches_the_dates(self):
        for r in self.rows:
            d = dt.date.fromisoformat(r["date"])
            inside = bs.OPENING <= d <= bs.CLOSING
            self.assertEqual(r["in_expo_window"], "yes" if inside else "no", r["date"])

    def test_ten_holidays_fall_inside_the_expo(self):
        self.assertEqual(sum(r["in_expo_window"] == "yes" for r in self.rows), 10)

    def test_every_row_has_a_source(self):
        for r in self.rows:
            self.assertTrue(r["source_url"].startswith("https://www8.cao.go.jp/"))


if __name__ == "__main__":
    unittest.main()

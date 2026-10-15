"""F2 tests: models, metrics, folds, selection rule, and the guards around them.

Run from the repo root:  python -m unittest discover -s forecast/tests -v
Needs requirements-forecast.txt (numpy, scikit-learn). Most tests use synthetic
series with a known answer, so they check the method, not the Osaka numbers.
"""
import csv
import datetime as dt
import math
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

FORECAST_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FORECAST_DIR))

import evaluate as E  # noqa: E402
import metrics as M  # noqa: E402
import series as S  # noqa: E402
from models import calendar_regression as CR  # noqa: E402
from models import ordered  # noqa: E402
from models import seasonal_naive as SN  # noqa: E402

OPEN = dt.date(2025, 4, 13)
DOW_EFFECT = {0: 0.00, 1: -0.05, 2: 0.02, 3: 0.03, 4: 0.10, 5: 0.25, 6: 0.15}  # Mon..Sun, log scale


def synthetic(n=184, noise=0.0, holidays=(), seed=0, growth=0.004):
    """ln(y) = 11 + weekday effect + growth*t (+ optional noise). Known answer."""
    rng = np.random.default_rng(seed)
    dates = tuple(OPEN + dt.timedelta(days=i) for i in range(n))
    logy = np.array([11 + DOW_EFFECT[d.weekday()] + growth * i for i, d in enumerate(dates)])
    logy += rng.normal(0, noise, n) if noise else 0
    return S.Series(dates=dates, y=np.rint(np.exp(logy)).astype(np.int64), holidays=frozenset(holidays))


class SeasonalNaive(unittest.TestCase):
    def test_p50_is_same_weekday_last_week(self):
        s = synthetic(noise=0.05)
        fc = SN.forecast(s.upto(100), 100, 7)
        np.testing.assert_array_equal(fc.p50, s.y[94:101])  # days 101..107 minus 7

    def test_flat_weekly_pattern_gives_a_zero_width_interval(self):
        s = synthetic(growth=0.0)
        fc = SN.forecast(s.upto(80), 80, 7)
        np.testing.assert_array_equal(fc.p10, fc.p50)
        np.testing.assert_array_equal(fc.p90, fc.p50)

    def test_refuses_more_history_than_as_of(self):
        s = synthetic()
        with self.assertRaises(ValueError):
            SN.forecast(s.y[:120], 100, 7)


class CalendarRegression(unittest.TestCase):
    def test_recovers_an_exact_log_linear_series(self):
        s = synthetic()
        fc = CR.forecast(s.upto(100), 100, 7, s)
        np.testing.assert_allclose(fc.p50, s.y[101:108], atol=1)
        np.testing.assert_allclose(fc.p10, fc.p50, atol=1)
        np.testing.assert_allclose(fc.p90, fc.p50, atol=1)

    def test_holiday_effect_is_learned_when_the_window_has_holidays(self):
        hol = {OPEN + dt.timedelta(days=i) for i in (60, 75, 90, 103)}
        base = synthetic()
        y = base.y.astype(float)
        for d in hol:
            y[(d - OPEN).days] *= math.exp(0.3)
        s = S.Series(base.dates, np.rint(y).astype(np.int64), frozenset(hol))
        fc = CR.forecast(s.upto(100), 100, 7, s)
        self.assertAlmostEqual(fc.p50[2] / base.y[103], math.exp(0.3), delta=0.01)  # day 103 is a holiday
        self.assertEqual(fc.notes, [])

    def test_constant_holiday_column_is_dropped_and_noted(self):
        s = synthetic()  # no holidays at all
        fc = CR.forecast(s.upto(100), 100, 7, s)
        self.assertEqual(fc.notes, ["dropped holiday (constant in window)"])

    def test_uses_only_the_last_56_days(self):
        s = synthetic()
        y = s.y.copy()
        y[:40] = 1  # nonsense far in the past, outside the 56-day window ending at day 100
        s2 = S.Series(s.dates, y, s.holidays)
        a = CR.forecast(s.upto(100), 100, 7, s)
        b = CR.forecast(s2.upto(100), 100, 7, s2)
        np.testing.assert_array_equal(a.p50, b.p50)


class QuantileOrder(unittest.TestCase):
    def test_both_models_keep_p10_le_p50_le_p90_on_noisy_data(self):
        for seed in range(5):
            s = synthetic(noise=0.12, seed=seed)
            for model in (SN, CR):
                for as_of in E.SELECTION_ORIGINS:
                    fc = model.forecast(s.upto(as_of), as_of, 7, s)
                    self.assertTrue(np.all(fc.p10 <= fc.p50), (model.NAME, seed, as_of))
                    self.assertTrue(np.all(fc.p50 <= fc.p90), (model.NAME, seed, as_of))

    def test_ordered_moves_and_counts_crossed_quantiles(self):
        fc = ordered(np.array([110.0, 90.0]), np.array([100.0, 100.0]), np.array([95.0, 120.0]))
        np.testing.assert_array_equal(fc.p10, [100, 90])
        np.testing.assert_array_equal(fc.p90, [100, 120])
        self.assertEqual(fc.clipped, 2)


class NoLeakage(unittest.TestCase):
    def test_changing_the_future_changes_nothing_but_the_actuals(self):
        s = synthetic(noise=0.1)
        y = s.y.copy()
        y[101:] = y[101:] * 3 + 7
        s2 = S.Series(s.dates, y, s.holidays)
        for model in (SN, CR):
            a, b = E.run_fold(s, model, 100), E.run_fold(s2, model, 100)
            for ra, rb in zip(a, b):
                for k in ("p10", "p50", "p90", "t75", "t90", "level_pred", "as_of", "horizon_days"):
                    self.assertEqual(ra[k], rb[k], (model.NAME, k))
                self.assertNotEqual(ra["actual"], rb["actual"])

    def test_forecast_date_is_as_of_plus_horizon(self):
        s = synthetic()
        for r in E.run_fold(s, SN, 100):
            self.assertEqual(dt.date.fromisoformat(r["date"]) - dt.date.fromisoformat(r["as_of"]),
                             dt.timedelta(days=r["horizon_days"]))


class Folds(unittest.TestCase):
    def test_selection_folds_are_14_mondays_ending_on_15_september(self):
        s = synthetic()
        self.assertEqual(len(E.SELECTION_ORIGINS), 14)
        self.assertTrue(all(s.dates[o].weekday() == 0 for o in E.SELECTION_ORIGINS))
        self.assertEqual(s.dates[E.SELECTION_ORIGINS[0]], dt.date(2025, 6, 9))
        self.assertEqual(s.dates[E.SELECTION_ORIGINS[-1] + E.HORIZON], dt.date(2025, 9, 15))

    def test_selection_never_touches_a_final_test_day(self):
        last_selection_day = E.SELECTION_ORIGINS[-1] + E.HORIZON
        first_final_day = E.FINAL_ORIGINS[0] + 1
        self.assertLess(last_selection_day, first_final_day)
        self.assertEqual(synthetic().dates[first_final_day], dt.date(2025, 9, 16))

    def test_first_fold_has_enough_history_for_the_window(self):
        from models import WINDOW_DAYS
        self.assertGreaterEqual(E.SELECTION_ORIGINS[0] + 1, WINDOW_DAYS + 1)

    def test_final_test_is_sealed_without_a_valid_freeze(self):
        tmp = Path(tempfile.mkdtemp())
        old = E.FROZEN
        try:
            E.FROZEN = tmp / "selection_frozen.json"
            with self.assertRaises(E.SealedError):          # no freeze
                E.origins_for("final")
            E.FROZEN.write_text("{}")
            with self.assertRaises(E.SealedError):          # an empty file is not a freeze (F2 accepted it)
                E.origins_for("final")
        finally:
            E.FROZEN = old
            shutil.rmtree(tmp)

    def test_cli_refuses_the_final_test_without_a_valid_freeze_before_reading_data(self):
        # Tripwires: if the seal were broken, neither the series nor any fold could be touched.
        tmp = Path(tempfile.mkdtemp())
        old = (E.FROZEN, E.run, S.load)

        def tripwire(*a, **k):
            raise AssertionError("final-test data or folds touched without a valid freeze")

        E.FROZEN, E.run, S.load = tmp / "missing.json", tripwire, tripwire
        try:
            self.assertEqual(E.main(["--final"]), 2)
        finally:
            E.FROZEN, E.run, S.load = old
            shutil.rmtree(tmp)

    def test_final_test_outputs_are_bound_to_the_freeze_and_reproduce(self):
        # Until F4c this test asserted that forecast/out/final did not exist. Nouf
        # authorized F4c on 2026-10-15 and the outputs were committed as received
        # (e05c822). What must hold now: they report against the frozen selection,
        # never re-select, and reproduce exactly (no Chronos-2 inference here).
        import json

        status = json.loads((FORECAST_DIR / "out" / "final" / "final_status.json").read_text(encoding="utf-8"))
        frozen = json.loads(E.FROZEN.read_text(encoding="utf-8"))
        self.assertTrue(status["final_reporting_only"])
        self.assertEqual(status["frozen_selection"], frozen["selected_model"])
        self.assertEqual(status["freeze_sha256"], E.FZ.freeze_sha256(E.FROZEN))
        self.assertFalse((FORECAST_DIR / "out" / "final" / "selection.json").exists())
        self.assertEqual(E.main(["--final", "--check"]), 0)


class Metrics(unittest.TestCase):
    """Hand-computed: y = [100, 200], p50 = [110, 180]."""

    def test_definitions(self):
        y, p = np.array([100.0, 200.0]), np.array([110.0, 180.0])
        self.assertEqual(M.mae(y, p), 15.0)                      # (10 + 20) / 2
        self.assertAlmostEqual(M.wape(y, p), 30 / 300)          # 0.1
        self.assertAlmostEqual(M.bias(y, p), (10 - 20) / 300)   # forecast too low overall
        self.assertEqual(M.coverage(y, np.array([90.0, 201.0]), np.array([105.0, 250.0])), 0.5)

    def test_pooled_wape_is_not_the_mean_of_fold_wapes(self):
        rows = [{"actual": 100, "p10": 0, "p50": 150, "p90": 0, "level_actual": "normal", "level_pred": "normal"},
                {"actual": 1000, "p10": 0, "p50": 1000, "p90": 0, "level_actual": "normal", "level_pred": "normal"}]
        self.assertAlmostEqual(E._summ(rows)["wape"], 50 / 1100)  # not (0.5 + 0) / 2

    def test_level_boundaries_are_inclusive_upwards(self):
        cuts = (100.0, 200.0)
        self.assertEqual(M.level(99.9, cuts), "normal")
        self.assertEqual(M.level(100.0, cuts), "high")
        self.assertEqual(M.level(200.0, cuts), "peak")

    def test_thresholds_are_the_declared_quantiles(self):
        self.assertEqual(M.thresholds(range(1, 101)), (np.quantile(range(1, 101), 0.75), np.quantile(range(1, 101), 0.90)))


class SelectionRule(unittest.TestCase):
    def table(self, naive, reg):
        return [{"model": "seasonal_naive", "slice": "pooled", "wape": naive},
                {"model": "calendar_regression", "slice": "pooled", "wape": reg}]

    def test_challenger_wins_by_at_least_one_point(self):
        self.assertEqual(E.select(self.table(0.100, 0.090))["status"], "model")

    def test_less_than_one_point_falls_back_to_the_simpler_model(self):
        sel = E.select(self.table(0.100, 0.0905))
        self.assertEqual((sel["selected"], sel["status"]), ("seasonal_naive", "fallback_model"))

    def test_worse_challenger_falls_back(self):
        self.assertEqual(E.select(self.table(0.10, 0.20))["selected"], "seasonal_naive")

    def test_peak_slices_do_not_enter_selection(self):
        t = self.table(0.10, 0.12) + [{"model": "calendar_regression", "slice": "peak_report_only", "wape": 0.0}]
        self.assertEqual(E.select(t)["selected"], "seasonal_naive")


class OfficialDataOnly(unittest.TestCase):
    def test_loader_refuses_a_non_official_row(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            src = S.SERIES_CSV.read_text(encoding="utf-8").splitlines()
            src[5] = src[5].replace(",official,", ",secondary,")
            (tmp / "s.csv").write_text("\n".join(src) + "\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                S.load(tmp / "s.csv")
        finally:
            shutil.rmtree(tmp)

    def test_real_series_loads_184_official_days(self):
        s = S.load()
        self.assertEqual(len(s), 184)
        self.assertEqual(int(s.y.sum()), 25_578_986)


class SampleReread(unittest.TestCase):
    def test_every_resampled_day_matches_the_series(self):
        path = FORECAST_DIR / "data" / "verification" / "sample_reread_2026-10-14.csv"
        with open(path, encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(len(rows), 26)
        self.assertEqual(len({r["source_url"] for r in rows}), 26)  # one per post
        with open(S.SERIES_CSV, encoding="utf-8") as f:
            series = {r["date"]: r for r in csv.DictReader(f)}
        for r in rows:
            self.assertEqual(r["reread_total"], series[r["date"]]["visitors_total"], r["date"])
            self.assertEqual(r["reread_ad"], series[r["date"]]["ad_pass_entries"], r["date"])


if __name__ == "__main__":
    unittest.main()


class Reproducible(unittest.TestCase):
    def test_committed_f2_outputs_reproduce_exactly(self):
        self.assertEqual(E.main(["--check"]), 0)

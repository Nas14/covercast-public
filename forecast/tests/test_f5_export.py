"""F5 tests: the Osaka replay export for Django (forecast/export_forecast.py).

The look-ahead tests use a SYNTHETIC series, so the rule they check (thresholds
only from data up to as_of) is shown on numbers whose answer is known.
"""
import dataclasses
import datetime as dt
import json
import sys
import unittest
from pathlib import Path

import numpy as np

FORECAST_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FORECAST_DIR))

import export_forecast as X  # noqa: E402
import freeze as FZ  # noqa: E402
import metrics as M  # noqa: E402
import series as S  # noqa: E402

OPEN = dt.date(2025, 4, 13)


def synthetic_series():
    dates = tuple(OPEN + dt.timedelta(days=i) for i in range(184))
    y = np.array([100_000 + 500 * i for i in range(184)], dtype=np.int64)
    return S.Series(dates=dates, y=y, holidays=frozenset())


def row_for(series, as_of, h, p50):
    cuts = M.thresholds(series.upto(as_of))
    return {"model": "seasonal_naive", "as_of": series.dates[as_of].isoformat(), "as_of_index": str(as_of),
            "date": series.dates[as_of + h].isoformat(), "day_index": str(as_of + h), "horizon_days": str(h),
            "p10": str(p50 - 1000), "p50": str(p50), "p90": str(p50 + 1000), "actual": str(series.y[as_of + h]),
            "t75": f"{round(cuts[0], 1):.6f}", "t90": f"{round(cuts[1], 1):.6f}",
            "level_pred": M.level(p50, cuts)}


class NoLookAhead(unittest.TestCase):
    def test_thresholds_use_only_days_up_to_as_of(self):
        s = synthetic_series()
        row = row_for(s, 100, 3, 160_000)
        before = X.day_entry(s, row)
        # change every value AFTER as_of except the target day: thresholds must not move
        y = s.y.copy()
        y[101:] += 1_000_000
        y[103] = s.y[103]
        later = dataclasses.replace(s, y=y)
        after = X.day_entry(later, row)
        self.assertEqual((before["threshold_high"], before["threshold_peak"], before["level"]),
                         (after["threshold_high"], after["threshold_peak"], after["level"]))

    def test_threshold_value_is_the_protocol_quantile_up_to_and_including_as_of(self):
        s = synthetic_series()
        e = X.day_entry(s, row_for(s, 60, 1, 120_000))
        hist = 100_000 + 500 * np.arange(61)
        self.assertAlmostEqual(e["threshold_high"], round(float(np.quantile(hist, 0.75)), 2))
        self.assertAlmostEqual(e["threshold_peak"], round(float(np.quantile(hist, 0.90)), 2))


class Guards(unittest.TestCase):
    def setUp(self):
        self.s = synthetic_series()

    def test_day_must_be_as_of_plus_horizon(self):
        row = row_for(self.s, 80, 2, 140_000)
        row["day_index"] = "83"
        with self.assertRaises(X.ExportError):
            X.day_entry(self.s, row)

    def test_horizon_outside_1_to_7_is_refused(self):
        row = row_for(self.s, 80, 2, 140_000)
        row["horizon_days"] = "8"
        row["day_index"] = "88"
        with self.assertRaises(X.ExportError):
            X.day_entry(self.s, row)

    def test_level_must_agree_with_the_evaluated_artifact(self):
        row = row_for(self.s, 80, 2, 140_000)
        row["level_pred"] = "normal" if row["level_pred"] != "normal" else "peak"
        with self.assertRaises(X.ExportError):
            X.day_entry(self.s, row)

    def test_threshold_must_agree_with_the_evaluated_artifact(self):
        row = row_for(self.s, 80, 2, 140_000)
        row["t75"] = str(float(row["t75"]) + 5)
        with self.assertRaises(X.ExportError):
            X.day_entry(self.s, row)

    def test_actual_must_be_the_official_value(self):
        row = row_for(self.s, 80, 2, 140_000)
        row["actual"] = "1"
        with self.assertRaises(X.ExportError):
            X.day_entry(self.s, row)


class CommittedExport(unittest.TestCase):
    """The committed file: frozen model only, official, 126 distinct 2025 days, reproducible."""

    @classmethod
    def setUpClass(cls):
        cls.doc = json.loads(X.OUT.read_text(encoding="utf-8"))
        cls.frozen = FZ.verify_freeze(FZ.FROZEN)

    def test_reproduces_exactly(self):
        self.assertEqual(X.main(["--check"]), 0)

    def test_run_is_the_frozen_selection_and_official(self):
        run = self.doc["run"]
        self.assertEqual(run["model"], self.frozen["selected_model"])
        self.assertEqual(run["model"], "seasonal_naive")
        self.assertEqual((run["mode"], run["data_origin"]), ("osaka_replay", "official"))
        self.assertEqual(run["freeze_sha256"], FZ.freeze_sha256())
        self.assertNotIn("chronos", json.dumps(self.doc))

    def test_days(self):
        days = self.doc["days"]
        self.assertEqual(len(days), 126)
        self.assertEqual(len({d["date"] for d in days}), 126)
        self.assertEqual((days[0]["date"], days[-1]["date"]), ("2025-06-10", "2025-10-13"))
        for d in days:
            self.assertLessEqual(d["p10"], d["p50"])
            self.assertLessEqual(d["p50"], d["p90"])
            self.assertLess(d["as_of"], d["date"])
            self.assertEqual(dt.date.fromisoformat(d["date"]) - dt.date.fromisoformat(d["as_of"]),
                             dt.timedelta(days=d["horizon_days"]))


if __name__ == "__main__":
    unittest.main()

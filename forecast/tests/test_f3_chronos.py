"""F3 tests: the Chronos-2 adapter, the local runner, and the ingestion guards.

Most tests use a fake pipeline, so they run without torch or the model (cloud and
Windows alike). One test builds a tiny *random* Chronos-2 and saves it to a folder,
then runs the real runner on it; it is skipped when chronos is not installed.
None of these tests downloads the real model, and none touches the final test.
"""
import csv
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

FORECAST_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FORECAST_DIR))

import evaluate as E  # noqa: E402
import run_chronos_local as R  # noqa: E402
import series as S  # noqa: E402
from models import chronos2 as C2  # noqa: E402

try:
    import chronos  # noqa: F401
    import torch  # noqa: F401
    HAVE_CHRONOS = True
except Exception:  # noqa: BLE001
    HAVE_CHRONOS = False


class FakePipeline:
    """Records what it was given; answers last value x (0.9, 1.0, 1.1)."""

    def __init__(self, shape3d=True, cross=False, nan=False, drift=0.0):
        self.calls, self.shape3d, self.cross, self.nan, self.drift = [], shape3d, cross, nan, drift

    def predict_quantiles(self, inputs, prediction_length, quantile_levels):
        self.calls.append({"inputs": inputs, "prediction_length": prediction_length,
                           "quantile_levels": quantile_levels})
        last = float(inputs[0][-1]) * (1 + self.drift * len(self.calls))
        lo, hi = (1.1, 0.9) if self.cross else (0.9, 1.1)
        arr = np.array([[last * lo, last, last * hi]] * prediction_length)
        if self.nan:
            arr[0, 0] = np.nan
        return [arr[None, ...] if self.shape3d else arr], [arr[:, 1]]


def series():
    return S.load()


def fake_run(tmp: Path) -> Path:
    """Run the real runner end to end with a fake pipeline and fake weights; return its out folder."""
    old = (R.resolve_model_dir, R.load_pipeline, R.sha256_file)
    weights = tmp / "model"
    weights.mkdir()
    (weights / "model.safetensors").write_bytes(b"fake")
    R.resolve_model_dir = lambda p: (weights, "fake-revision")
    R.load_pipeline = lambda d: FakePipeline()
    R.sha256_file = lambda p: C2.WEIGHTS_SHA256      # pretend the fake file is the pinned weights
    try:
        if R.main(["--out", str(tmp / "out")]) != 0:
            raise AssertionError("runner failed")
    finally:
        R.resolve_model_dir, R.load_pipeline, R.sha256_file = old
    return tmp / "out"


class Adapter(unittest.TestCase):
    def test_sends_exactly_the_history_and_the_declared_quantiles(self):
        s, fake = series(), FakePipeline()
        C2.Chronos2Forecaster(fake).forecast(s.upto(100), 100, 7, s)
        call = fake.calls[0]
        self.assertEqual(len(call["inputs"]), 1)                       # one univariate series
        np.testing.assert_array_equal(call["inputs"][0], s.y[:101].astype(np.float32))
        self.assertEqual(call["inputs"][0].dtype, np.float32)
        self.assertEqual(call["prediction_length"], 7)
        self.assertEqual(call["quantile_levels"], [0.1, 0.5, 0.9])

    def test_maps_quantiles_to_p10_p50_p90(self):
        s = series()
        fc = C2.Chronos2Forecaster(FakePipeline()).forecast(s.upto(100), 100, 7, s)
        last = float(s.y[100])
        np.testing.assert_array_equal(fc.p50, np.rint([last] * 7))
        np.testing.assert_array_equal(fc.p10, np.rint([last * 0.9] * 7))
        np.testing.assert_array_equal(fc.p90, np.rint([last * 1.1] * 7))

    def test_accepts_a_2d_answer(self):
        s = series()
        fc = C2.Chronos2Forecaster(FakePipeline(shape3d=False)).forecast(s.upto(100), 100, 7, s)
        self.assertEqual(fc.p50.shape, (7,))

    def test_crossed_quantiles_are_ordered_and_counted(self):
        s = series()
        fc = C2.Chronos2Forecaster(FakePipeline(cross=True)).forecast(s.upto(100), 100, 7, s)
        self.assertTrue(np.all(fc.p10 <= fc.p50) and np.all(fc.p50 <= fc.p90))
        self.assertEqual(fc.clipped, 14)

    def test_non_finite_output_is_refused(self):
        s = series()
        with self.assertRaises(ValueError):
            C2.Chronos2Forecaster(FakePipeline(nan=True)).forecast(s.upto(100), 100, 7, s)

    def test_wrong_history_length_is_refused(self):
        s = series()
        with self.assertRaises(ValueError):
            C2.Chronos2Forecaster(FakePipeline()).forecast(s.y[:120], 100, 7, s)

    def test_the_future_never_reaches_the_model(self):
        s = series()
        y = s.y.copy()
        y[101:] = 1
        s2 = S.Series(s.dates, y, s.holidays)
        a, b = FakePipeline(), FakePipeline()
        E.run_fold(s, C2.Chronos2Forecaster(a), 100)
        E.run_fold(s2, C2.Chronos2Forecaster(b), 100)
        np.testing.assert_array_equal(a.calls[0]["inputs"][0], b.calls[0]["inputs"][0])


class Runner(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_grid_is_the_14_selection_folds_and_never_the_final_test(self):
        grid = R.selection_grid()
        self.assertEqual(len(grid), 98)
        self.assertLess(max(o + h for o, h in grid), E.FINAL_ORIGINS[0] + 1)

    def test_a_non_deterministic_model_stops_the_run(self):
        with self.assertRaises(SystemExit):
            R.run_folds(series(), C2.Chronos2Forecaster(FakePipeline(drift=0.01)))

    def run_fake(self):
        return fake_run(self.tmp)

    def test_runner_writes_98_rows_and_a_complete_manifest(self):
        out = self.run_fake()
        with open(out / "chronos2_predictions.csv", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(len(rows), 98)
        self.assertNotIn("actual", rows[0])                       # forecasts only, no metrics
        man = json.loads((out / "run_manifest.json").read_text(encoding="utf-8"))
        for k in ("spec_version", "revision_used", "weights_sha256", "input_sha256_lf",
                  "determinism_check", "fold_seconds", "environment"):
            self.assertIn(k, man)
        self.assertEqual(man["spec_version"], "chronos2-spec-v1")
        self.assertEqual(len(man["fold_seconds"]), 14)

    def test_runner_output_is_accepted_and_evaluated_with_the_same_code(self):
        out = self.run_fake()
        model, status = E.load_chronos(series(), out)
        self.assertEqual(status["chronos2"], "included")
        files = E.build(series(), "selection", E.MODELS + (model,), status)
        sel = json.loads(files["selection.json"])
        self.assertEqual(set(sel["pooled_wape"]), {"seasonal_naive", "calendar_regression", "chronos2"})
        self.assertIn("comparison complete", files["evaluation_report.md"])
        # the two existing models are unchanged by adding a third
        f2 = E.build(series(), "selection")
        for line in f2["predictions.csv"].splitlines()[1:]:
            self.assertIn(line, files["predictions.csv"])

    def test_record_unavailable_needs_no_torch_and_marks_the_comparison_incomplete(self):
        out = self.tmp / "out"
        self.assertEqual(R.main(["--record-unavailable", "download blocked by proxy", "--out", str(out)]), 0)
        model, status = E.load_chronos(series(), out)
        self.assertIsNone(model)
        self.assertEqual((status["chronos2"], status["comparison"]), ("unavailable", "incomplete"))
        files = E.build(series(), "selection", E.MODELS, status)
        self.assertIn("INCOMPLETE", files["evaluation_report.md"])
        self.assertIn("download blocked by proxy", files["evaluation_report.md"])

    def test_nothing_run_yet_is_also_incomplete(self):
        model, status = E.load_chronos(series(), self.tmp / "empty")
        self.assertIsNone(model)
        self.assertEqual(status["comparison"], "incomplete")

    def test_other_weights_stop_the_run(self):
        old = (R.resolve_model_dir, R.load_pipeline)
        weights = self.tmp / "model"
        weights.mkdir()
        (weights / "model.safetensors").write_bytes(b"not the pinned weights")
        R.resolve_model_dir = lambda p: (weights, "x")
        R.load_pipeline = lambda d: self.fail("must stop before loading")
        try:
            with self.assertRaises(SystemExit) as ctx:
                R.main(["--out", str(self.tmp / "out")])
            self.assertIn("not the file the spec pins", str(ctx.exception))
            self.assertFalse((self.tmp / "out").exists())
        finally:
            R.resolve_model_dir, R.load_pipeline = old

    def test_runner_final_mode_refuses_without_a_valid_freeze_before_reading_data(self):
        old = (R.FZ.FROZEN, S.load, R.resolve_model_dir)

        def tripwire(*a, **k):
            raise AssertionError("series or model touched before the freeze check")

        R.FZ.FROZEN, S.load, R.resolve_model_dir = self.tmp / "missing.json", tripwire, tripwire
        try:
            with self.assertRaises(SystemExit) as ctx:
                R.main(["--final", "--out", str(self.tmp / "out")])
            self.assertIn("sealed", str(ctx.exception))
            self.assertFalse((self.tmp / "out").exists())
        finally:
            R.FZ.FROZEN, S.load, R.resolve_model_dir = old

    def test_spec_version_is_the_same_on_both_sides(self):
        self.assertEqual(E.CHRONOS_SPEC, C2.SPEC_VERSION)

    def test_input_hash_ignores_windows_line_endings(self):
        a, b = self.tmp / "a.csv", self.tmp / "b.csv"
        a.write_bytes(b"x,y\n1,2\n")
        b.write_bytes(b"x,y\r\n1,2\r\n")
        self.assertEqual(R.sha256_text_file(a), R.sha256_text_file(b))


class IngestGuards(unittest.TestCase):
    """Each off-spec change to valid runner output is refused."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.out = fake_run(self.tmp)
        self.preds = self.out / "chronos2_predictions.csv"
        self.man = self.out / "run_manifest.json"

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def rows(self):
        with open(self.preds, encoding="utf-8") as f:
            return list(csv.DictReader(f))

    def write(self, rows):
        with open(self.preds, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]), lineterminator="\n")
            w.writeheader()
            w.writerows(rows)

    def manifest(self, **changes):
        m = json.loads(self.man.read_text(encoding="utf-8"))
        m.update(changes)
        self.man.write_text(json.dumps(m), encoding="utf-8")

    def refused(self, fragment):
        with self.assertRaises(E.IngestError) as ctx:
            E.load_chronos(series(), self.out)
        self.assertIn(fragment, str(ctx.exception))

    def test_valid_output_is_accepted(self):
        self.assertIsNotNone(E.load_chronos(series(), self.out)[0])

    def test_missing_manifest(self):
        self.man.unlink()
        self.refused("run_manifest.json is missing")

    def test_other_spec_version(self):
        self.manifest(spec_version="chronos2-spec-v2")
        self.refused("spec_version")

    def test_different_input_series(self):
        self.manifest(input_sha256_lf="0" * 64)
        self.refused("differs")

    def test_failed_determinism(self):
        self.manifest(determinism_check="failed")
        self.refused("determinism")

    def test_missing_row(self):
        self.write(self.rows()[:-1])
        self.refused("do not match the 14 selection folds")

    def test_a_final_test_row_is_refused(self):
        rows = self.rows()
        r = dict(rows[-1])
        r["as_of_index"] = str(E.FINAL_ORIGINS[0])
        self.write(rows + [r])
        self.refused("do not match the 14 selection folds")

    def test_duplicate_row(self):
        rows = self.rows()
        self.write(rows[:-1] + [rows[0]])
        self.refused("duplicate")

    def test_date_not_as_of_plus_horizon(self):
        rows = self.rows()
        rows[3]["date"] = "2025-01-01"
        self.write(rows)
        self.refused("as_of + horizon")

    def test_quantiles_out_of_order(self):
        rows = self.rows()
        rows[5]["p10"] = str(int(rows[5]["p50"]) + 1)
        self.write(rows)
        self.refused("out of order")

    def test_wrong_model_name(self):
        rows = self.rows()
        rows[0]["model"] = "seasonal_naive"
        self.write(rows)
        self.refused("model")

    def test_cli_returns_3_on_refusal(self):
        self.manifest(spec_version="x")
        old = E.CHRONOS_DIR
        E.CHRONOS_DIR = self.out
        try:
            self.assertEqual(E.main(["--stage", "f3", "--check"]), 3)
        finally:
            E.CHRONOS_DIR = old


@unittest.skipUnless(HAVE_CHRONOS, "chronos-forecasting not installed (cloud or Django environment)")
class RealLibrary(unittest.TestCase):
    """The real chronos package, a tiny random model saved to a folder: the manual-download route."""

    def test_runner_on_a_saved_tiny_model_produces_valid_files(self):
        import torch
        from chronos.chronos2.config import Chronos2CoreConfig
        from chronos.chronos2.model import Chronos2Model

        tmp = Path(tempfile.mkdtemp())
        try:
            torch.manual_seed(0)
            q = [0.01, 0.05] + [round(0.1 * i, 1) for i in range(1, 10)] + [0.95, 0.99]
            cfg = Chronos2CoreConfig(
                d_model=32, d_kv=8, d_ff=64, num_layers=1, num_heads=4, architectures=["Chronos2Model"],
                chronos_config={"context_length": 256, "output_patch_size": 16, "input_patch_size": 16,
                                "input_patch_stride": 16, "quantiles": q, "use_reg_token": True,
                                "use_arcsinh": True, "max_output_patches": 4})
            Chronos2Model(cfg).save_pretrained(tmp / "tiny")
            self.assertTrue((tmp / "tiny" / "model.safetensors").exists())
            old = C2.WEIGHTS_SHA256
            C2.WEIGHTS_SHA256 = R.sha256_file(tmp / "tiny" / "model.safetensors")  # random tiny weights
            try:
                self.assertEqual(R.main(["--model-path", str(tmp / "tiny"), "--out", str(tmp / "out")]), 0)
            finally:
                C2.WEIGHTS_SHA256 = old
            model, status = E.load_chronos(series(), tmp / "out")
            self.assertEqual(status["chronos2"], "included")
            self.assertEqual(json.loads((tmp / "out" / "run_manifest.json").read_text())["revision_used"],
                             "local-folder")
        finally:
            shutil.rmtree(tmp)

    def test_missing_files_in_a_manual_folder_are_explained(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            with self.assertRaises(SystemExit) as ctx:
                R.resolve_model_dir(str(tmp))
            self.assertIn("missing", str(ctx.exception))
        finally:
            shutil.rmtree(tmp)


if __name__ == "__main__":
    unittest.main()


class CommittedF3Results(unittest.TestCase):
    """Nouf's real Chronos-2 run (14 Oct 2026), as committed in forecast/out/f3/."""

    def test_real_run_matches_the_spec(self):
        man = json.loads((E.CHRONOS_DIR / "run_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(man["revision_used"], C2.MODEL_REVISION)
        self.assertEqual(man["weights_sha256"], C2.WEIGHTS_SHA256)
        self.assertEqual(man["determinism_check"], "passed")
        self.assertEqual(man["environment"]["chronos"], "2.3.2")

    def test_real_run_passes_every_ingestion_guard_and_stays_out_of_the_holdout(self):
        model, status = E.load_chronos(series(), E.CHRONOS_DIR)
        self.assertEqual(status["comparison"], "complete")
        last_day = max(r["day_index"] for rows in model.table.values() for r in map(lambda x: {"day_index": int(x["day_index"])}, rows))
        self.assertEqual(last_day, E.SELECTION_ORIGINS[-1] + E.HORIZON)   # 15 Sep 2025, day 155
        self.assertLess(last_day, E.FINAL_ORIGINS[0] + 1)

    def test_f3_outputs_reproduce_exactly(self):
        self.assertEqual(E.main(["--stage", "f3", "--check"]), 0)

    def test_exploratory_breakdown_reproduces(self):
        import explore_f3
        self.assertEqual(explore_f3.main(["--check"]), 0)

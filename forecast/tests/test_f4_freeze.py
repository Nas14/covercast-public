"""F4a/F4b tests: the selection freeze and every gate in front of the final test.

Holdout rule for these tests: no test reads a final-test target. Anything that runs the
final folds uses a SYNTHETIC series (dates of the Expo, made-up attendance), and every
path that could touch the real series before a valid freeze carries a tripwire.
"""
import contextlib
import datetime as dt
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
import freeze as FZ  # noqa: E402
import run_chronos_local as R  # noqa: E402
import series as S  # noqa: E402
from models import chronos2 as C2  # noqa: E402

OPEN = dt.date(2025, 4, 13)


def synthetic_series():
    """The Expo's 184 dates with made-up attendance: lets the final folds run without real targets."""
    dates = tuple(OPEN + dt.timedelta(days=i) for i in range(184))
    y = np.array([100_000 + 500 * i + (20_000 if d.weekday() == 5 else 0) for i, d in enumerate(dates)],
                 dtype=np.int64)
    return S.Series(dates=dates, y=y, holidays=frozenset())


def tripwire(*a, **k):
    raise AssertionError("touched something that must stay closed")


class FakePipeline:
    def predict_quantiles(self, inputs, prediction_length, quantile_levels):
        last = float(inputs[0][-1])
        arr = np.array([[last * 0.9, last, last * 1.1]] * prediction_length)
        return [arr[None, ...]], [arr[:, 1]]


@contextlib.contextmanager
def freeze_at(path: Path):
    """Point every module at a freeze file at `path` (missing, broken or valid)."""
    old = (FZ.FROZEN, E.FROZEN)
    FZ.FROZEN = E.FROZEN = path
    try:
        yield
    finally:
        FZ.FROZEN, E.FROZEN = old


def write_valid_freeze(folder: Path) -> Path:
    path = folder / "selection_frozen.json"
    path.write_text(json.dumps(FZ.build_freeze("test-time"), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def copy_tree(dst: Path) -> Path:
    """A copy of the repo parts the freeze pins, to tamper with safely."""
    for rel in FZ.PINNED_LOGIC:
        (dst / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(FZ.REPO / rel, dst / rel)
    for d in FZ.ARTIFACT_DIRS:
        shutil.copytree(FZ.REPO / d, dst / d)
    (dst / "forecast" / "README.md").write_text("docs are not pinned\n", encoding="utf-8")
    return dst


class FreezeContent(unittest.TestCase):
    def setUp(self):
        self.f = FZ.build_freeze("test-time")

    def test_records_the_protocol_selection_and_not_more(self):
        self.assertEqual((self.f["selected_model"], self.f["status"]), ("seasonal_naive", "fallback_model"))
        self.assertEqual(self.f["lowest_numerical_wape"]["model"], "chronos2")
        self.assertIn("not the most accurate", self.f["what_this_is_not"])
        self.assertIn("design assumption", self.f["threshold_provenance"])
        self.assertIn("Neither wording is changed", self.f["plan_protocol_discrepancy"])
        self.assertTrue(self.f["final_reporting_only"])

    def test_selection_matches_the_f3_record_exactly(self):
        sel = json.loads((FZ.REPO / "forecast/out/f3/selection.json").read_text(encoding="utf-8"))
        self.assertEqual(self.f["selection_fold_pooled_wape"], sel["pooled_wape"])
        self.assertEqual(self.f["selected_model"], sel["selected"])
        self.assertEqual(self.f["rule"]["min_improvement"], E.MIN_IMPROVEMENT)

    def test_pins_logic_data_protocol_and_every_f2_f3_artifact(self):
        for rel in ("forecast/evaluate.py", "forecast/run_chronos_local.py", "forecast/freeze.py",
                    "forecast/data/osaka_daily.csv", "forecast/EVALUATION_PROTOCOL.md", "forecast/CHRONOS2_SPEC.md"):
            self.assertIn(rel, self.f["pinned_files"])
        self.assertIn("forecast/out/f3/chronos2/chronos2_predictions.csv", self.f["artifacts"])
        self.assertIn("forecast/out/f2/selection.json", self.f["artifacts"])
        self.assertFalse(any("/tests/" in p or p.endswith("README.md") for p in self.f["pinned_files"]))

    def test_final_folds_and_models_are_declared(self):
        self.assertEqual(tuple(self.f["final_origins"].values()), E.FINAL_ORIGINS)
        self.assertEqual(self.f["models_for_final_reporting"], ["seasonal_naive", "calendar_regression", "chronos2"])
        self.assertEqual(self.f["chronos2"]["weights_sha256"], C2.WEIGHTS_SHA256)

    def test_protocol_is_the_preregistered_one(self):
        # sha256 (LF) of EVALUATION_PROTOCOL.md as committed in 47b580f; it has not changed since.
        self.assertEqual(self.f["pinned_files"]["forecast/EVALUATION_PROTOCOL.md"], FZ.sha256_lf(
            FZ.REPO / "forecast/EVALUATION_PROTOCOL.md"))


class FreezeVerification(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_a_fresh_freeze_verifies(self):
        self.assertEqual(FZ.verify_freeze(write_valid_freeze(self.tmp))["selected_model"], "seasonal_naive")

    def test_missing_empty_or_wrong_version_is_refused(self):
        with self.assertRaises(FZ.FreezeError):
            FZ.verify_freeze(self.tmp / "none.json")
        (self.tmp / "a.json").write_text("{}")
        with self.assertRaises(FZ.FreezeError):
            FZ.verify_freeze(self.tmp / "a.json")
        (self.tmp / "b.json").write_text("not json")
        with self.assertRaises(FZ.FreezeError):
            FZ.verify_freeze(self.tmp / "b.json")

    def test_windows_line_endings_do_not_break_the_freeze(self):
        root = copy_tree(self.tmp / "repo")
        frozen = write_valid_freeze(self.tmp)
        for rel in ("forecast/evaluate.py", "forecast/data/osaka_daily.csv", "forecast/out/f3/selection.json"):
            p = root / rel
            p.write_bytes(p.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
        FZ.verify_freeze(frozen, root)

    def test_unpinned_files_do_not_matter(self):
        root = copy_tree(self.tmp / "repo")
        (root / "forecast" / "README.md").write_text("edited\n", encoding="utf-8")
        FZ.verify_freeze(write_valid_freeze(self.tmp), root)

    def test_a_changed_byte_of_logic_breaks_it(self):
        root = copy_tree(self.tmp / "repo")
        p = root / "forecast/models/seasonal_naive.py"
        p.write_text(p.read_text(encoding="utf-8").replace("0.10, 0.90", "0.10, 0.91"), encoding="utf-8")
        with self.assertRaises(FZ.FreezeError) as ctx:
            FZ.verify_freeze(write_valid_freeze(self.tmp), root)
        self.assertIn("seasonal_naive.py (changed)", str(ctx.exception))

    def test_a_changed_f3_artifact_breaks_it(self):
        root = copy_tree(self.tmp / "repo")
        p = root / "forecast/out/f3/chronos2/chronos2_predictions.csv"
        p.write_text(p.read_text(encoding="utf-8").replace("120238", "120239", 1), encoding="utf-8")
        with self.assertRaises(FZ.FreezeError) as ctx:
            FZ.verify_freeze(write_valid_freeze(self.tmp), root)
        self.assertIn("chronos2_predictions.csv (changed)", str(ctx.exception))

    def test_an_added_artifact_breaks_it(self):
        root = copy_tree(self.tmp / "repo")
        (root / "forecast/out/f3/new.csv").write_text("x\n", encoding="utf-8")
        with self.assertRaises(FZ.FreezeError) as ctx:
            FZ.verify_freeze(write_valid_freeze(self.tmp), root)
        self.assertIn("not in freeze", str(ctx.exception))

    def test_editing_the_selection_inside_the_freeze_breaks_it(self):
        path = write_valid_freeze(self.tmp)
        data = json.loads(path.read_text(encoding="utf-8"))
        data["selected_model"] = "chronos2"
        path.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaises(FZ.FreezeError) as ctx:
            FZ.verify_freeze(path)
        self.assertIn("selected_model", str(ctx.exception))

    def test_write_refuses_a_second_freeze(self):
        with freeze_at(write_valid_freeze(self.tmp)):
            self.assertEqual(FZ.main(["--write", "--frozen-at", "x"]), 2)


class RunnerHoldoutSafety(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_forecast_rows_carry_no_target_and_ignore_the_future(self):
        s = synthetic_series()
        y = s.y.copy()
        y[101:] = -1                                   # anything after as_of is garbage
        s2 = S.Series(s.dates, y, s.holidays)
        a = R.forecast_rows(s, C2.Chronos2Forecaster(FakePipeline()), 100)
        b = R.forecast_rows(s2, C2.Chronos2Forecaster(FakePipeline()), 100)
        self.assertEqual(a, b)
        self.assertNotIn("actual", a[0])

    def test_runner_never_uses_the_metric_path(self):
        old = E.run_fold
        E.run_fold = tripwire
        try:
            rows, _ = R.run_folds(synthetic_series(), C2.Chronos2Forecaster(FakePipeline()), E.SELECTION_ORIGINS)
            self.assertEqual(len(rows), 98)
        finally:
            E.run_fold = old

    def run_final_fake(self, freeze_path: Path) -> Path:
        weights = self.tmp / "model"
        weights.mkdir(exist_ok=True)
        (weights / "model.safetensors").write_bytes(b"fake")
        old = (R.resolve_model_dir, R.load_pipeline, R.sha256_file, S.load)
        R.resolve_model_dir = lambda p: (weights, "fake-revision")
        R.load_pipeline = lambda d: FakePipeline()
        R.sha256_file = lambda p: C2.WEIGHTS_SHA256
        S.load = synthetic_series                      # the real targets never enter
        try:
            with freeze_at(freeze_path):
                self.assertEqual(R.main(["--final", "--out", str(self.tmp / "out")]), 0)
        finally:
            R.resolve_model_dir, R.load_pipeline, R.sha256_file, S.load = old
        return self.tmp / "out"

    def test_final_run_after_a_valid_freeze_records_full_provenance(self):
        out = self.run_final_fake(write_valid_freeze(self.tmp))
        man = json.loads((out / "run_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(man["stage"], "final")
        self.assertEqual(man["rows"], 28)
        self.assertEqual(man["folds"], ["2025-09-15", "2025-09-22", "2025-09-29", "2025-10-06"])
        self.assertEqual(man["freeze_sha256"], FZ.sha256_lf(self.tmp / "selection_frozen.json"))
        self.assertEqual(man["pinned_check"], "passed")
        self.assertRegex(man["git"]["commit"], r"^[0-9a-f]{40}$")
        self.assertIn("tree_clean", man["git"])
        for k in ("input_sha256_lf", "weights_sha256", "revision_used", "environment"):
            self.assertIn(k, man)

    def test_final_ingestion_accepts_it_and_refuses_each_off_spec_change(self):
        frozen = write_valid_freeze(self.tmp)
        out = self.run_final_fake(frozen)
        s = synthetic_series()
        with freeze_at(frozen):
            self.assertIsNotNone(E.load_chronos(s, out, stage="final")[0])
            man_path = out / "run_manifest.json"
            good = man_path.read_text(encoding="utf-8")
            for change, words in (({"stage": "selection"}, "stage"), ({"freeze_sha256": "0" * 64}, "different freeze"),
                                  ({"pinned_check": "failed"}, "pinned"), ({"git": {"commit": None}}, "git commit")):
                man = json.loads(good)
                man.update(change)
                man_path.write_text(json.dumps(man), encoding="utf-8")
                with self.assertRaises(E.IngestError) as ctx:
                    E.load_chronos(s, out, stage="final")
                self.assertIn(words, str(ctx.exception))
            man_path.write_text(good, encoding="utf-8")
            # final files can never pass as selection files, and vice versa
            with self.assertRaises(E.IngestError):
                E.load_chronos(s, out, stage="selection")
            with self.assertRaises(E.IngestError):
                E.load_chronos(s, E.CHRONOS_DIR, stage="final")

    def test_record_unavailable_for_the_final_also_needs_the_freeze(self):
        with freeze_at(self.tmp / "missing.json"):
            with self.assertRaises(SystemExit):
                R.main(["--final", "--record-unavailable", "x", "--out", str(self.tmp / "out")])
        self.assertFalse((self.tmp / "out").exists())


class FinalReportingOnly(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_final_build_never_selects_and_reports_all_three(self):
        frozen_path = write_valid_freeze(self.tmp)
        r = RunnerHoldoutSafety()
        r.tmp = self.tmp
        r.assertEqual = self.assertEqual
        out = r.run_final_fake(frozen_path)
        s = synthetic_series()
        old = E.select
        E.select = tripwire                            # the frozen selection must not be recomputed
        try:
            with freeze_at(frozen_path):
                chronos, status = E.load_chronos(s, out, stage="final")
                files = E.build_final(s, FZ.verify_freeze(frozen_path), E.MODELS + (chronos,), status)
        finally:
            E.select = old
        self.assertNotIn("selection.json", files)
        rep = files["final_report.md"]
        self.assertIn("Reporting only", rep)
        self.assertIn("`seasonal_naive` (fallback_model)", rep)
        models = {line.split(",")[0] for line in files["predictions.csv"].splitlines()[1:]}
        self.assertEqual(models, {"seasonal_naive", "calendar_regression", "chronos2"})

    def test_final_build_rechecks_the_freeze_itself(self):
        # Defence in depth: build_final refuses on its own, whatever its caller checked.
        old = E.run
        E.run = tripwire
        try:
            with freeze_at(self.tmp / "missing.json"):
                with self.assertRaises(E.SealedError):
                    E.build_final(synthetic_series(), {}, E.MODELS, {"chronos2": "unavailable"})
        finally:
            E.run = old

    def test_cli_final_needs_chronos_files_or_a_recorded_reason(self):
        frozen = write_valid_freeze(self.tmp)
        old = (E.FINAL_CHRONOS_DIR, S.load, E.FINAL_DIR)
        E.FINAL_CHRONOS_DIR, S.load, E.FINAL_DIR = self.tmp / "empty", synthetic_series, self.tmp / "final"
        try:
            with freeze_at(frozen):
                self.assertEqual(E.main(["--final"]), 3)
            self.assertFalse((self.tmp / "final").exists())
        finally:
            E.FINAL_CHRONOS_DIR, S.load, E.FINAL_DIR = old


class Provenance(unittest.TestCase):
    def test_git_info_reads_the_commit_here(self):
        info = R.git_info()
        self.assertRegex(info["commit"], r"^[0-9a-f]{40}$")


if __name__ == "__main__":
    unittest.main()


class CommittedFreeze(unittest.TestCase):
    """F4b: the freeze committed on 15 Oct 2026. Changing it, or anything it pins, fails here."""

    FREEZE_SHA256 = "20d80998a19e1035f5c29c6276162e4286f4488de55cb02cf5f06fa955911a18"

    def test_the_committed_freeze_is_this_exact_file(self):
        self.assertEqual(FZ.freeze_sha256(), self.FREEZE_SHA256)

    def test_the_committed_freeze_still_holds(self):
        frozen = FZ.verify_freeze()
        self.assertEqual((frozen["selected_model"], frozen["status"]), ("seasonal_naive", "fallback_model"))
        self.assertEqual(frozen["frozen_at"], "2026-10-15T00:09:17+03:00")

    def test_the_seal_now_opens_only_through_the_freeze(self):
        # origins_for reads no data; it only answers whether the final folds may be used.
        self.assertEqual(E.origins_for("final"), E.FINAL_ORIGINS)

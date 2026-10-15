"""P0-2/P0-3: ForecastRun and the forecast import (coverage/forecast_import.py).

The Osaka file used here is the committed export of the frozen selection
(forecast/out/export/osaka_replay.json); broken variants are built from it.
"""

import copy
import io
import json
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from coverage import importer
from coverage.forecast_import import ForecastImportError, frozen_selection, import_forecast, level_of
from coverage.models import AttendanceForecast, ForecastRun
from coverage.services.requirements import window_gaps

OSAKA = Path(settings.BASE_DIR) / "forecast" / "out" / "export" / "osaka_replay.json"
SCENARIO = Path(settings.BASE_DIR) / "data" / "demo" / "scenario_riyadh_peak" / "forecast.json"


def osaka_doc() -> dict:
    return json.loads(OSAKA.read_text(encoding="utf-8"))


def scenario_doc() -> dict:
    return json.loads(SCENARIO.read_text(encoding="utf-8"))


def raw(doc: dict) -> bytes:
    return json.dumps(doc).encode()


class ImportCase(TestCase):
    def assertRefused(self, doc, *fragments):
        before = (ForecastRun.objects.count(), AttendanceForecast.objects.count())
        with self.assertRaises(ForecastImportError) as cm:
            import_forecast(raw(doc) if isinstance(doc, dict) else doc)
        text = "\n".join(cm.exception.problems)
        for f in fragments:
            self.assertIn(f, text)
        self.assertEqual(before, (ForecastRun.objects.count(), AttendanceForecast.objects.count()),
                         "a refused file must change nothing")


class OsakaReplayImport(ImportCase):
    def test_committed_export_imports_as_the_active_official_run(self):
        r = import_forecast(OSAKA.read_bytes())
        self.assertTrue(r.created)
        self.assertEqual(r.days, 126)
        run = r.run
        self.assertEqual((run.mode, run.data_origin, run.model_name, run.is_active),
                         ("osaka_replay", "official", "seasonal_naive", True))
        self.assertEqual(run.freeze_sha256, frozen_selection()[1])
        day = AttendanceForecast.objects.get(run=run, date="2025-09-13")
        self.assertEqual((day.p50, day.actual, day.level, str(day.as_of), day.horizon_days),
                         (209837, 218130, "peak", "2025-09-08", 5))
        self.assertEqual(level_of(day.actual, day.threshold_high, day.threshold_peak), "peak")

    def test_same_file_twice_changes_nothing(self):
        import_forecast(OSAKA.read_bytes())
        again = import_forecast(OSAKA.read_bytes())
        self.assertFalse(again.created)
        self.assertEqual((ForecastRun.objects.count(), AttendanceForecast.objects.count()), (1, 126))

    # E-18: the file on disk may already be CRLF (Git core.autocrlf on Windows checks
    # forecast/out/ out with CRLF), so both variants are built from LF-normalised bytes.
    # Replacing b"\n" in a CRLF file would give b"\r\r\n", which is not a line-ending
    # variant but different content.
    @staticmethod
    def lf_and_crlf() -> tuple[bytes, bytes]:
        lf = OSAKA.read_bytes().replace(b"\r\n", b"\n")
        return lf, lf.replace(b"\n", b"\r\n")

    def test_crlf_copy_is_the_same_file(self):
        lf, crlf = self.lf_and_crlf()
        import_forecast(lf)
        self.assertFalse(import_forecast(crlf).created)

    def test_lf_copy_of_a_crlf_import_is_the_same_file(self):
        lf, crlf = self.lf_and_crlf()
        import_forecast(crlf)
        self.assertFalse(import_forecast(lf).created)
        self.assertEqual((ForecastRun.objects.count(), AttendanceForecast.objects.count()), (1, 126))

    def test_the_file_as_checked_out_is_the_same_as_its_lf_form(self):
        lf, _ = self.lf_and_crlf()
        import_forecast(OSAKA.read_bytes())          # LF on Linux, CRLF on Windows with autocrlf
        self.assertFalse(import_forecast(lf).created)

    def test_only_crlf_is_normalised_not_other_byte_changes(self):
        lf, _ = self.lf_and_crlf()
        import_forecast(lf)
        for changed in (lf.replace(b"\n", b"\r\r\n"), lf.replace(b"\n", b"\r"), lf + b"\n"):
            with self.subTest(changed=changed[:12]):
                self.assertRefused(changed, "already exists with different content")

    def test_different_content_under_the_same_code_needs_replace(self):
        import_forecast(OSAKA.read_bytes())
        doc = osaka_doc()
        doc["run"]["note"] = "edited"
        self.assertRefused(doc, "already exists with different content")
        r = import_forecast(raw(doc), replace=True)
        self.assertTrue(r.created)
        self.assertEqual(ForecastRun.objects.get().note, "edited")
        self.assertEqual(AttendanceForecast.objects.count(), 126)

    def test_model_must_be_the_frozen_selection(self):
        doc = osaka_doc()
        doc["run"]["model"] = "chronos2"
        self.assertRefused(doc, "frozen selection")

    def test_freeze_hash_must_match(self):
        doc = osaka_doc()
        doc["run"]["freeze_sha256"] = "0" * 64
        self.assertRefused(doc, "frozen selection")

    def test_replay_without_a_freeze_file_is_refused(self):
        with self.assertRaises(ForecastImportError):
            import_forecast(OSAKA.read_bytes(), frozen=Path("/nonexistent/selection_frozen.json"))

    def test_no_look_ahead(self):
        doc = osaka_doc()
        doc["days"][0]["as_of"] = doc["days"][0]["date"]
        self.assertRefused(doc, "as_of + horizon_days")
        doc = osaka_doc()
        doc["days"][3]["horizon_days"] = 8
        self.assertRefused(doc, "horizon_days 1..7")

    def test_level_must_follow_from_p50_and_the_thresholds(self):
        doc = osaka_doc()
        d = next(x for x in doc["days"] if x["level"] == "peak")
        d["level"] = "normal"
        self.assertRefused(doc, "does not match p50 against the thresholds")

    def test_quantiles_and_thresholds_are_checked(self):
        doc = osaka_doc()
        doc["days"][5]["p10"] = doc["days"][5]["p90"] + 1
        self.assertRefused(doc, "p10 <= p50 <= p90")
        doc = osaka_doc()
        doc["days"][5]["threshold_high"] = doc["days"][5]["threshold_peak"] + 1
        self.assertRefused(doc, "threshold_high <= threshold_peak")

    def test_accuracy_or_unknown_fields_are_refused(self):
        doc = osaka_doc()
        doc["days"][0]["mae"] = 1
        self.assertRefused(doc, "keys must be exactly")
        doc = osaka_doc()
        doc["run"]["wape"] = 0.03
        self.assertRefused(doc, "keys not allowed")

    def test_replay_dates_are_expo_2025_only_and_once(self):
        doc = osaka_doc()
        doc["days"][0]["date"], doc["days"][0]["as_of"] = "2030-10-24", "2030-10-19"
        self.assertRefused(doc, "outside osaka_replay dates")
        doc = osaka_doc()
        doc["days"][1] = copy.deepcopy(doc["days"][0])
        self.assertRefused(doc, "date appears twice")

    def test_replay_must_be_official(self):
        doc = osaka_doc()
        doc["run"]["data_origin"] = "simulated"
        self.assertRefused(doc, "data_origin must be 'official'")

    def test_all_or_nothing(self):
        doc = osaka_doc()
        doc["days"][-1]["p50"] = -5          # 125 good days and one bad one
        self.assertRefused(doc)

    def test_not_json(self):
        self.assertRefused(b"\xff\xfe nope", "not valid UTF-8 JSON")
        self.assertRefused(b"[1, 2]", "exactly the keys")


class ScenarioImport(ImportCase):
    def test_scenario_file_imports_a_level_and_no_numbers(self):
        r = import_forecast(SCENARIO.read_bytes())
        (day,) = r.run.days.all()
        self.assertEqual((r.run.mode, r.run.data_origin, day.level), ("riyadh_scenario", "simulated", "peak"))
        self.assertEqual((day.p10, day.p50, day.p90, day.actual, day.as_of), (None,) * 5)

    def test_scenario_may_not_carry_visitor_numbers_or_accuracy(self):
        doc = scenario_doc()
        doc["days"][0]["p50"] = 250000
        self.assertRefused(doc, "keys must be exactly ['date', 'level']")
        doc = scenario_doc()
        doc["days"][0]["wape"] = 0.02
        self.assertRefused(doc, "keys must be exactly")

    def test_scenario_must_be_simulated_and_a_stated_assumption(self):
        doc = scenario_doc()
        doc["run"]["data_origin"] = "official"
        self.assertRefused(doc, "data_origin must be 'simulated'")
        doc = scenario_doc()
        doc["run"]["model"] = "seasonal_naive"
        self.assertRefused(doc, "scenario_assumption")

    def test_scenario_dates_are_expo_2030_only(self):
        doc = scenario_doc()
        doc["days"][0]["date"] = "2025-09-13"
        self.assertRefused(doc, "outside riyadh_scenario dates")

    def test_a_new_run_replaces_the_active_run_of_its_mode_only(self):
        import_forecast(OSAKA.read_bytes())
        first = import_forecast(SCENARIO.read_bytes()).run
        doc = scenario_doc()
        doc["run"]["code"] = "riyadh-scenario-high-2030-10-24"
        doc["days"][0]["level"] = "high"
        r = import_forecast(raw(doc))
        self.assertEqual(r.deactivated, [first.code])
        active = dict(ForecastRun.objects.filter(is_active=True).values_list("mode", "code"))
        self.assertEqual(active, {"osaka_replay": "osaka-replay-seasonal_naive-20d80998",
                                  "riyadh_scenario": "riyadh-scenario-high-2030-10-24"})


class ImportCommand(TestCase):
    def test_command_imports_then_reports_already_imported(self):
        out = io.StringIO()
        call_command("import_forecast", str(OSAKA), stdout=out)
        self.assertIn("Imported osaka-replay-seasonal_naive-20d80998: 126 days", out.getvalue())
        out = io.StringIO()
        call_command("import_forecast", str(OSAKA), stdout=out)
        self.assertIn("Already imported", out.getvalue())

    def test_command_refusal_lists_the_problems(self, tmp=None):
        bad = Path(settings.BASE_DIR) / "test_bad_forecast.json"
        doc = scenario_doc()
        doc["days"][0]["p50"] = 1
        bad.write_text(json.dumps(doc), encoding="utf-8")
        try:
            with self.assertRaises(CommandError) as cm:
                call_command("import_forecast", str(bad))
            self.assertIn("nothing was changed", str(cm.exception))
        finally:
            bad.unlink()
        self.assertFalse(ForecastRun.objects.exists())


class ReplayDoesNotTouchRiyadhShifts(TestCase):
    def test_importing_osaka_changes_no_2030_gap(self):
        importer.load_folder(settings.COVERCAST_DEMO_DATA_DIR)
        before = [(g.demand.site.code, g.demand.shift.date, g.demand.required_total, g.open_slots) for g in window_gaps()]
        import_forecast(OSAKA.read_bytes())
        after = [(g.demand.site.code, g.demand.shift.date, g.demand.required_total, g.open_slots) for g in window_gaps()]
        self.assertEqual(before, after)

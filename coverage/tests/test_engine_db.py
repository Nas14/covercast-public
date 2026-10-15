"""The engine on the real demo data (Phase 4 acceptance gate).

The expected gaps come from gap_definitions.csv, written before this engine
existed, so the engine is checked against an independent answer.
"""

import csv
from datetime import date
from pathlib import Path

from django.conf import settings
from django.test import TestCase

from coverage import importer
from coverage.models import (
    Assignment,
    AttendanceForecast,
    CalendarEvent,
    CoverageRequirement,
    DemandRule,
    EventImpact,
    ForecastRun,
    Shift,
    Site,
)
from coverage.services import demand_for, gap_for, refresh_requirement, window_gaps

PACK = Path(settings.COVERCAST_DEMO_DATA_DIR)


def expected_gaps():
    with open(PACK / "gap_definitions.csv", encoding="utf-8-sig", newline="") as fh:
        return {r["gap_id"]: r for r in csv.DictReader(fh)}


class DemoGapsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        importer.load_folder(PACK)

    def site_shift(self, site, day, slot):
        return Site.objects.get(code=site), Shift.objects.get(date=day, slot=slot)

    def test_exactly_the_two_planted_gaps_in_the_whole_window(self):
        found = sorted((g.demand.site.code, g.demand.shift.date.isoformat(), g.demand.shift.slot)
                       for g in window_gaps() if g.is_open)
        expected = sorted((r["site_code"], r["shift_date"], r["shift_label"]) for r in expected_gaps().values())
        self.assertEqual(found, expected)

    def test_g1_is_one_bundle_slot_anticipated(self):
        g1 = expected_gaps()["G1"]
        gap = gap_for(*self.site_shift(g1["site_code"], g1["shift_date"], g1["shift_label"]))
        self.assertEqual(gap.present, ("E006", "E018"))
        self.assertEqual(gap.headcount_gap, 0)
        (slot,) = gap.open_slots
        self.assertEqual(slot.must_have, set(g1["required_skills"].split(";")))
        self.assertEqual(gap.nature, "anticipated")

    def test_g2_no_show_counts_as_absent_although_assignment_says_confirmed(self):
        g2 = expected_gaps()["G2"]
        site, shift = self.site_shift(g2["site_code"], g2["shift_date"], g2["shift_label"])
        self.assertEqual(Assignment.objects.get(site=site, shift=shift, employee__code="E019").status, "confirmed")
        gap = gap_for(site, shift)
        self.assertNotIn("E019", gap.present)
        self.assertEqual((gap.headcount_gap, gap.nature), (1, "confirmed"))
        (slot,) = gap.open_slots  # A-15: the slot asks for exactly what Raghad's file says
        self.assertEqual(slot.must_have, set(g2["required_skills"].split(";")))

    def test_refresh_refuses_a_demand_of_another_cell(self):
        from coverage.services.requirements import demand_for, refresh_requirement
        s03, shift = self.site_shift("S03", date(2030, 10, 14), "morning")
        s01, _ = self.site_shift("S01", date(2030, 10, 14), "morning")
        with self.assertRaises(ValueError):
            refresh_requirement(s03, shift, demand=demand_for(s01, shift))

    def test_gap_is_live_cancelling_a_paramedic_turns_g1_confirmed(self):
        site, shift = self.site_shift("S03", date(2030, 10, 14), "morning")
        Assignment.objects.filter(site=site, shift=shift, employee__code="E018").update(status="cancelled")
        gap = gap_for(site, shift)
        self.assertEqual(gap.nature, "confirmed")
        self.assertEqual(gap.headcount_gap, 1)
        self.assertIn(frozenset({"paramedic", "adv_first_aid", "ja"}), [s.must_have for s in gap.open_slots])

    def test_refresh_requirement_keeps_one_row_per_site_and_shift(self):
        site, shift = self.site_shift("S03", date(2030, 10, 14), "morning")
        refresh_requirement(site, shift)
        refresh_requirement(site, shift)
        row = CoverageRequirement.objects.get(site=site, shift=shift)
        self.assertEqual((row.base, row.event_uplift, row.volume_uplift, row.required_total), (2, 0, 0, 2))
        self.assertEqual([d["kind"] for d in row.drivers], ["base", "event"])
        self.assertEqual(CoverageRequirement.objects.count(), 1)

    def test_engine_does_not_depend_on_a_forecast(self):
        before = [(g.demand.site.code, g.demand.shift.date, g.open_slots, g.nature) for g in window_gaps()]
        # P0-4: only forecasts of an ACTIVE run feed the engine.
        run = ForecastRun.objects.create(code="test-run", mode="riyadh_scenario", model_name="scenario_assumption",
                                         data_origin="simulated", level_method="test", source_sha256="x")
        AttendanceForecast.objects.create(run=run, date=date(2030, 10, 14), level="high",
                                          model_version="test", trained_on="test", data_origin="simulated")
        DemandRule.objects.create(site_type="medical", level="high", extra_staff=1)
        site, shift = self.site_shift("S03", date(2030, 10, 14), "morning")
        self.assertEqual(demand_for(site, shift).volume_uplift, 1)  # the forecast is used when present
        AttendanceForecast.objects.all().delete()
        after = [(g.demand.site.code, g.demand.shift.date, g.open_slots, g.nature) for g in window_gaps()]
        self.assertEqual(before, after)  # and removing it restores exactly the same gaps

    # Until 2026-10-15: "ignored even if stored", i.e. ignored with or without a
    # forecast (the documented defect). Now: counted with no forecast, ignored with one.
    def test_national_day_headcount_counts_without_a_forecast_and_not_with_one(self):
        site, shift = self.site_shift("S03", date(2030, 10, 14), "morning")
        EventImpact.objects.create(event=CalendarEvent.objects.get(code="1"), site=site, extra_staff=2)
        d = demand_for(site, shift)
        self.assertEqual((d.event_uplift, d.volume_uplift, d.required_total), (2, 0, 4))
        self.assertIn("fallback", [x.kind for x in d.drivers])

        DemandRule.objects.create(site_type="medical", level="peak", extra_staff=1)
        run = ForecastRun.objects.create(code="test-run", mode="riyadh_scenario", model_name="scenario_assumption",
                                         data_origin="simulated", level_method="test", source_sha256="x")
        AttendanceForecast.objects.create(run=run, date=date(2030, 10, 14), level="peak",
                                          model_version="t", trained_on="t", data_origin="simulated")
        d = demand_for(site, shift)
        self.assertEqual((d.event_uplift, d.volume_uplift, d.required_total), (0, 1, 3))
        self.assertIn("ignored", [x.kind for x in d.drivers])

        run.is_active = False   # deactivating the run brings the fallback back
        run.save()
        self.assertEqual(demand_for(site, shift).event_uplift, 2)

    def test_g1_g2_unchanged_by_the_event_fix(self):
        # The demo pack stores no headcount for the national day, so nothing moves.
        self.assertFalse(EventImpact.objects.exists())
        open_cells = [(g.demand.site.code, str(g.demand.shift.date), g.demand.shift.slot, g.demand.required_total)
                      for g in window_gaps() if g.is_open]
        self.assertEqual(open_cells, [("S03", "2030-10-14", "morning", 2), ("S10", "2030-10-15", "evening", 3)])

    def test_whole_window_query_count_is_bounded(self):
        # 72 site-shifts; guards against an accidental query per employee.
        with self.assertNumQueriesLessThan(72 * 8):
            window_gaps()

    def assertNumQueriesLessThan(self, limit):
        test = self

        class _Ctx:
            def __enter__(self):
                from django.db import connection
                from django.test.utils import CaptureQueriesContext
                self.ctx = CaptureQueriesContext(connection)
                return self.ctx.__enter__()

            def __exit__(self, *exc):
                self.ctx.__exit__(*exc)
                test.assertLess(len(self.ctx.captured_queries), limit)

        return _Ctx()

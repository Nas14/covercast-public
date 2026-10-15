"""Every database rule in coverage: the valid case passes, the case the rule
exists for is rejected."""

from datetime import date
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.test import TestCase

from coverage.codes import AssignmentOrigin, AssignmentStatus, ExclusionCode
from coverage.models import (
    Assignment,
    AttendanceForecast,
    CalendarEvent,
    CandidateEvaluation,
    CoverageRequirement,
    DemandRule,
    EventImpact,
    ForecastRun,
    Shift,
    Site,
    SiteRequirement,
)
from workforce.tests import factories as f


def make_site(code="S03", **kw):
    defaults = {"name": code, "zone": f.zone("C"), "site_type": "medical", "base_staff": 2, "importance": 5}
    defaults.update(kw)
    return Site.objects.create(code=code, **defaults)


def make_shift(day=date(2030, 10, 14), slot="morning"):
    start, end = (7, 15) if slot == "morning" else (15, 23)
    return Shift.objects.create(
        date=day, slot=slot,
        starts_at=f.at(day.year, day.month, day.day, start),
        ends_at=f.at(day.year, day.month, day.day, end),
    )


def make_event(code="1", kind="national_day"):
    return CalendarEvent.objects.create(
        code=code, kind=kind, title=code, starts_at=f.at(2030, 10, 14, 9), ends_at=f.at(2030, 10, 14, 14)
    )


class SiteTests(TestCase):
    def test_importance_must_be_1_to_5(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_site(importance=6)
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_site(code="S99", importance=0)

    def test_base_staff_at_least_one(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_site(base_staff=0)


class SiteRequirementTests(TestCase):
    def setUp(self):
        self.site, self.skill = make_site(), f.skill("paramedic")

    def test_duplicate_base_row_rejected(self):
        # The case a single unique(site, skill, event) would MISS, because NULL != NULL in SQL.
        SiteRequirement.objects.create(site=self.site, skill=self.skill, min_count=1)
        with self.assertRaises(IntegrityError), transaction.atomic():
            SiteRequirement.objects.create(site=self.site, skill=self.skill, min_count=1)

    def test_duplicate_event_row_rejected(self):
        ev = make_event()
        SiteRequirement.objects.create(site=self.site, skill=self.skill, min_count=1, event=ev)
        with self.assertRaises(IntegrityError), transaction.atomic():
            SiteRequirement.objects.create(site=self.site, skill=self.skill, min_count=1, event=ev)

    def test_base_and_event_rows_for_same_skill_coexist(self):
        SiteRequirement.objects.create(site=self.site, skill=self.skill, min_count=1)
        SiteRequirement.objects.create(site=self.site, skill=self.skill, min_count=1, event=make_event(), bundle="B1")
        self.assertEqual(self.site.requirements.count(), 2)

    def test_min_count_zero_rejected(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            SiteRequirement.objects.create(site=self.site, skill=self.skill, min_count=0)


class EventImpactTests(TestCase):
    def test_national_day_headcount_may_be_stored_as_the_no_forecast_fallback(self):
        # Until 2026-10-15 this was refused ("add skills, not headcount"). It is now the
        # fallback used only when no forecast covers the day (services/demand.py decides).
        impact = EventImpact(event=make_event(kind="national_day"), site=make_site(), extra_staff=2)
        impact.full_clean()  # no exception

    def test_vip_visit_can_add_headcount(self):
        impact = EventImpact(event=make_event(code="3", kind="vip_visit"), site=make_site(), extra_staff=2)
        impact.full_clean()  # no exception

    def test_national_day_with_zero_headcount_is_fine(self):
        EventImpact(event=make_event(), site=make_site(), extra_staff=0).full_clean()


class ShiftTests(TestCase):
    def test_one_shift_per_date_and_slot(self):
        make_shift()
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_shift()

    def test_end_after_start(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Shift.objects.create(
                date=date(2030, 10, 14), slot="evening",
                starts_at=f.at(2030, 10, 14, 23), ends_at=f.at(2030, 10, 14, 15),
            )


class AssignmentTests(TestCase):
    def setUp(self):
        self.emp, self.shift = f.employee("E061"), make_shift()
        self.s12, self.s03 = make_site("S12", zone=f.zone("D")), make_site("S03")
        self.user = get_user_model().objects.create_user("supervisor", password="x-not-used")

    def test_no_two_active_assignments_in_one_shift(self):
        Assignment.objects.create(employee=self.emp, site=self.s12, shift=self.shift, status="confirmed")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Assignment.objects.create(employee=self.emp, site=self.s03, shift=self.shift, status="planned")

    def test_cancelled_assignment_does_not_block_a_new_one(self):
        Assignment.objects.create(employee=self.emp, site=self.s12, shift=self.shift, status="cancelled")
        Assignment.objects.create(employee=self.emp, site=self.s03, shift=self.shift, status="planned")
        self.assertEqual(Assignment.objects.filter(status="planned").count(), 1)

    def test_absent_assignment_does_not_block_a_new_one(self):
        Assignment.objects.create(employee=self.emp, site=self.s12, shift=self.shift, status="absent")
        Assignment.objects.create(employee=self.emp, site=self.s03, shift=self.shift, status="planned")

    def test_replacement_requires_approver_and_time(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Assignment.objects.create(
                employee=self.emp, site=self.s03, shift=self.shift, origin=AssignmentOrigin.REPLACEMENT
            )

    def test_approved_replacement_is_accepted(self):
        a = Assignment.objects.create(
            employee=self.emp, site=self.s03, shift=self.shift, origin=AssignmentOrigin.REPLACEMENT,
            approved_by=self.user, approved_at=f.at(2030, 10, 13, 16, 45),
        )
        self.assertTrue(a.is_active)

    def test_idempotency_key_is_unique(self):
        other = make_shift(slot="evening")
        Assignment.objects.create(employee=self.emp, site=self.s03, shift=self.shift, idempotency_key="k1")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Assignment.objects.create(employee=self.emp, site=self.s03, shift=other, idempotency_key="k1")

    def test_many_rows_without_key_allowed(self):
        other = make_shift(slot="evening")
        Assignment.objects.create(employee=self.emp, site=self.s03, shift=self.shift)
        Assignment.objects.create(employee=self.emp, site=self.s03, shift=other)
        self.assertEqual(Assignment.objects.filter(idempotency_key__isnull=True).count(), 2)

    def test_employee_with_assignments_cannot_be_deleted(self):
        Assignment.objects.create(employee=self.emp, site=self.s12, shift=self.shift)
        with self.assertRaises(ProtectedError):
            self.emp.delete()

    def test_status_values(self):
        self.assertEqual(
            sorted(AssignmentStatus.values), ["absent", "cancelled", "confirmed", "planned"]
        )


class CoverageRequirementTests(TestCase):
    def setUp(self):
        self.site, self.shift = make_site(), make_shift()

    def _make(self, **kw):
        values = {"base": 2, "event_uplift": 0, "volume_uplift": 0, "required_total": 2,
                  "computed_at": f.at(2030, 10, 13, 16, 40)}
        values.update(kw)
        return CoverageRequirement.objects.create(site=self.site, shift=self.shift, **values)

    def test_total_must_equal_sum_of_parts(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            self._make(event_uplift=1, required_total=2)

    def test_valid_total(self):
        self.assertEqual(self._make(event_uplift=1, volume_uplift=1, required_total=4).required_total, 4)

    def test_one_row_per_site_and_shift(self):
        self._make()
        with self.assertRaises(IntegrityError), transaction.atomic():
            self._make()


class CandidateEvaluationTests(TestCase):
    def setUp(self):
        req = CoverageRequirement.objects.create(
            site=make_site(), shift=make_shift(), base=2, required_total=2, computed_at=f.at(2030, 10, 13, 16, 40)
        )
        self.base = {"requirement": req, "employee": f.employee(), "run_id": uuid4(),
                     "evaluated_at": f.at(2030, 10, 13, 16, 40)}

    def test_excluded_needs_a_reason(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            CandidateEvaluation.objects.create(eligible=False, failed_rule="", **self.base)

    def test_eligible_cannot_carry_a_reason(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            CandidateEvaluation.objects.create(eligible=True, failed_rule=ExclusionCode.SCOPE_OUT, **self.base)

    def test_rank_only_for_eligible(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            CandidateEvaluation.objects.create(
                eligible=False, failed_rule=ExclusionCode.CERT_EXPIRED, rank=1, **self.base
            )

    def test_valid_rows(self):
        CandidateEvaluation.objects.create(eligible=True, rank=1, score="60.83", **self.base)
        self.assertEqual(CandidateEvaluation.objects.count(), 1)


class ForecastAndDemandRuleTests(TestCase):
    def test_quantiles_must_be_ordered(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            AttendanceForecast.objects.create(
                date=date(2030, 10, 14), p10=200, p50=150, p90=250, level="high",
                model_version="v1", trained_on="x", data_origin="simulated",
            )

    # Migration 0004: uniqueness is (run, date), replacing (date, model_version).
    def make_run(self, code="r1", mode="riyadh_scenario", origin="simulated", active=True):
        return ForecastRun.objects.create(code=code, mode=mode, model_name="m", data_origin=origin,
                                          level_method="x", source_sha256="x", is_active=active)

    def test_one_forecast_per_run_and_date(self):
        kw = {"level": "normal", "model_version": "v1", "trained_on": "x", "data_origin": "simulated"}
        r1, r2 = self.make_run("r1"), self.make_run("r2", active=False)
        AttendanceForecast.objects.create(run=r1, date=date(2030, 10, 14), **kw)
        AttendanceForecast.objects.create(run=r2, date=date(2030, 10, 14), **kw)
        with self.assertRaises(IntegrityError), transaction.atomic():
            AttendanceForecast.objects.create(run=r1, date=date(2030, 10, 14), **kw)

    def test_one_active_run_per_mode(self):
        self.make_run("a")
        self.make_run("b", active=False)
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.make_run("c")
        self.make_run("d", mode="osaka_replay", origin="official")  # the other mode is independent

    def test_mode_must_match_data_origin(self):
        for mode, origin in (("osaka_replay", "simulated"), ("riyadh_scenario", "official")):
            with self.subTest(mode=mode), self.assertRaises(IntegrityError), transaction.atomic():
                self.make_run(f"x-{mode}", mode=mode, origin=origin, active=False)

    def test_as_of_must_be_before_the_date(self):
        r = self.make_run()
        with self.assertRaises(IntegrityError), transaction.atomic():
            AttendanceForecast.objects.create(run=r, date=date(2030, 10, 14), as_of=date(2030, 10, 14),
                                              level="high", model_version="v", trained_on="x",
                                              data_origin="simulated")

    def test_scenario_day_needs_no_visitor_numbers(self):
        AttendanceForecast.objects.create(run=self.make_run(), date=date(2030, 10, 24), level="peak",
                                          model_version="scenario_assumption", trained_on="n/a",
                                          data_origin="simulated")

    def test_one_demand_rule_per_type_and_level(self):
        DemandRule.objects.create(site_type="medical", level="high", extra_staff=1)
        with self.assertRaises(IntegrityError), transaction.atomic():
            DemandRule.objects.create(site_type="medical", level="high", extra_staff=2)

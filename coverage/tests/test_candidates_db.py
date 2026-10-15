"""Phase 5 acceptance gate: the engine on the real demo data, checked against
answers derived by hand BEFORE the engine existed (fixtures/expected_g1.csv)."""

import csv
from collections import Counter
from datetime import date
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from coverage import importer
from coverage.models import Assignment, CandidateEvaluation, Shift, Site
from coverage.services import snapshot
from coverage.services.evaluation import SlotNotOpen, evaluate_slot
from coverage.services.requirements import gap_for
from workforce.models import Absence, EmployeeSkill

PACK = Path(settings.COVERCAST_DEMO_DATA_DIR)
EXPECTED = Path(__file__).parent / "fixtures" / "expected_g1.csv"


def expected_rows():
    with open(EXPECTED, encoding="utf-8", newline="") as fh:
        return {r["employee_code"]: r for r in csv.DictReader(fh)}


class G1Tests(TestCase):
    @classmethod
    def setUpTestData(cls):
        importer.load_folder(PACK)
        cls.site = Site.objects.get(code="S03")
        cls.shift = Shift.objects.get(date=date(2030, 10, 14), slot="morning")

    def setUp(self):
        self.result = evaluate_slot(self.site, self.shift)

    def test_every_hand_derived_row_matches(self):
        all_verdicts = {v.code: v for v in (*self.result.ranked, *self.result.excluded)}
        for code, exp in expected_rows().items():
            v = all_verdicts[code]
            with self.subTest(code=code):
                self.assertEqual(v.eligible, exp["expected_eligible"] == "True")
                self.assertEqual(v.failed_rule, exp["expected_failed_rule"])
                if v.eligible:
                    self.assertEqual(v.rank, int(exp["expected_rank"]))
                    self.assertEqual(v.tag, exp["expected_tag"])
                    self.assertEqual(v.travel_minutes, int(exp["travel_minutes"]))
                    self.assertEqual(v.hours_after, int(exp["hours_after"]))
                    self.assertEqual(v.score, Decimal(exp["score"]))
                    for part in ("proximity", "hours_headroom", "extra_skills", "donor_safety", "fairness"):
                        self.assertEqual(v.score_parts[part], Decimal(exp[part]), part)

    def test_totals_match_the_hand_derivation(self):
        counts = Counter(v.failed_rule for v in self.result.excluded)
        self.assertEqual(dict(counts), {"MISSING_SKILL": 74, "ALREADY_ASSIGNED": 2, "CERT_EXPIRED": 1, "SCOPE_OUT": 1})
        self.assertEqual(len(self.result.ranked), 2)

    def test_baseline_picks_ahmed_and_breaks_north_gate(self):
        b = self.result.baseline
        self.assertEqual((b.code, b.travel_minutes, b.violates, b.breaks_site, b.donor_short_by),
                         ("E053", 10, "SCOPE_OUT", "S05", 1))

    def test_one_audit_row_per_person(self):
        rows = CandidateEvaluation.objects.filter(run_id=self.result.run_id)
        self.assertEqual(rows.count(), 80)
        self.assertEqual(rows.filter(eligible=True).count(), 2)
        noura = rows.get(employee__code="E061")
        self.assertEqual((noura.rank, noura.tag, noura.score), (1, "recommended", Decimal("60.83")))
        self.assertEqual(noura.score_parts["hours_headroom"], "8.33")
        reem = rows.get(employee__code="E060")
        self.assertEqual((reem.failed_rule, reem.failed_detail["date"]), ("CERT_EXPIRED", "2030-10-13"))

    def test_evaluating_twice_gives_the_same_answer_and_two_runs(self):
        again = evaluate_slot(self.site, self.shift)
        self.assertEqual([(v.code, v.score) for v in again.ranked], [(v.code, v.score) for v in self.result.ranked])
        self.assertNotEqual(again.run_id, self.result.run_id)
        self.assertEqual(CandidateEvaluation.objects.values("run_id").distinct().count(), 2)

    def test_evaluation_never_creates_an_assignment(self):
        before = Assignment.objects.count()
        evaluate_slot(self.site, self.shift)
        self.assertEqual(Assignment.objects.count(), before)

    def test_query_count_does_not_grow_with_people(self):
        with CaptureQueriesContext(connection) as ctx:
            evaluate_slot(self.site, self.shift, persist=False)
        self.assertLess(len(ctx.captured_queries), 150)


class G2Tests(TestCase):
    """G2 has no hand-written ranking; instead every ranked person is
    re-checked here against the raw data, independently of the engine."""

    @classmethod
    def setUpTestData(cls):
        importer.load_folder(PACK)
        cls.site = Site.objects.get(code="S10")
        cls.shift = Shift.objects.get(date=date(2030, 10, 15), slot="evening")
        cls.result = evaluate_slot(cls.site, cls.shift, persist=False)

    def test_who_is_where(self):
        by_code = {v.code: v.failed_rule for v in self.result.excluded}
        self.assertEqual(by_code["E019"], "ABSENT")          # not ALREADY_ASSIGNED
        self.assertEqual(by_code["E003"], "ALREADY_ASSIGNED")
        self.assertEqual(by_code["E046"], "ALREADY_ASSIGNED")
        self.assertEqual(self.result.slot.must_have, {"crowd_control"})  # A-15

    def test_every_ranked_person_really_satisfies_the_hard_rules(self):
        self.assertTrue(self.result.ranked)
        for v in self.result.ranked:
            with self.subTest(code=v.code):
                mine = Assignment.objects.filter(employee__code=v.code, status__in=["planned", "confirmed"])
                self.assertFalse(mine.filter(site=self.site, shift=self.shift).exists())
                self.assertFalse(Absence.objects.filter(employee__code=v.code, starts_at__lt=self.shift.ends_at,
                                                        ends_at__gt=self.shift.starts_at).exists())
                self.assertLessEqual(v.hours_after, v.hours_cap)
                for a in mine.exclude(shift=self.shift).select_related("shift"):
                    gap = min(abs((self.shift.starts_at - a.shift.ends_at).total_seconds()),
                              abs((a.shift.starts_at - self.shift.ends_at).total_seconds())) / 3600
                    self.assertGreaterEqual(gap, 11)
                donor = mine.filter(shift=self.shift).first()
                if donor:  # leaving must not open a new gap at the donor site
                    before = gap_for(donor.site, self.shift)
                    donor.status = "cancelled"
                    donor.save()
                    self.assertEqual(len(gap_for(donor.site, self.shift).open_slots), len(before.open_slots))
                    donor.status = "confirmed"
                    donor.save()

    def test_only_valid_crowd_control_holders_are_ranked(self):
        # Re-derived from employee_skill.csv by hand (docs/09, A-15): 18 hold a valid
        # crowd_control on 15 Oct; E019 is absent, E046 is already there; of the other
        # 16 only Ghada and Shurooq pass every rule.
        self.assertEqual([(v.code, v.score, v.tag) for v in self.result.ranked],
                         [("E058", Decimal("50.00"), "recommended"), ("E013", Decimal("45.00"), "backup")])
        counts = Counter(v.failed_rule for v in self.result.excluded)
        self.assertEqual(counts["MISSING_SKILL"], 58)
        self.assertEqual([v.code for v in self.result.excluded if v.failed_rule == "CERT_EXPIRED"], ["E033"])
        for v in self.result.ranked:
            held = EmployeeSkill.objects.get(employee__code=v.code, skill__code="crowd_control")
            self.assertTrue(held.expires_on is None or held.expires_on >= self.shift.date)

    def test_ranking_is_sorted(self):
        keys = [(-v.score, v.travel_minutes, v.hours_after, v.code) for v in self.result.ranked]
        self.assertEqual(keys, sorted(keys))

    def test_baseline_takes_an_out_of_scope_person_and_breaks_s09(self):
        b = self.result.baseline
        self.assertEqual((b.code, b.violates, b.breaks_site, b.donor_short_by), ("E009", "SCOPE_OUT", "S09", 1))


class SlotTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        importer.load_folder(PACK)

    def test_covered_site_has_no_slot_to_evaluate(self):
        with self.assertRaises(SlotNotOpen):
            evaluate_slot(Site.objects.get(code="S01"), Shift.objects.get(date=date(2030, 10, 14), slot="morning"))

    def test_week_runs_sunday_to_saturday(self):
        self.assertEqual(snapshot.week_bounds(date(2030, 10, 15)), (date(2030, 10, 13), date(2030, 10, 20)))
        self.assertEqual(snapshot.week_bounds(date(2030, 10, 13)), (date(2030, 10, 13), date(2030, 10, 20)))
        self.assertEqual(snapshot.week_bounds(date(2030, 10, 19)), (date(2030, 10, 13), date(2030, 10, 20)))

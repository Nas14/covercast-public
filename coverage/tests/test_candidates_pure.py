"""Eligibility rules, ranking and the baseline, with hand-built facts (no database)."""

import csv
import random
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from django.conf import settings
from django.test import SimpleTestCase

from coverage.codes import ExclusionCode
from coverage.services.baseline import nearest_qualified
from coverage.services.demand import build_demand
from coverage.services.eligibility import evaluate, hours_after
from coverage.services.ranking import rank, score_parts
from coverage.services.reasons import TEMPLATES, render
from coverage.services.types import (
    CandidateFacts,
    DonorSite,
    Evaluation,
    Person,
    RequirementLine,
    ShiftInfo,
    SiteInfo,
    Slot,
    SlotContext,
)

R = ZoneInfo("Asia/Riyadh")
DAY = date(2030, 10, 14)


def at(d, h):
    return datetime(2030, 10, d, h, tzinfo=R)


SHIFT = ShiftInfo(DAY, "morning", at(14, 7), at(14, 15))
S03 = SiteInfo("S03", 2, "medical", 5, "C")
BUNDLE = frozenset({"paramedic", "adv_first_aid", "ja"})
TRAVEL = {(a, b): 5 if a == b else 10 + 5 * (abs(ord(a) - ord(b)) - 1) for a in "ABCDE" for b in "ABCDE"}
PARENTS = {"adv_first_aid": "basic_first_aid"}
CTX = SlotContext(site=S03, shift=SHIFT, slot=Slot(1, BUNDLE, "event:1"), travel=TRAVEL, parents=PARENTS)
ALL_VALID = (("adv_first_aid", date(2031, 1, 1)), ("ja", None), ("paramedic", None))


def facts(code="E900", **kw):
    base = dict(
        code=code, name=code, category="contractor", hours_cap=48, min_rest_hours=11, enforce_scope=False,
        scope_zones=frozenset(), held=ALL_VALID, hours_before_window=0, week_shift_count=0, other_shifts=(),
        absences=(), assigned_here=False, donor=None, recent_replacements=0,
    )
    base.update(kw)
    return CandidateFacts(**base)


def donor(code="S12", zone="D", importance=5, base=4, present=5, lines=()):
    site = SiteInfo(code, base, "medical", importance, zone)
    people = tuple(Person(f"P{i}", frozenset({"paramedic", "adv_first_aid"})) for i in range(present - 1))
    return DonorSite(code, zone, importance, build_demand(site, SHIFT, list(lines)),
                     people + (Person("E900", frozenset(BUNDLE)),))


class RuleTests(SimpleTestCase):
    def rule(self, f, ctx=CTX):
        return evaluate(f, ctx).failed_rule

    def test_fully_qualified_off_duty_person_is_eligible(self):
        ev = evaluate(facts(), CTX)
        self.assertTrue(ev.eligible)
        self.assertEqual((ev.travel_minutes, ev.hours_after), (60, 8))

    def test_absent_comes_first_even_if_assigned_here(self):
        f = facts(assigned_here=True, absences=(("no_show", at(14, 7), at(14, 15)),))
        self.assertEqual(self.rule(f), ExclusionCode.ABSENT)

    def test_absence_outside_the_shift_does_not_count(self):
        f = facts(absences=(("leave", at(13, 0), at(13, 23)),))
        self.assertTrue(evaluate(f, CTX).eligible)

    def test_already_assigned(self):
        self.assertEqual(self.rule(facts(assigned_here=True)), ExclusionCode.ALREADY_ASSIGNED)

    def test_missing_skill_names_what_is_missing(self):
        ev = evaluate(facts(held=(("paramedic", None), ("adv_first_aid", None))), CTX)
        self.assertEqual((ev.failed_rule, ev.detail["skills"]), (ExclusionCode.MISSING_SKILL, ["ja"]))

    def test_missing_before_expired_before_scope(self):
        f = facts(held=(("adv_first_aid", date(2030, 10, 13)),), enforce_scope=True, scope_zones=frozenset("AB"))
        self.assertEqual(self.rule(f), ExclusionCode.MISSING_SKILL)
        f = replace(f, held=(("adv_first_aid", date(2030, 10, 13)), ("ja", None), ("paramedic", None)))
        self.assertEqual(self.rule(f), ExclusionCode.CERT_EXPIRED)
        f = replace(f, held=ALL_VALID)
        self.assertEqual(self.rule(f), ExclusionCode.SCOPE_OUT)

    def test_certificate_judged_on_shift_date(self):
        reem = facts(held=(("adv_first_aid", date(2030, 10, 13)), ("ja", None), ("paramedic", None)))
        ev = evaluate(reem, CTX)
        self.assertEqual((ev.failed_rule, ev.detail["date"]), (ExclusionCode.CERT_EXPIRED, "2030-10-13"))
        self.assertTrue(evaluate(replace(reem, held=ALL_VALID[:0] + (("adv_first_aid", DAY),) + ALL_VALID[1:]), CTX).eligible)

    def test_advanced_satisfies_a_basic_requirement(self):
        ctx = replace(CTX, slot=Slot(1, frozenset({"basic_first_aid"}), "base"))
        self.assertTrue(evaluate(facts(held=(("adv_first_aid", None),)), ctx).eligible)

    def test_basic_does_not_satisfy_an_advanced_requirement(self):
        ctx = replace(CTX, slot=Slot(1, frozenset({"adv_first_aid"}), "base"))
        self.assertEqual(self.rule(facts(held=(("basic_first_aid", None),)), ctx), ExclusionCode.MISSING_SKILL)

    def test_scope_only_for_categories_that_enforce_it(self):
        self.assertTrue(evaluate(facts(enforce_scope=False, scope_zones=frozenset("AB")), CTX).eligible)
        self.assertEqual(self.rule(facts(enforce_scope=True, scope_zones=frozenset("AB"))), ExclusionCode.SCOPE_OUT)
        self.assertTrue(evaluate(facts(enforce_scope=True, scope_zones=frozenset("CDE")), CTX).eligible)

    def test_hours_cap_boundary(self):
        self.assertTrue(evaluate(facts(hours_before_window=24, week_shift_count=2), CTX).eligible)  # 48 of 48
        self.assertEqual(self.rule(facts(hours_before_window=25, week_shift_count=2)), ExclusionCode.HOURS_CAP)

    def test_moving_within_the_shift_still_counts_that_shift(self):
        # Regression E-06: Noura works the shift either way (32, not 24).
        f = facts(hours_before_window=8, week_shift_count=2, donor=donor())
        self.assertEqual(hours_after(f), 32)

    def test_rest_gap(self):
        evening_before = ((at(13, 15), at(13, 23)),)       # 8 h before 07:00
        self.assertEqual(self.rule(facts(other_shifts=evening_before)), ExclusionCode.REST_GAP)
        same_day_evening = ((at(14, 15), at(14, 23)),)     # 0 h after
        self.assertEqual(self.rule(facts(other_shifts=same_day_evening)), ExclusionCode.REST_GAP)
        morning_before = ((at(13, 7), at(13, 15)),)        # 16 h
        self.assertTrue(evaluate(facts(other_shifts=morning_before), CTX).eligible)
        exactly_11 = ((at(13, 12), at(13, 20)),)           # 20:00 -> 07:00 = 11 h
        self.assertTrue(evaluate(facts(other_shifts=exactly_11), CTX).eligible)

    def test_leaving_a_site_at_its_minimum_breaks_it(self):
        ev = evaluate(facts(donor=donor(present=4)), CTX)
        self.assertEqual((ev.failed_rule, ev.detail["short_by"]), (ExclusionCode.BREAKS_MINIMUM, 1))

    def test_leaving_a_site_with_surplus_is_fine(self):
        self.assertTrue(evaluate(facts(donor=donor(present=5)), CTX).eligible)

    def test_leaving_the_only_paramedic_breaks_the_site_even_with_surplus(self):
        site = SiteInfo("S12", 2, "medical", 5, "D")
        d = DonorSite("S12", "D", 5, build_demand(site, SHIFT, [RequirementLine("paramedic", 1)]),
                      (Person("P1", frozenset()), Person("P2", frozenset()), Person("E900", frozenset(BUNDLE))))
        self.assertEqual(self.rule(facts(donor=d)), ExclusionCode.BREAKS_MINIMUM)


def eligible(code, travel, hours, cap=48, donor_site=None, importance=None, recent=0):
    return Evaluation(code=code, name=code, category="x", eligible=True, travel_minutes=travel,
                      hours_after=hours, hours_cap=cap, donor_site=donor_site, donor_importance=importance,
                      recent_replacements=recent)


class RankingTests(SimpleTestCase):
    def test_noura_parts_match_the_hand_calculation(self):
        parts = score_parts(eligible("E061", 10, 32, donor_site="S12", importance=5))
        self.assertEqual({k: str(v) for k, v in parts.items()}, {
            "proximity": "22.50", "hours_headroom": "8.33", "extra_skills": "20.00",
            "donor_safety": "0.00", "fairness": "10.00"})

    def test_score_is_exactly_the_sum_of_parts(self):
        (top,) = rank([eligible("A", 10, 32, donor_site="S12", importance=5)])
        self.assertEqual(top.score, sum(top.score_parts.values()))
        self.assertEqual(top.score, Decimal("60.83"))

    def test_proximity_is_clamped(self):
        self.assertEqual(score_parts(eligible("A", 5, 0))["proximity"], Decimal("30.00"))
        self.assertEqual(score_parts(eligible("A", 60, 0))["proximity"], Decimal("0.00"))

    def test_donor_safety_by_importance(self):
        self.assertEqual(score_parts(eligible("A", 10, 8))["donor_safety"], Decimal("15.00"))
        self.assertEqual(score_parts(eligible("A", 10, 8, donor_site="S09", importance=3))["donor_safety"], Decimal("7.50"))

    def test_fairness_penalty(self):
        self.assertEqual(score_parts(eligible("A", 10, 8, recent=1))["fairness"], Decimal("5.00"))
        self.assertEqual(score_parts(eligible("A", 10, 8, recent=3))["fairness"], Decimal("0.00"))

    def test_excluded_people_are_not_ranked(self):
        out = rank([eligible("A", 10, 8), Evaluation("B", "B", "x", eligible=False, failed_rule="SCOPE_OUT")])
        self.assertEqual([e.code for e in out], ["A"])

    def test_tags(self):
        out = rank([eligible("A", 5, 8), eligible("B", 10, 8), eligible("C", 15, 8)])
        self.assertEqual([(e.code, e.rank, e.tag) for e in out],
                         [("A", 1, "recommended"), ("B", 2, "backup"), ("C", 3, None)])

    def test_order_is_identical_over_100_shuffles(self):
        pool = [eligible(f"E{i:03d}", random.Random(i).choice([5, 10, 15, 60]), random.Random(i).choice([8, 16, 24]))
                for i in range(30)]
        first = [e.code for e in rank(pool)]
        for seed in range(100):
            shuffled = pool[:]
            random.Random(seed).shuffle(shuffled)
            self.assertEqual([e.code for e in rank(shuffled)], first)

    def test_full_tie_is_broken_by_code(self):
        out = rank([eligible("E002", 10, 8), eligible("E001", 10, 8)])
        self.assertEqual([e.code for e in out], ["E001", "E002"])

    def test_near_cap_reason(self):
        (ev,) = rank([eligible("K", 60, 48)])
        self.assertIn("NEAR_HOURS_CAP", [code for code, _ in ev.reasons])


class BaselineTests(SimpleTestCase):
    def test_nearest_qualified_ignores_scope_and_reports_the_damage(self):
        ahmed = facts("E053", enforce_scope=True, scope_zones=frozenset("AB"),
                      donor=replace(donor("S05", "B", 4, base=3, present=3), present=(
                          Person("P1", frozenset()), Person("P2", frozenset()), Person("E053", BUNDLE))))
        noura = facts("E061", enforce_scope=True, scope_zones=frozenset("CDE"), donor=donor())
        khaled = facts("E012")
        b = nearest_qualified([khaled, noura, ahmed], CTX)
        self.assertEqual((b.code, b.travel_minutes, b.violates, b.breaks_site, b.donor_short_by),
                         ("E053", 10, "SCOPE_OUT", "S05", 1))

    def test_baseline_skips_expired_absent_and_already_here(self):
        reem = facts("E060", held=(("adv_first_aid", date(2030, 10, 13)), ("ja", None), ("paramedic", None)),
                     donor=donor("S08", "C", 3))
        here = facts("E018", assigned_here=True)
        away = facts("E099", absences=(("sick", at(14, 0), at(14, 23)),))
        khaled = facts("E012")
        self.assertEqual(nearest_qualified([reem, here, away, khaled], CTX).code, "E012")

    def test_no_qualified_person_gives_none(self):
        self.assertIsNone(nearest_qualified([facts(held=())], CTX))


class ReasonTemplateTests(SimpleTestCase):
    def test_templates_match_the_team_file(self):
        path = Path(settings.COVERCAST_DEMO_DATA_DIR) / "reason_templates.csv"
        with open(path, encoding="utf-8-sig", newline="") as fh:
            from_file = {r["code"]: (r["kind"], r["template_ar"], r["template_en"]) for r in csv.DictReader(fh)}
        self.assertEqual(TEMPLATES, from_file)

    def test_every_exclusion_code_has_a_template(self):
        self.assertTrue(set(ExclusionCode.values) <= set(TEMPLATES))

    def test_render_fills_parameters(self):
        self.assertEqual(render("SCOPE_OUT", {"zone": "C"}), "نطاق إعارته لا يشمل المنطقة C")
        self.assertEqual(render("CERT_EXPIRED", {"skill": "adv_first_aid", "date": "2030-10-13"}, "en"),
                         "Certificate adv_first_aid expires on 2030-10-13, before the shift")

    def test_render_keeps_missing_parameters_visible(self):
        self.assertIn("{zone}", render("SCOPE_OUT", {}))

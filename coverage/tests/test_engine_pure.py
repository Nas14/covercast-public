"""Rules of build_demand / detect_gaps / effective_skills, with hand-written
inputs and no database. Each test names the rule it pins down."""

from datetime import date, datetime
from unittest import mock
from zoneinfo import ZoneInfo

from django.test import SimpleTestCase

from coverage import policy
from coverage.services.demand import build_demand
from coverage.services.gaps import detect_gaps
from coverage.services.skills import effective_skills
from coverage.services.types import EventInfo, ForecastInfo, ImpactLine, Person, RequirementLine, ShiftInfo, SiteInfo

R = ZoneInfo("Asia/Riyadh")
DAY = date(2030, 10, 14)
MORNING = ShiftInfo(DAY, "morning", datetime(2030, 10, 14, 7, tzinfo=R), datetime(2030, 10, 14, 15, tzinfo=R))
EVENING = ShiftInfo(DAY, "evening", datetime(2030, 10, 14, 15, tzinfo=R), datetime(2030, 10, 14, 23, tzinfo=R))
JAPAN_DAY = EventInfo("1", "national_day", "Japan national day",
                      datetime(2030, 10, 14, 9, tzinfo=R), datetime(2030, 10, 14, 14, tzinfo=R))
VIP = EventInfo("3", "vip_visit", "VIP visit",
                datetime(2030, 10, 14, 9, tzinfo=R), datetime(2030, 10, 14, 11, tzinfo=R))
MEDICAL = SiteInfo("S03", base_staff=2, site_type="medical", importance=5, zone="C")
GATE = SiteInfo("S04", base_staff=4, site_type="security_gate", importance=4, zone="A")

S03_LINES = [
    RequirementLine("adv_first_aid", 1),
    RequirementLine("paramedic", 1),
    RequirementLine("paramedic", 1, JAPAN_DAY, "B1"),
    RequirementLine("adv_first_aid", 1, JAPAN_DAY, "B1"),
    RequirementLine("ja", 1, JAPAN_DAY, "B1"),
]


def person(code, *skills):
    return Person(code, frozenset(skills))


class EffectiveSkillsTests(SimpleTestCase):
    parents = {"adv_first_aid": "basic_first_aid", "basic_first_aid": None}

    def test_advanced_satisfies_basic(self):
        got = effective_skills([("adv_first_aid", None)], self.parents, DAY)
        self.assertEqual(got, {"adv_first_aid", "basic_first_aid"})

    def test_basic_never_satisfies_advanced(self):
        got = effective_skills([("basic_first_aid", None)], self.parents, DAY)
        self.assertNotIn("adv_first_aid", got)

    def test_expired_certificate_gives_nothing_not_even_its_parent(self):
        got = effective_skills([("adv_first_aid", date(2030, 10, 13))], self.parents, DAY)
        self.assertEqual(got, frozenset())

    def test_expiry_day_itself_is_still_valid(self):
        got = effective_skills([("adv_first_aid", DAY)], self.parents, DAY)
        self.assertIn("adv_first_aid", got)


class BuildDemandTests(SimpleTestCase):
    def test_base_only(self):
        d = build_demand(MEDICAL, MORNING, [RequirementLine("paramedic", 1)])
        self.assertEqual((d.base, d.event_uplift, d.volume_uplift, d.required_total), (2, 0, 0, 2))

    def test_event_outside_the_shift_is_ignored(self):
        d = build_demand(MEDICAL, EVENING, S03_LINES)
        self.assertEqual(len(d.skill_needs), 2)  # base adv + base paramedic only
        self.assertTrue(all(n.is_base for n in d.skill_needs))

    def test_bundle_is_one_need_for_one_person(self):
        d = build_demand(MEDICAL, MORNING, S03_LINES)
        bundle = [n for n in d.skill_needs if len(n.skills) == 3]
        self.assertEqual(len(bundle), 1)
        self.assertEqual(bundle[0].skills, {"paramedic", "adv_first_aid", "ja"})
        self.assertEqual((bundle[0].count, bundle[0].base_count, bundle[0].source), (1, 0, "event:1"))

    def test_event_line_raises_the_total_it_does_not_add(self):
        lines = [RequirementLine("security_license", 1), RequirementLine("security_license", 2, VIP)]
        d = build_demand(GATE, MORNING, lines)
        (need,) = d.skill_needs
        self.assertEqual((need.count, need.base_count, need.source), (2, 1, "event:3"))  # 2, not 3

    def test_vip_impact_adds_headcount(self):
        d = build_demand(GATE, MORNING, [], impacts=[ImpactLine(VIP, 2)])
        self.assertEqual((d.event_uplift, d.required_total), (2, 6))
        self.assertIn(("event", 2), [(x.kind, x.adds) for x in d.drivers])

    # BR-02 (changed 2026-10-15, docs/18 section 3.1): a national day's headcount is
    # ignored only WHEN a forecast covers the day; with no forecast it is the fallback.
    # Until then this was one test asserting "always ignored" (the documented defect).
    def test_national_day_headcount_is_ignored_when_a_forecast_covers_the_day(self):
        d = build_demand(MEDICAL, MORNING, [], impacts=[ImpactLine(JAPAN_DAY, 2)],
                         forecast=ForecastInfo("peak", 230000, "v1"), demand_rules={("medical", "peak"): 1})
        self.assertEqual((d.event_uplift, d.volume_uplift, d.required_total), (0, 1, 3))  # 2 + 0 + 1, not + 2
        self.assertIn("ignored", [x.kind for x in d.drivers])

    def test_national_day_headcount_counts_when_no_forecast_covers_the_day(self):
        d = build_demand(MEDICAL, MORNING, [], impacts=[ImpactLine(JAPAN_DAY, 2)], forecast=None,
                         demand_rules={("medical", "peak"): 1})
        self.assertEqual((d.event_uplift, d.volume_uplift, d.required_total), (2, 0, 4))
        self.assertIn("fallback", [x.kind for x in d.drivers])
        self.assertNotIn("ignored", [x.kind for x in d.drivers])

    def test_a_normal_level_forecast_still_covers_the_event(self):
        # The forecast saw the day (and found it normal): the event's crowd is part of it.
        d = build_demand(MEDICAL, MORNING, [], impacts=[ImpactLine(JAPAN_DAY, 2)],
                         forecast=ForecastInfo("normal", 120000, "v1"), demand_rules={("medical", "normal"): 0})
        self.assertEqual((d.event_uplift, d.volume_uplift, d.required_total), (0, 0, 2))

    def test_never_both_event_and_volume_for_a_forecast_covered_event(self):
        for forecast in (None, ForecastInfo("high", 1, "v1"), ForecastInfo("peak", None, "s", "riyadh_scenario")):
            d = build_demand(MEDICAL, MORNING, [], impacts=[ImpactLine(JAPAN_DAY, 2)], forecast=forecast,
                             demand_rules={("medical", "high"): 1, ("medical", "peak"): 1})
            self.assertFalse(d.event_uplift and d.volume_uplift, forecast)

    def test_vip_headcount_counts_with_or_without_a_forecast(self):
        VIP2 = EventInfo("3", "vip_visit", "VIP", MORNING.starts_at, MORNING.ends_at)
        for forecast in (None, ForecastInfo("peak", 1, "v1")):
            d = build_demand(MEDICAL, MORNING, [], impacts=[ImpactLine(VIP2, 2)], forecast=forecast, demand_rules={})
            self.assertEqual(d.event_uplift, 2)

    def test_scenario_level_label_says_it_is_not_a_forecast(self):
        d = build_demand(MEDICAL, MORNING, [], forecast=ForecastInfo("peak", None, "scenario_assumption",
                                                                       "riyadh_scenario", "x"),
                         demand_rules={("medical", "peak"): 1})
        (drv,) = [x for x in d.drivers if x.kind == "forecast"]
        self.assertEqual(drv.adds, 1)
        self.assertIn("افتراض سيناريو محاكى", drv.label)
        self.assertNotIn("زائر", drv.label)   # no visitor number is shown for a scenario

    def test_forecast_level_adds_volume_through_demand_rule(self):
        d = build_demand(MEDICAL, MORNING, [], forecast=ForecastInfo("high", 215280, "v1"),
                         demand_rules={("medical", "high"): 1})
        self.assertEqual((d.volume_uplift, d.required_total), (1, 3))

    def test_no_forecast_means_no_volume(self):
        d = build_demand(MEDICAL, MORNING, [], forecast=None, demand_rules={("medical", "high"): 1})
        self.assertEqual(d.volume_uplift, 0)

    def test_forecast_without_matching_rule_adds_nothing(self):
        d = build_demand(MEDICAL, MORNING, [], forecast=ForecastInfo("peak", 1, "v1"), demand_rules={})
        self.assertEqual(d.volume_uplift, 0)

    def test_same_input_same_output(self):
        a = build_demand(MEDICAL, MORNING, S03_LINES)
        b = build_demand(MEDICAL, MORNING, list(reversed(S03_LINES)))
        self.assertEqual(a, b)


class DetectGapsTests(SimpleTestCase):
    def setUp(self):
        self.s03 = build_demand(MEDICAL, MORNING, S03_LINES)

    def test_g1_skill_gap_without_headcount_gap(self):
        gap = detect_gaps(self.s03, [person("E006", "security_guard"), person("E018", "paramedic", "adv_first_aid")])
        self.assertEqual(gap.headcount_gap, 0)
        (slot,) = gap.open_slots
        self.assertEqual(slot.must_have, {"paramedic", "adv_first_aid", "ja"})
        self.assertEqual((slot.source, gap.nature), ("event:1", "anticipated"))

    def test_japanese_speaking_paramedic_present_closes_it(self):
        gap = detect_gaps(self.s03, [person("E006"), person("E061", "paramedic", "adv_first_aid", "ja")])
        self.assertFalse(gap.is_open)
        self.assertIsNone(gap.nature)

    def test_bundle_skills_spread_over_two_people_do_not_count(self):
        gap = detect_gaps(self.s03, [person("A", "paramedic", "adv_first_aid"), person("B", "ja")])
        self.assertEqual(len(gap.open_slots), 1)

    def test_g2_headcount_gap_is_confirmed_and_requires_the_site_skill(self):
        # A-15 approved: the missing third person must hold the site's base skill.
        s10 = build_demand(SiteInfo("S10", 3, "crowd", 3, "E"), EVENING, [RequirementLine("crowd_control", 1)])
        gap = detect_gaps(s10, [person("E003", "security_license"), person("E046", "crowd_control")])
        (slot,) = gap.open_slots
        self.assertEqual((gap.headcount_gap, slot.source, slot.must_have, gap.nature),
                         (1, "headcount", frozenset({"crowd_control"}), "confirmed"))

    def test_generic_slot_is_open_to_anyone_if_policy_is_switched_off(self):
        s10 = build_demand(SiteInfo("S10", 3, "crowd", 3, "E"), EVENING, [RequirementLine("crowd_control", 1)])
        with mock.patch.object(policy, "GENERIC_SLOT_REQUIRES_BASE_SKILLS", False):
            gap = detect_gaps(s10, [person("E003"), person("E046", "crowd_control")])
        self.assertEqual(gap.open_slots[0].must_have, frozenset())

    def test_one_new_person_closes_skill_and_headcount_together(self):
        # 1 person short AND the only paramedic missing: one slot, not two.
        gap = detect_gaps(build_demand(MEDICAL, EVENING, S03_LINES), [person("X", "adv_first_aid")])
        self.assertEqual(gap.headcount_gap, 1)
        self.assertEqual([s.must_have for s in gap.open_slots], [frozenset({"paramedic"})])
        self.assertEqual(gap.nature, "confirmed")

    def test_event_raised_count_partly_missing_is_anticipated(self):
        d = build_demand(GATE, MORNING, [RequirementLine("security_license", 1),
                                         RequirementLine("security_license", 2, VIP)])
        people = [person("A", "security_license"), person("B"), person("C"), person("D")]
        gap = detect_gaps(d, people)
        self.assertEqual([s.source for s in gap.open_slots], ["event:3"])
        self.assertEqual(gap.nature, "anticipated")

    def test_event_raised_count_fully_missing_splits_base_and_event(self):
        d = build_demand(GATE, MORNING, [RequirementLine("security_license", 1),
                                         RequirementLine("security_license", 2, VIP)])
        gap = detect_gaps(d, [person("A"), person("B"), person("C"), person("D")])
        self.assertEqual(sorted(s.source for s in gap.open_slots), ["base", "event:3"])
        self.assertEqual(gap.nature, "confirmed")

    def test_slot_numbers_are_stable(self):
        people = [person("Z"), person("A", "adv_first_aid")]
        a = detect_gaps(build_demand(MEDICAL, MORNING, S03_LINES), people)
        b = detect_gaps(build_demand(MEDICAL, MORNING, S03_LINES), list(reversed(people)))
        self.assertEqual(a, b)
        self.assertEqual([s.number for s in a.open_slots], list(range(1, len(a.open_slots) + 1)))

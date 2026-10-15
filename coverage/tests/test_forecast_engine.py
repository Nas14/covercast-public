"""P0-4/P0-5: the simulated Riyadh peak scenario, end to end in the engine.

Every number below was derived by hand BEFORE the engine ran on the scenario
(data/demo/scenario_riyadh_peak/README.md, commit b8713f1) and cross-checked by
an independent CSV-only script. The scenario is SIMULATED.
"""

from datetime import date
from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase

from coverage import importer
from coverage.models import AttendanceForecast, Assignment, DemandRule, ForecastRun, Shift, Site
from coverage.services.approval import approve
from coverage.services.evaluation import evaluate_slot
from coverage.services.offers import offer_token, respond
from coverage.services.requirements import gap_for, window_gaps

PACK = settings.COVERCAST_DEMO_DATA_DIR
DAY = date(2030, 10, 24)
REQUIRED_AT_PEAK = {"S01": 3, "S02": 3, "S03": 3, "S04": 6, "S05": 5, "S06": 5, "S07": 2, "S08": 4,
                    "S09": 3, "S10": 4, "S11": 1, "S12": 5}
RANKED = [("E066", "80.83", "S07"), ("E010", "65.83", None), ("E036", "65.83", None), ("E040", "63.33", None),
          ("E046", "63.33", None), ("E058", "63.33", None), ("E073", "63.33", None), ("E014", "61.67", None),
          ("E034", "57.50", None), ("E019", "56.67", None), ("E013", "53.33", None)]
EXCLUDED = {"MISSING_SKILL": 61, "ALREADY_ASSIGNED": 3, "SCOPE_OUT": 2, "BREAKS_MINIMUM": 2, "CERT_EXPIRED": 1}


def demo_cells():
    """G1/G2 window (13-15 Oct): every cell's requirement and open slots."""
    return [(g.demand.site.code, str(g.demand.shift.date), g.demand.shift.slot, g.demand.required_total,
             g.open_slots, g.nature) for g in window_gaps(Shift.objects.filter(date__lte="2030-10-15"))]


def top(site, day, slot):
    r = evaluate_slot(Site.objects.get(code=site), Shift.objects.get(date=day, slot=slot), 1)
    return [(e.code, str(e.score)) for e in r.ranked]


class ScenarioCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        importer.load_folder(PACK, scenario="riyadh_peak")

    def s10(self):
        return Site.objects.get(code="S10"), Shift.objects.get(date=DAY, slot="morning")


class PeakDay(ScenarioCase):
    def test_requirements_follow_the_approved_uplift_table(self):
        gaps = {g.demand.site.code: g for g in window_gaps(Shift.objects.filter(date=DAY))}
        self.assertEqual({c: g.demand.required_total for c, g in gaps.items()}, REQUIRED_AT_PEAK)
        self.assertEqual(sum(g.demand.volume_uplift for g in gaps.values()), 13)
        for code in ("S07", "S11"):   # operations / safety: 0 at every level
            self.assertEqual(gaps[code].demand.volume_uplift, 0)

    def test_exactly_one_anticipated_gap_at_s10(self):
        open_ = [g for g in window_gaps(Shift.objects.filter(date=DAY)) if g.is_open]
        (g,) = open_
        self.assertEqual((g.demand.site.code, g.demand.base, g.demand.event_uplift, g.demand.volume_uplift),
                         ("S10", 3, 0, 1))
        self.assertEqual((len(g.present), g.nature), (3, "anticipated"))
        (slot,) = g.open_slots
        self.assertEqual((slot.must_have, slot.source), (frozenset({"crowd_control"}), "headcount"))
        (drv,) = [d for d in g.demand.drivers if d.kind == "forecast"]
        self.assertEqual(drv.adds, 1)
        self.assertIn("افتراض سيناريو محاكى", drv.label)

    def test_candidates_match_the_hand_derivation(self):
        r = evaluate_slot(*self.s10(), 1)
        self.assertEqual([(e.code, str(e.score), e.donor_site) for e in r.ranked], RANKED)
        self.assertEqual([e.tag for e in r.ranked[:3]], ["recommended", "backup", None])
        e066 = r.ranked[0]
        self.assertEqual({k: str(v) for k, v in e066.score_parts.items()},
                         {"proximity": "22.50", "hours_headroom": "20.83", "extra_skills": "20.00",
                          "donor_safety": "7.50", "fairness": "10.00"})
        counts = {}
        for e in r.excluded:
            counts[e.failed_rule] = counts.get(e.failed_rule, 0) + 1
        self.assertEqual(counts, EXCLUDED)
        self.assertEqual({e.code for e in r.excluded if e.failed_rule == "BREAKS_MINIMUM"}, {"E064", "E075"})

    def test_nearest_qualified_would_break_the_medical_centre(self):
        b = evaluate_slot(*self.s10(), 1).baseline
        self.assertEqual((b.code, b.travel_minutes, b.violates, b.breaks_site, b.donor_short_by),
                         ("E064", 10, "BREAKS_MINIMUM", "S12", 1))


class G1G2Unchanged(TestCase):
    def test_scenario_overlay_changes_nothing_in_13_to_15_october(self):
        importer.load_folder(PACK)
        before = demo_cells()
        g1, g2 = top("S03", "2030-10-14", "morning"), top("S10", "2030-10-15", "evening")
        importer.load_folder(PACK, scenario="riyadh_peak")
        self.assertEqual(before, demo_cells())
        self.assertEqual(top("S03", "2030-10-14", "morning"), g1)
        self.assertEqual(top("S10", "2030-10-15", "evening"), g2)
        self.assertEqual(g1, [("E061", "60.83"), ("E012", "45.00")])
        self.assertEqual(g2, [("E058", "50.00"), ("E013", "45.00")])


class LevelTransitions(ScenarioCase):
    def set_level(self, level):
        AttendanceForecast.objects.filter(date=DAY).update(level=level)

    def open_sites(self):
        return [g.demand.site.code for g in window_gaps(Shift.objects.filter(date=DAY)) if g.is_open]

    def test_normal_level_and_no_forecast_mean_no_gap(self):
        self.set_level("normal")
        self.assertEqual(self.open_sites(), [])
        self.set_level("peak")
        ForecastRun.objects.filter(mode="riyadh_scenario").update(is_active=False)
        self.assertEqual(self.open_sites(), [])

    def test_high_level_adds_one_at_gates_and_crowd_only(self):
        self.set_level("high")
        gaps = {g.demand.site.code: g.demand.volume_uplift for g in window_gaps(Shift.objects.filter(date=DAY))}
        self.assertEqual({c: n for c, n in gaps.items() if n}, {"S04": 1, "S05": 1, "S06": 1, "S10": 1})
        self.assertEqual(self.open_sites(), ["S10"])

    def test_no_demand_rules_means_no_volume(self):
        DemandRule.objects.all().delete()
        self.assertEqual(self.open_sites(), [])


class ApproveAndRespond(ScenarioCase):
    def test_supervisor_approves_e066_who_accepts_and_s10_is_covered(self):
        site, shift = self.s10()
        sup = get_user_model().objects.create_user("sup")
        now = shift.starts_at.replace(hour=5)
        with mock.patch("django.utils.timezone.now", return_value=now):
            result = approve(site, shift, "E066", idempotency_key="scenario-1", supervisor=sup, slot_number=1)
            self.assertEqual(result.moved_from_site, "S07")
            self.assertFalse(gap_for(site, shift).is_open)           # S10 covered (awaiting the answer)
            s07 = Site.objects.get(code="S07")
            self.assertFalse(gap_for(s07, shift).is_open)            # S07 keeps its 2
            answer = respond(offer_token(result.assignment), accept=True)
        self.assertEqual(answer.assignment.status, "confirmed")
        self.assertEqual(Assignment.objects.filter(shift=shift, site=site, status="confirmed").count(), 4)


class ScenarioPackValidation(TestCase):
    def setUp(self):
        importer.load_folder(PACK)
        self.before = importer.fingerprint()

    def problems(self, sc):
        return [str(p) for p in importer.validate_scenario(sc, importer.read_pack(PACK))]

    def test_committed_overlay_is_valid(self):
        self.assertEqual(self.problems(importer.read_scenario("riyadh_peak")), [])

    def test_wrong_category_or_missing_level_is_refused(self):
        sc = importer.read_scenario("riyadh_peak")
        rules = [dict(r) for r in sc.tables["demand_rule.csv"]]
        rules[0]["category"] = "medical"                       # security_gate is gates_security
        del rules[-1]                                          # safety @ peak missing
        bad = importer.Scenario(sc.name, {**sc.tables, "demand_rule.csv": rules}, sc.forecast_raw)
        text = "\n".join(self.problems(bad))
        self.assertIn("differs from the approved mapping", text)
        self.assertIn("no rule for site type 'safety' at level 'peak'", text)

    def test_overlay_cannot_reuse_a_pack_shift(self):
        sc = importer.read_scenario("riyadh_peak")
        shifts = [{"date": "2030-10-14", "label": "morning", "starts_at": "2030-10-14T07:00+03:00",
                   "ends_at": "2030-10-14T15:00+03:00"}]
        bad = importer.Scenario(sc.name, {**sc.tables, "shift.csv": shifts}, sc.forecast_raw)
        self.assertTrue(any("already exists in the pack" in p for p in self.problems(bad)))

    def test_overlay_level_file_must_be_a_riyadh_scenario(self):
        sc = importer.read_scenario("riyadh_peak")
        osaka = (settings.BASE_DIR / "forecast" / "out" / "export" / "osaka_replay.json").read_bytes()
        bad = importer.Scenario(sc.name, sc.tables, osaka)
        self.assertTrue(any("riyadh_scenario level file" in p for p in self.problems(bad)))

    def test_unknown_scenario_and_failed_load_change_nothing(self):
        with self.assertRaises(importer.PackError):
            importer.load_folder(PACK, scenario="nope")
        self.assertEqual(self.before, importer.fingerprint())

    def test_loading_the_scenario_twice_gives_the_same_database(self):
        importer.load_folder(PACK, scenario="riyadh_peak")
        first = importer.fingerprint()
        importer.load_folder(PACK, scenario="riyadh_peak")
        self.assertEqual(first, importer.fingerprint())
        self.assertNotEqual(first, self.before)

"""Phase 8a: the HTTP layer over the demo data.

Numbers are the ones already proven in Phases 5-6 (G1: Noura E061 60.83,
recommended, moved from S12; 74/2/1/1 excluded; baseline E053 breaks S05).
"""

from datetime import date

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import Client, TestCase

from coverage import importer, policy
from coverage.models import Assignment, CandidateEvaluation, CoverageRequirement, Shift, Site

G1_DAY, G2_DAY = "2030-10-14", "2030-10-15"


class ApiCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        importer.load_folder(settings.COVERCAST_DEMO_DATA_DIR)
        User = get_user_model()
        cls.sup = User.objects.create_user("supervisor", password="x-not-used")
        cls.sup.groups.add(Group.objects.create(name=policy.SUPERVISOR_GROUP))
        cls.other = User.objects.create_user("viewer")

    def setUp(self):
        self.c = Client()
        self.c.force_login(self.sup)

    def board(self, day=G1_DAY):
        r = self.c.get("/api/board", {"date": day})
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()

    def cell(self, board, slot, site):
        sh = next(s for s in board["shifts"] if s["slot"] == slot)
        return next(x for x in sh["sites"] if x["site_code"] == site)

    def g1_id(self):
        return self.cell(self.board(), "morning", "S03")["id"]

    def post(self, url, data, client=None):
        return (client or self.c).post(url, data, content_type="application/json")

    def assertError(self, r, status, code):
        self.assertEqual(r.status_code, status, r.content)
        self.assertEqual(r.json()["error"]["code"], code)
        self.assertEqual(set(r.json()["error"]), {"code", "message_ar", "details"})


class AccessTests(ApiCase):
    def test_anonymous_and_non_supervisor_are_refused_everywhere(self):
        rid = self.g1_id()
        a = Assignment.objects.first().id
        calls = [("get", "/api/board?date=2030-10-14"), ("get", f"/api/requirements/{rid}/candidates"),
                 ("post", f"/api/requirements/{rid}/approve"), ("post", f"/api/assignments/{a}/absence")]
        for who, code in ((Client(), "AUTH_REQUIRED"), (None, "NOT_SUPERVISOR")):
            if who is None:
                who = Client()
                who.force_login(self.other)
            for method, url in calls:
                with self.subTest(code=code, url=url):
                    r = getattr(who, method)(url, content_type="application/json") if method == "post" else who.get(url)
                    self.assertError(r, 403, code)

    def test_wrong_method(self):
        self.assertEqual(self.c.post("/api/board").status_code, 405)
        self.assertEqual(self.c.get(f"/api/requirements/{self.g1_id()}/approve").status_code, 405)

    def test_post_without_csrf_token_is_refused_with_json(self):
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.sup)
        r = self.post(f"/api/requirements/{self.g1_id()}/approve",
                      {"employee_code": "E061", "idempotency_key": "k"}, client=strict)
        self.assertError(r, 403, "CSRF_FAILED")
        self.assertFalse(Assignment.objects.filter(origin="replacement").exists())

    def test_post_with_csrf_token_passes(self):
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.sup)
        from django.utils.crypto import get_random_string
        token = get_random_string(32)       # what a browser holds after loading our page (cookie == header)
        strict.cookies[settings.CSRF_COOKIE_NAME] = token
        r = strict.post(f"/api/requirements/{self.g1_id()}/approve",
                        {"employee_code": "E061", "idempotency_key": "k"},
                        content_type="application/json", HTTP_X_CSRFTOKEN=token)
        self.assertEqual(r.status_code, 201, r.content)


class BoardTests(ApiCase):
    def test_monday_has_exactly_g1_open(self):
        b = self.board()
        self.assertEqual(b["summary"], {"cells": 24, "open": 1, "confirmed": 0, "anticipated": 1})
        g1 = self.cell(b, "morning", "S03")
        self.assertEqual((g1["gap_nature"], g1["required_total"], g1["present"], g1["headcount_gap"]),
                         ("anticipated", 2, 2, 0))
        self.assertEqual(g1["open_slots"], [{"slot": 1, "must_have": ["adv_first_aid", "ja", "paramedic"],
                                             "must_have_ar": ["إسعاف أولي متقدم", "اليابانية", "مسعف"],
                                             "source": "event:1"}])
        self.assertEqual([p["employee_code"] for p in g1["assignments"]], ["E006", "E018"])
        self.assertEqual(g1["importance"], 5)
        self.assertEqual((g1["shift"]["starts_at"], g1["shift"]["ends_at"]),
                         ("2030-10-14T07:00:00+03:00", "2030-10-14T15:00:00+03:00"))   # Riyadh time

    def test_tuesday_shows_e019_assigned_but_absent(self):
        g2 = self.cell(self.board(G2_DAY), "evening", "S10")
        self.assertEqual((g2["gap_nature"], g2["present"], g2["open_slots"][0]["must_have"]),
                         ("confirmed", 2, ["crowd_control"]))
        e019 = next(p for p in g2["assignments"] if p["employee_code"] == "E019")
        self.assertEqual((e019["status"], e019["absent"]), ("confirmed", True))

    def test_query_count_is_bounded(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        with CaptureQueriesContext(connection) as q:
            self.board()
        # first open: 348 (snapshot rows are created); later opens: 299. Was 395/~440 before Phase 9.
        self.assertLess(len(q.captured_queries), 380)

    def test_bad_or_missing_date(self):
        self.assertError(self.c.get("/api/board", {"date": "14/10/2030"}), 400, "INVALID_INPUT")
        self.assertError(self.c.get("/api/board"), 400, "INVALID_INPUT")

    def test_arabic_is_sent_as_text_not_escapes(self):
        r = self.c.get("/api/board", {"date": G1_DAY})
        self.assertIn("نقطة الإسعاف 3".encode(), r.content)


class CandidatesTests(ApiCase):
    def test_g1_answer_matches_the_hand_derivation(self):
        r = self.c.get(f"/api/requirements/{self.g1_id()}/candidates")
        self.assertEqual(r.status_code, 200, r.content)
        d = r.json()
        noura, khaled = d["candidates"]
        self.assertEqual((noura["employee_code"], noura["score"], noura["tag"], noura["requirements_met"],
                          noura["travel"], noura["hours_after"], noura["donor_site"]),
                         ("E061", 60.83, "recommended", "3/3", 10, 32, "S12"))
        self.assertEqual(noura["score_parts"], {"proximity": 22.5, "hours_headroom": 8.33, "extra_skills": 20.0,
                                                "donor_safety": 0.0, "fairness": 10.0})
        self.assertEqual((khaled["employee_code"], khaled["score"], khaled["tag"]), ("E012", 45.0, "backup"))
        self.assertEqual(list(d["excluded_summary"].items()),       # in check order
                         [("ALREADY_ASSIGNED", 2), ("MISSING_SKILL", 74), ("CERT_EXPIRED", 1), ("SCOPE_OUT", 1)])
        reem = next(x for x in d["excluded"] if x["employee_code"] == "E060")
        self.assertEqual(reem["reason_ar"], "شهادة إسعاف أولي متقدم تنتهي في 2030-10-13، قبل موعد الوردية")
        self.assertEqual(reem["detail"]["skills"], ["adv_first_aid"])          # codes stay in the data
        missing = next(x for x in d["excluded"] if x["failed_rule"] == "MISSING_SKILL")
        self.assertNotRegex(missing["reason_ar"], "[a-z_]{3,}")                # no English codes in Arabic text
        self.assertEqual(d["baseline"]["employee"], "E053")
        self.assertEqual((d["baseline"]["violates"], d["baseline"]["breaks_site"]), ("SCOPE_OUT", "S05"))
        self.assertEqual(CandidateEvaluation.objects.filter(run_id=d["run_id"]).count(), 80)

    def test_reasons_are_the_team_templates(self):
        d = self.c.get(f"/api/requirements/{self.g1_id()}/candidates").json()
        texts = [x["text_ar"] for x in d["candidates"][0]["reasons"]]
        self.assertIn("يستوفي 3 من 3 متطلبات الموقع", texts)
        # E-13: only the nearest is called "the nearest", and the Arabic count agrees
        self.assertIn("الأقرب: على بعد 10 دقائق من الموقع", texts)
        khaled = {x["code"]: x["text_ar"] for x in d["candidates"][1]["reasons"]}
        self.assertNotIn("NEAR", khaled)
        self.assertEqual(khaled["TRAVEL"], "على بعد 60 دقيقة من الموقع")
        self.assertTrue(all("{" not in t for t in texts), texts)   # no unfilled placeholder

    def test_covered_site_and_unknown_id(self):
        s01 = self.cell(self.board(), "morning", "S01")["id"]
        self.assertError(self.c.get(f"/api/requirements/{s01}/candidates"), 409, "GAP_ALREADY_FILLED")
        self.assertError(self.c.get("/api/requirements/999999/candidates"), 404, "NOT_FOUND")
        self.assertError(self.c.get(f"/api/requirements/{s01}/candidates?slot=x"), 400, "INVALID_INPUT")


class ApproveTests(ApiCase):
    def url(self):
        return f"/api/requirements/{self.g1_id()}/approve"

    def test_approve_then_replay_then_second_supervisor(self):
        r = self.post(self.url(), {"employee_code": "E061", "idempotency_key": "click-1"})
        self.assertEqual(r.status_code, 201, r.content)
        d = r.json()
        self.assertEqual((d["employee_code"], d["status"], d["replayed"], d["moved_from_site"]),
                         ("E061", "planned", False, "S12"))
        self.assertIn("/offer/", d["offer_url"])
        again = self.post(self.url(), {"employee_code": "E061", "idempotency_key": "click-1"})
        self.assertEqual((again.status_code, again.json()["assignment_id"], again.json()["replayed"]),
                         (200, d["assignment_id"], True))
        self.assertError(self.post(self.url(), {"employee_code": "E012", "idempotency_key": "other"}),
                         409, "GAP_ALREADY_FILLED")

    def test_refusals_carry_the_rule(self):
        r = self.post(self.url(), {"employee_code": "E060", "idempotency_key": "r"})
        self.assertError(r, 409, "CANDIDATE_NOT_ELIGIBLE")
        self.assertEqual(r.json()["error"]["details"]["failed_rule"], "CERT_EXPIRED")
        self.assertError(self.post(self.url(), {"employee_code": "E999", "idempotency_key": "r"}),
                         404, "EMPLOYEE_NOT_FOUND")

    def test_bad_bodies(self):
        for bad in ({}, {"employee_code": "E061"}, {"employee_code": 61, "idempotency_key": "k"},
                    {"employee_code": "E061", "idempotency_key": "k", "slot": "1"}, [1, 2]):
            with self.subTest(bad=bad):
                self.assertError(self.post(self.url(), bad), 400, "INVALID_INPUT")
        r = self.c.post(self.url(), "{not json", content_type="application/json")
        self.assertError(r, 400, "INVALID_INPUT")
        self.assertFalse(Assignment.objects.filter(origin="replacement").exists())


class AbsenceTests(ApiCase):
    def test_report_twice_and_refusals(self):
        e018 = Assignment.objects.get(employee__code="E018", shift__date=date(2030, 10, 14), shift__slot="morning")
        url = f"/api/assignments/{e018.id}/absence"
        r = self.post(url, {"kind": "no_show"})
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual((r.json()["created"], r.json()["requirement"]["gap_nature"]), (True, "confirmed"))
        self.assertEqual(self.post(url, {"kind": "no_show"}).json()["created"], False)
        self.assertError(self.post(url, {"kind": "vacation"}), 400, "INVALID_INPUT")
        self.assertError(self.post("/api/assignments/999999/absence", {"kind": "sick"}), 404, "NOT_FOUND")


class OfferPageTests(ApiCase):
    def approve_noura(self):
        r = self.post(f"/api/requirements/{self.g1_id()}/approve", {"employee_code": "E061", "idempotency_key": "o"})
        return r.json()["offer_url"].split("testserver", 1)[1]

    def test_employee_accepts_without_logging_in(self):
        url = self.approve_noura()
        anon = Client()
        page = anon.get(url)
        self.assertContains(page, "مرحبًا نوره")
        self.assertContains(page, "نقطة الإسعاف 3 - بجوار الجناح الياباني")
        self.assertContains(page, "ستنتقل من المركز الطبي")
        self.assertContains(page, 'value="accept"')
        done = anon.post(url, {"action": "accept"})
        self.assertContains(done, 'data-result="confirmed"')
        self.assertEqual(anon.get(url).status_code, 200)
        self.assertNotContains(anon.get(url), 'value="accept"')     # answered: no buttons any more

    def test_decline_restores_s12_and_g1_reopens(self):
        url = self.approve_noura()
        Client().post(url, {"action": "decline"})
        b = self.board()
        self.assertEqual(self.cell(b, "morning", "S03")["gap_nature"], "anticipated")
        s12 = self.cell(b, "morning", "S12")["assignments"]
        self.assertIn(("E061", "confirmed"), [(p["employee_code"], p["status"]) for p in s12])

    def test_unknown_action_changes_nothing(self):
        # Without this check a stray value would fall through to "decline".
        url = self.approve_noura()
        r = Client().post(url, {"action": "maybe"})
        self.assertEqual(r.status_code, 400)
        a = Assignment.objects.get(employee__code="E061", site__code="S03")
        self.assertEqual((a.status, a.responded_at), ("planned", None))

    def test_tampered_link(self):
        url = self.approve_noura()
        r = Client().get(url[:-2] + "zz")
        self.assertEqual(r.status_code, 403)
        self.assertContains(r, "OFFER_INVALID", status_code=403)


class FullStoryOverHttp(ApiCase):
    def test_absence_candidates_approve_accept_board(self):
        e019 = Assignment.objects.get(employee__code="E019", shift__date=date(2030, 10, 15), shift__slot="evening")
        self.post(f"/api/assignments/{e019.id}/absence", {"kind": "no_show"})
        rid = self.cell(self.board(G2_DAY), "evening", "S10")["id"]
        top = self.c.get(f"/api/requirements/{rid}/candidates").json()["candidates"][0]
        self.assertEqual(top["employee_code"], "E058")
        offer = self.post(f"/api/requirements/{rid}/approve",
                          {"employee_code": top["employee_code"], "idempotency_key": "story"}).json()["offer_url"]
        waiting = self.cell(self.board(G2_DAY), "evening", "S10")
        self.assertIsNone(waiting["gap_nature"])
        self.assertTrue(next(p for p in waiting["assignments"] if p["employee_code"] == "E058")["awaiting_response"])
        Client().post(offer.split("testserver", 1)[1], {"action": "accept"})
        done = self.cell(self.board(G2_DAY), "evening", "S10")
        e058 = next(p for p in done["assignments"] if p["employee_code"] == "E058")
        self.assertEqual((e058["status"], e058["awaiting_response"]), ("confirmed", False))


class ApiContractTests(ApiCase):
    """Field names ARE the contract with the UI (docs/06). A rename fails here."""

    REQUIREMENT = {"id", "site_code", "site_name", "shift", "base", "event_uplift", "volume_uplift",
                   "required_total", "present", "headcount_gap", "gap_nature", "open_slots", "drivers"}
    BOARD_SITE = REQUIREMENT | {"zone", "importance", "site_type", "assignments"}
    SHIFT = {"date", "slot", "label_ar", "starts_at", "ends_at"}
    POST = {"assignment_id", "employee_code", "name", "status", "origin", "absent", "awaiting_response"}
    CANDIDATE = {"rank", "employee_code", "name", "category", "tag", "score", "score_parts", "requirements_met",
                 "travel", "hours_after", "hours_cap", "donor_site", "reasons"}
    SCORE_PARTS = {"proximity", "hours_headroom", "extra_skills", "donor_safety", "fairness"}
    EXCLUDED = {"employee_code", "name", "category", "failed_rule", "reason_ar", "detail"}
    BASELINE = {"employee", "name", "travel", "violates", "violates_ar", "breaks_site", "donor_short_by"}
    APPROVAL = {"assignment_id", "employee_code", "status", "replayed", "moved_from_site", "offer_url"}

    def test_api_contract_fields(self):
        b = self.board()
        self.assertEqual(set(b), {"date", "shifts", "summary"})
        self.assertEqual(set(b["shifts"][0]), self.SHIFT | {"sites"})
        site = self.cell(b, "morning", "S03")
        self.assertEqual(set(site), self.BOARD_SITE)
        self.assertEqual(set(site["shift"]), self.SHIFT)
        self.assertEqual(set(site["assignments"][0]), self.POST)
        self.assertEqual(set(site["open_slots"][0]), {"slot", "must_have", "must_have_ar", "source"})

        d = self.c.get(f"/api/requirements/{site['id']}/candidates").json()
        self.assertEqual(set(d), {"requirement", "slot", "run_id", "candidates", "excluded", "excluded_summary",
                                  "baseline"})
        self.assertEqual(set(d["requirement"]), self.REQUIREMENT)
        self.assertEqual(set(d["candidates"][0]), self.CANDIDATE)
        self.assertEqual(set(d["candidates"][0]["score_parts"]), self.SCORE_PARTS)
        self.assertEqual(set(d["candidates"][0]["reasons"][0]), {"code", "kind", "text_ar"})
        self.assertEqual(set(d["excluded"][0]), self.EXCLUDED)
        self.assertEqual(set(d["baseline"]), self.BASELINE)

        a = self.post(f"/api/requirements/{site['id']}/approve",
                      {"employee_code": "E061", "idempotency_key": "c"}).json()
        self.assertEqual(set(a), self.APPROVAL)

    def test_failed_rule_values_are_the_eight_codes(self):
        from coverage.codes import ExclusionCode
        d = self.c.get(f"/api/requirements/{self.g1_id()}/candidates").json()
        self.assertTrue({x["failed_rule"] for x in d["excluded"]} <= set(ExclusionCode.values))


class ForecastApiTests(TestCase):
    """GET /api/forecast (P0-6): level, provenance and staffing effect; same dict as the board panel."""

    FORECAST = {"level", "level_ar", "mode", "mode_ar", "data_origin", "provenance_ar", "run", "visitors",
                "staffing_effect"}
    VISITORS = {"p10", "p50", "p90", "actual", "actual_level", "as_of", "horizon_days", "threshold_high",
                "threshold_peak"}

    @classmethod
    def setUpTestData(cls):
        from coverage.forecast_import import import_path
        importer.load_folder(settings.COVERCAST_DEMO_DATA_DIR, scenario="riyadh_peak")
        import_path(settings.BASE_DIR / "forecast" / "out" / "export" / "osaka_replay.json")
        cls.sup = get_user_model().objects.create_user("supervisor")
        cls.sup.groups.add(Group.objects.create(name=policy.SUPERVISOR_GROUP))

    def setUp(self):
        self.c = Client()
        self.c.force_login(self.sup)

    def get(self, day):
        r = self.c.get("/api/forecast", {"date": day})
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()

    def test_scenario_day(self):
        d = self.get("2030-10-24")
        self.assertEqual(set(d), {"date", "forecast", "rules_loaded"})
        f = d["forecast"]
        self.assertEqual(set(f), self.FORECAST)
        self.assertEqual((f["level"], f["mode"], f["data_origin"], f["visitors"]),
                         ("peak", "riyadh_scenario", "simulated", None))
        eff = f["staffing_effect"]
        self.assertEqual((eff["applies_to_shifts"], eff["total_added"]), (True, 13))
        by = {c["category"]: (c["extra_staff_per_site"], c["sites"]) for c in eff["by_category"]}
        self.assertEqual(by, {"gates_security": (2, ["S04", "S05", "S06"]), "crowd_management": (1, ["S10"]),
                              "visitor_services": (1, ["S08", "S09"]),
                              "medical": (1, ["S01", "S02", "S03", "S12"]),
                              "operations_safety": (0, ["S07", "S11"])})

    def test_osaka_replay_day(self):
        f = self.get("2025-09-13")["forecast"]
        self.assertEqual((f["mode"], f["data_origin"], f["run"]["model"]), ("osaka_replay", "official", "seasonal_naive"))
        self.assertEqual(set(f["visitors"]), self.VISITORS)
        v = f["visitors"]
        self.assertEqual((v["p50"], v["actual"], v["actual_level"], v["as_of"], v["horizon_days"]),
                         (209837, 218130, "peak", "2025-09-08", 5))
        self.assertFalse(f["staffing_effect"]["applies_to_shifts"])
        self.assertIn("ليس توقعًا للرياض 2030", f["provenance_ar"])

    def test_day_without_forecast(self):
        self.assertEqual(self.get("2030-10-14"), {"date": "2030-10-14", "forecast": None, "rules_loaded": True})

    def test_access_method_and_input(self):
        r = Client().get("/api/forecast", {"date": "2030-10-24"})
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (403, "AUTH_REQUIRED"))
        other = Client()
        other.force_login(get_user_model().objects.create_user("viewer"))
        r = other.get("/api/forecast", {"date": "2030-10-24"})
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (403, "NOT_SUPERVISOR"))
        self.assertEqual(self.c.post("/api/forecast").status_code, 405)
        r = self.c.get("/api/forecast", {"date": "24-10-2030"})
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (400, "INVALID_INPUT"))

    def test_board_contract_is_unchanged_by_the_forecast(self):
        b = self.c.get("/api/board", {"date": "2030-10-24"}).json()
        self.assertEqual(set(b), {"date", "shifts", "summary"})
        self.assertEqual(b["summary"], {"cells": 12, "open": 1, "confirmed": 0, "anticipated": 1})

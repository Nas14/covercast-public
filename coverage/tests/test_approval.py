"""Phase 6 gates on the real demo data: approval, the employee's answer, absence.

Every expectation below follows from Phase 5 results already proven by hand:
G1 (S03, Mon 14 Oct morning) -> Noura E061 rank 1, she works at S12 in that
same shift; Khaled E012 rank 2, off duty. G2 (S10, Tue 15 Oct evening) ->
Ghada E058 rank 1, off duty.
"""

from datetime import date, datetime
from unittest import mock
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase

from coverage import importer, policy
from coverage.models import Assignment, CoverageRequirement, Shift, Site
from coverage.services.absence import report_absence
from coverage.services.approval import approve
from coverage.services.errors import CoverCastError
from coverage.services.evaluation import evaluate_slot
from coverage.services.offers import offer_token, respond
from coverage.services.requirements import gap_for
from workforce.models import Absence, EmployeeSkill

RIYADH = ZoneInfo("Asia/Riyadh")
BEFORE_G1 = datetime(2030, 10, 13, 20, 0, tzinfo=RIYADH)   # the evening before
AFTER_G1 = datetime(2030, 10, 14, 16, 0, tzinfo=RIYADH)    # the shift ended at 15:00


class DemoCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        importer.load_folder(settings.COVERCAST_DEMO_DATA_DIR)
        cls.sup = get_user_model().objects.create_user("supervisor")
        cls.s03 = Site.objects.get(code="S03")
        cls.s12 = Site.objects.get(code="S12")
        cls.g1 = Shift.objects.get(date=date(2030, 10, 14), slot="morning")
        cls.s10 = Site.objects.get(code="S10")
        cls.g2 = Shift.objects.get(date=date(2030, 10, 15), slot="evening")

    def approve_g1(self, code="E061", key="k-1", **kw):
        kw.setdefault("now", BEFORE_G1)
        return approve(self.s03, self.g1, code, idempotency_key=key, supervisor=self.sup, **kw)

    def assertRefused(self, expected, fn, *a, **kw):
        before = list(Assignment.objects.order_by("pk").values_list("pk", "status"))
        with self.assertRaises(CoverCastError) as cm:
            fn(*a, **kw)
        self.assertEqual(cm.exception.code, expected)
        # a refusal changes nothing at all
        self.assertEqual(list(Assignment.objects.order_by("pk").values_list("pk", "status")), before)
        return cm.exception


class ApproveTests(DemoCase):
    def test_noura_is_moved_from_s12_and_g1_closes(self):
        old = Assignment.objects.get(employee__code="E061", shift=self.g1)
        self.assertEqual((old.site.code, old.status), ("S12", "confirmed"))

        r = self.approve_g1()
        a = r.assignment
        self.assertFalse(r.replayed)
        self.assertEqual(r.moved_from_site, "S12")
        self.assertEqual((a.site.code, a.status, a.origin, a.approved_by, a.approved_at),
                         ("S03", "planned", "replacement", self.sup, BEFORE_G1))
        self.assertEqual((a.moved_from, a.moved_from_status), (old, "confirmed"))
        old.refresh_from_db()
        self.assertEqual(old.status, "cancelled")
        self.assertFalse(gap_for(self.s03, self.g1).is_open)           # G1 closed
        self.assertFalse(gap_for(self.s12, self.g1).is_open)           # S12 not broken (rule 8)
        self.assertEqual(CoverageRequirement.objects.get(site=self.s03, shift=self.g1).computed_at, BEFORE_G1)

    def test_off_duty_person_is_called_in_without_any_move(self):
        r = approve(self.s10, self.g2, "E058", idempotency_key="g2", supervisor=self.sup, now=BEFORE_G1)
        self.assertIsNone(r.moved_from_site)
        self.assertIsNone(r.assignment.moved_from)
        self.assertFalse(gap_for(self.s10, self.g2).is_open)

    def test_double_click_creates_one_assignment(self):
        first = self.approve_g1()
        n = Assignment.objects.count()
        second = self.approve_g1()
        self.assertTrue(second.replayed)
        self.assertEqual(second.assignment.pk, first.assignment.pk)
        self.assertEqual(Assignment.objects.count(), n)

    def test_same_key_for_a_different_request_is_refused(self):
        self.approve_g1()
        self.assertRefused("IDEMPOTENCY_KEY_REUSED", self.approve_g1, code="E012")

    def test_second_supervisor_finds_the_gap_already_filled(self):
        self.approve_g1(code="E061", key="sup-A")
        e = self.assertRefused("GAP_ALREADY_FILLED", self.approve_g1, code="E012", key="sup-B")
        self.assertEqual(e.http_status, 409)

    def test_certificate_changed_after_viewing_is_caught(self):
        shown = evaluate_slot(self.s03, self.g1, persist=False)
        self.assertEqual(shown.ranked[0].code, "E061")                 # what the supervisor saw
        EmployeeSkill.objects.filter(employee__code="E061", skill__code="paramedic").update(
            expires_on=date(2030, 10, 13))                              # then the record was corrected
        e = self.assertRefused("CANDIDATE_NOT_ELIGIBLE", self.approve_g1)
        self.assertEqual((e.details["failed_rule"], e.details["detail"]["date"]),
                         ("CERT_EXPIRED", "2030-10-13"))

    def test_absence_reported_after_viewing_is_caught(self):
        evaluate_slot(self.s03, self.g1, persist=False)
        Absence.objects.create(employee=Assignment.objects.get(employee__code="E061", shift=self.g1).employee,
                               kind="sick", starts_at=self.g1.starts_at, ends_at=self.g1.ends_at,
                               reported_at=BEFORE_G1)
        e = self.assertRefused("CANDIDATE_NOT_ELIGIBLE", self.approve_g1)
        self.assertEqual(e.details["failed_rule"], "ABSENT")

    def test_people_the_ranking_excluded_are_refused_with_the_same_reason(self):
        for code, rule in (("E060", "CERT_EXPIRED"), ("E053", "SCOPE_OUT"), ("E018", "ALREADY_ASSIGNED"),
                           ("E001", "MISSING_SKILL")):
            with self.subTest(code=code):
                e = self.assertRefused("CANDIDATE_NOT_ELIGIBLE", self.approve_g1, code=code, key=code)
                self.assertEqual(e.details["failed_rule"], rule)

    def test_unknown_employee_ended_shift_and_bad_input(self):
        self.assertEqual(self.assertRefused("EMPLOYEE_NOT_FOUND", self.approve_g1, code="E999").http_status, 404)
        self.assertRefused("SHIFT_ENDED", self.approve_g1, now=AFTER_G1)
        self.assertEqual(self.assertRefused("INVALID_INPUT", self.approve_g1, key="").http_status, 400)
        self.assertRefused("INVALID_INPUT", self.approve_g1, key="x" * 65)

    def test_error_has_the_agreed_shape(self):
        self.approve_g1()
        e = self.assertRefused("GAP_ALREADY_FILLED", self.approve_g1, code="E012", key="k-2")
        body = e.as_dict()["error"]
        self.assertEqual(set(body), {"code", "message_ar", "details"})
        self.assertEqual(body["message_ar"], "سُدّت هذه الفجوة بالفعل")

    def test_database_refuses_a_second_active_assignment_even_without_the_service(self):
        from django.db import IntegrityError, transaction
        old = Assignment.objects.get(employee__code="E061", shift=self.g1)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Assignment.objects.create(employee=old.employee, site=self.s03, shift=self.g1, status="planned",
                                      origin="replacement", approved_by=self.sup, approved_at=BEFORE_G1)


class OfferTests(DemoCase):
    def test_accept_confirms_and_answering_twice_is_harmless(self):
        token = offer_token(self.approve_g1().assignment)
        r = respond(token, accept=True, now=BEFORE_G1)
        self.assertEqual((r.assignment.status, r.assignment.responded_at, r.repeated), ("confirmed", BEFORE_G1, False))
        self.assertTrue(respond(token, accept=True, now=BEFORE_G1).repeated)
        self.assertRefused("OFFER_CLOSED", respond, token, accept=False, now=BEFORE_G1)

    def test_decline_reopens_g1_and_puts_noura_back_exactly_as_before(self):
        before = evaluate_slot(self.s03, self.g1, persist=False)
        r = respond(offer_token(self.approve_g1().assignment), accept=False, now=BEFORE_G1)
        self.assertEqual((r.assignment.status, r.restored_site), ("cancelled", "S12"))
        self.assertEqual(Assignment.objects.get(employee__code="E061", site=self.s12, shift=self.g1).status,
                         "confirmed")
        after = evaluate_slot(self.s03, self.g1, persist=False)       # same world as before the approval
        self.assertEqual([(v.code, v.score, v.rank) for v in after.ranked],
                         [(v.code, v.score, v.rank) for v in before.ranked])

    def test_khaled_can_be_approved_after_noura_declines(self):
        respond(offer_token(self.approve_g1().assignment), accept=False, now=BEFORE_G1)
        r = self.approve_g1(code="E012", key="k-2")
        self.assertEqual(r.assignment.status, "planned")

    def test_forged_tampered_or_wrong_kind_of_link_is_refused(self):
        token = offer_token(self.approve_g1().assignment)
        for bad in ("abc", token[:-1] + ("A" if token[-1] != "A" else "B"), token.replace(":", ":9", 1)):
            with self.subTest(bad=bad):
                self.assertEqual(self.assertRefused("OFFER_INVALID", respond, bad, accept=True).http_status, 403)
        schedule = Assignment.objects.get(employee__code="E018", shift=self.g1)
        self.assertRefused("OFFER_INVALID", respond, offer_token(schedule), accept=True)

    def test_old_link_and_ended_shift_are_refused(self):
        token = offer_token(self.approve_g1().assignment)
        with mock.patch.object(policy, "OFFER_TOKEN_MAX_AGE_HOURS", -1):
            self.assertRefused("OFFER_EXPIRED", respond, token, accept=True)
        self.assertRefused("OFFER_EXPIRED", respond, token, accept=True, now=AFTER_G1)

    def test_withdrawn_offer_cannot_be_accepted(self):
        a = self.approve_g1().assignment
        Assignment.objects.filter(pk=a.pk).update(status="cancelled")
        self.assertRefused("OFFER_CLOSED", respond, offer_token(a), accept=True, now=BEFORE_G1)


class AbsenceTests(DemoCase):
    def test_paramedic_no_show_turns_g1_confirmed_and_twice_is_once(self):
        a = Assignment.objects.get(employee__code="E018", shift=self.g1)
        self.assertEqual(gap_for(self.s03, self.g1).nature, "anticipated")
        r = report_absence(a, "no_show", now=BEFORE_G1)
        self.assertTrue(r.created)
        self.assertEqual((r.gap.nature, r.gap.headcount_gap), ("confirmed", 1))
        a.refresh_from_db()
        self.assertEqual(a.status, "confirmed")                         # the Absence row is the truth
        self.assertFalse(report_absence(a, "no_show", now=BEFORE_G1).created)
        self.assertEqual(Absence.objects.filter(employee=a.employee).count(), 1)

    def test_bad_kind_and_inactive_assignment_are_refused(self):
        a = Assignment.objects.get(employee__code="E018", shift=self.g1)
        self.assertRefused("INVALID_INPUT", report_absence, a, "holiday", now=BEFORE_G1)
        self.assertRefused("SHIFT_ENDED", report_absence, a, "sick", now=AFTER_G1)
        Assignment.objects.filter(pk=a.pk).update(status="cancelled")
        self.assertRefused("ASSIGNMENT_NOT_ACTIVE", report_absence, a, "sick", now=BEFORE_G1)

    def test_the_whole_story_absence_rank_approve_accept(self):
        report_absence(Assignment.objects.get(employee__code="E019", shift=self.g2), "no_show", now=BEFORE_G1)
        top = evaluate_slot(self.s10, self.g2).ranked[0]
        self.assertEqual(top.code, "E058")
        a = approve(self.s10, self.g2, top.code, idempotency_key="story", supervisor=self.sup,
                    now=BEFORE_G1).assignment
        respond(offer_token(a), accept=True, now=BEFORE_G1)
        a.refresh_from_db()
        self.assertEqual(a.status, "confirmed")
        self.assertFalse(gap_for(self.s10, self.g2).is_open)

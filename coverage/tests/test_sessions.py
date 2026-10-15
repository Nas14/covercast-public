"""The usability-session timing report: numbers a researcher will quote."""

import io
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from coverage import importer
from coverage.models import Shift, Site
from coverage.services.approval import approve
from coverage.services.evaluation import evaluate_slot
from coverage.services.offers import offer_token, respond
from coverage.services.sessions import approval_timings

T0 = datetime(2030, 10, 13, 20, 0, 0, tzinfo=ZoneInfo("Asia/Riyadh"))


class SessionTimingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        importer.load_folder(settings.COVERCAST_DEMO_DATA_DIR)
        cls.sup = get_user_model().objects.create_user("s")
        cls.s03 = Site.objects.get(code="S03")
        cls.g1 = Shift.objects.get(date=date(2030, 10, 14), slot="morning")

    def run_session(self):
        evaluate_slot(self.s03, self.g1, now=T0 - timedelta(minutes=5))       # an earlier look
        evaluate_slot(self.s03, self.g1, now=T0)                              # the screen opened again
        a = approve(self.s03, self.g1, "E061", idempotency_key="ux", supervisor=self.sup,
                    now=T0 + timedelta(seconds=43)).assignment
        respond(offer_token(a), accept=True, now=T0 + timedelta(seconds=43 + 95))

    def test_time_counts_from_the_last_opening_before_the_approval(self):
        self.run_session()
        (t,) = approval_timings()
        self.assertEqual((t.seconds_to_approve, t.seconds_to_respond), (43, 95))
        self.assertEqual(t.employee, "نوره القحطاني")

    def test_report_prints_the_numbers_and_the_under_60_count(self):
        self.run_session()
        out = io.StringIO()
        call_command("session_report", stdout=out)
        text = out.getvalue()
        self.assertIn("20:00:00", text)
        self.assertIn("زمن الاعتماد 43 ث", text)
        self.assertIn("بعد 95 ث", text)
        self.assertIn("اعتمادات أقل من 60 ثانية: 1 من 1", text)

    def test_empty_report_says_so(self):
        out = io.StringIO()
        call_command("session_report", stdout=out)
        self.assertIn("لا توجد اعتمادات بعد", out.getvalue())

"""Board polling (GET /board/live): read-only change check, fragment, permissions.

The browser side (5-second poll, hidden tab, network errors, two sessions) is
in test_e2e_browser.BoardUpdatesItself.
"""

from datetime import date
from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import connection
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext

from coverage import importer, policy
from coverage.models import CoverageRequirement, Shift, Site
from coverage.services.approval import approve
from coverage.services.board import board_version
from coverage.services.offers import offer_token, respond
from workforce.models import Absence

DAY = date(2030, 10, 24)
URL = "/board/live"


class LiveCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        importer.load_folder(settings.COVERCAST_DEMO_DATA_DIR, scenario="riyadh_peak")
        cls.sup = get_user_model().objects.create_user("supervisor", first_name="نوف")
        cls.sup.groups.add(Group.objects.create(name=policy.SUPERVISOR_GROUP))

    def setUp(self):
        self.c = Client()
        self.c.force_login(self.sup)

    def get(self, v=None, day=DAY, client=None):
        params = {"date": day.isoformat()}
        if v is not None:
            params["v"] = v
        return (client or self.c).get(URL, params)

    def approve_e066(self):
        site, shift = Site.objects.get(code="S10"), Shift.objects.get(date=DAY, slot="morning")
        with mock.patch("django.utils.timezone.now", return_value=shift.starts_at.replace(hour=5)):
            return approve(site, shift, "E066", idempotency_key="live-1", supervisor=self.sup, slot_number=1)


class VersionCheck(LiveCase):
    def test_same_version_answers_204_without_writing(self):
        v = board_version(DAY)
        with CaptureQueriesContext(connection) as q:
            r = self.get(v)
        self.assertEqual(r.status_code, 204)
        self.assertEqual(r["X-Board-Version"], v)
        self.assertEqual(r.content, b"")
        writes = [x["sql"] for x in q.captured_queries
                  if x["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))]
        self.assertEqual(writes, [], "the poll must not write (no SQLite write lock every 5 s)")

    def test_stale_version_gets_the_fragment_and_the_new_version(self):
        r = self.get("old")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["X-Board-Version"], board_version(DAY))
        self.assertEqual(r["Cache-Control"], "no-store")
        html = r.content.decode()
        self.assertIn('data-site="S10"', html)
        self.assertEqual(html.count('data-state="open"'), 1)   # one gap, on its site card (round 5)
        self.assertIn("اعرض الفجوات", html)
        self.assertNotIn("<html", html)                    # a fragment, not a page

    def test_version_changes_on_approval_then_on_acceptance(self):
        v0 = board_version(DAY)
        result = self.approve_e066()
        v1 = board_version(DAY)
        self.assertNotEqual(v0, v1)
        r = self.get(v0)
        self.assertEqual(r.status_code, 200)
        self.assertNotIn('data-state="open"', r.content.decode())    # the seat is held for E066
        self.assertIn("بانتظار الرد", r.content.decode())
        respond(offer_token(result.assignment), accept=True)
        v2 = board_version(DAY)
        self.assertNotEqual(v1, v2)
        self.assertNotIn("بانتظار الرد", self.get(v1).content.decode())

    def test_version_changes_on_decline(self):
        result = self.approve_e066()
        v1 = board_version(DAY)
        respond(offer_token(result.assignment), accept=False)
        self.assertNotEqual(v1, board_version(DAY))
        self.assertEqual(self.get(v1).content.decode().count('data-state="open"'), 1)   # the gap is back

    def test_version_changes_on_absence(self):
        v0 = board_version(DAY)
        shift = Shift.objects.get(date=DAY, slot="morning")
        emp = shift.assignments.filter(site__code="S04").first().employee
        Absence.objects.create(employee=emp, kind="sick", starts_at=shift.starts_at, ends_at=shift.ends_at,
                               reported_at=shift.starts_at)
        self.assertNotEqual(v0, board_version(DAY))

    def test_version_ignores_the_snapshots_the_board_writes(self):
        v0 = board_version(DAY)
        self.c.get("/board", {"date": DAY.isoformat()})           # writes CoverageRequirement rows
        self.assertTrue(CoverageRequirement.objects.exists())
        self.assertEqual(v0, board_version(DAY))

    def test_other_days_do_not_change_this_days_version(self):
        v0 = board_version(DAY)
        g1 = Shift.objects.get(date="2030-10-14", slot="morning")
        with mock.patch("django.utils.timezone.now", return_value=g1.starts_at.replace(hour=5)):
            approve(Site.objects.get(code="S03"), g1, "E061", idempotency_key="g1", supervisor=self.sup)
        self.assertEqual(v0, board_version(DAY))


class Permissions(LiveCase):
    def test_anonymous_gets_403_not_a_redirect_to_the_login_page(self):
        r = self.get("x", client=Client())
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r["X-Board-Auth"], "login")
        self.assertNotIn("Location", r)

    def test_non_supervisor_gets_403(self):
        c = Client()
        c.force_login(get_user_model().objects.create_user("viewer"))
        r = self.get("x", client=c)
        self.assertEqual((r.status_code, r["X-Board-Auth"]), (403, "forbidden"))

    def test_get_only_and_a_readable_date(self):
        self.assertEqual(self.c.post(URL + "?date=2030-10-24").status_code, 405)
        self.assertEqual(self.c.get(URL, {"date": "24-10-2030"}).status_code, 400)


class BoardPageWiring(LiveCase):
    def test_board_page_carries_the_live_url_version_and_interval(self):
        html = self.c.get("/board", {"date": DAY.isoformat()}).content.decode()
        self.assertIn(f'data-version="{board_version(DAY)}"', html)
        self.assertIn('data-live="/board/live?date=2030-10-24"', html)
        self.assertIn(f'data-interval="{settings.COVERCAST_BOARD_POLL_MS}"', html)
        self.assertIn("data-live-status", html)

    def test_fragment_and_page_show_the_same_board(self):
        page = self.c.get("/board", {"date": DAY.isoformat()}).content.decode()
        frag = self.get("old").content.decode().split("\n", 1)[1]   # drop the template comment line
        self.assertIn(frag.strip()[:400], page)

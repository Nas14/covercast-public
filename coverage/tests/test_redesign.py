"""The v3 redesign (Shahad's five proposals, 15 Oct 2026): what the new parts SHOW
must come from the same data as before, and nothing new may write.

- board: KPI tiles, day strip, gap cards in priority order, the first-ranked
  person previewed on the gap card (no audit rows), site cards
- candidates: the 80 -> N funnel in rule order, featured and folded candidates
- requests: a read-only list of approved replacements and the answers, with
  the same 204/200/403 poll as the board
"""

import re
from pathlib import Path
from datetime import datetime
from unittest import mock
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import connection
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext

from coverage import importer, policy
from coverage.codes import ExclusionCode
from coverage.models import CandidateEvaluation, CoverageRequirement, Shift
from coverage.services.approval import approve
from coverage.services.offers import offer_token, respond
from workforce.models import Absence

RIYADH = ZoneInfo("Asia/Riyadh")


class RedesignCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        importer.load_folder(settings.COVERCAST_DEMO_DATA_DIR)
        cls.sup = get_user_model().objects.create_user("supervisor", first_name="نوف")
        cls.sup.groups.add(Group.objects.create(name=policy.SUPERVISOR_GROUP))

    def setUp(self):
        self.c = Client()
        self.c.force_login(self.sup)

    def text(self, r) -> str:
        return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", r.content.decode()))

    def g1(self):
        self.c.get("/board?date=2030-10-14")
        return CoverageRequirement.objects.get(site__code="S03", shift__date="2030-10-14", shift__slot="morning")

    def approve_noura(self, key="r1"):
        g1 = self.g1()
        with mock.patch("django.utils.timezone.now", return_value=datetime(2030, 10, 14, 5, tzinfo=RIYADH)):
            return approve(g1.site, g1.shift, "E061", idempotency_key=key, supervisor=self.sup)


class BoardSummary(RedesignCase):
    def test_kpis_come_from_the_seats(self):
        t = self.text(self.c.get("/board?date=2030-10-14"))
        # 12 sites in 2 shifts; G1 is the only open seat of the day
        self.assertIn("المواقع الحرجة 12 في مناوبتين", t)
        self.assertIn("فجوات تحتاج قرارك 1 مؤكدة 0، متوقعة 1", t)
        self.assertIn("بانتظار رد الموظف 0", t)
        m = re.search(r"المقاعد المغطاة (\d+) من (\d+)", t)
        self.assertEqual(int(m.group(2)) - int(m.group(1)), 1, "exactly one seat is open on Monday")

    def test_kpis_follow_an_approval(self):
        self.approve_noura()
        t = self.text(self.c.get("/board?date=2030-10-14"))
        self.assertIn("فجوات تحتاج قرارك 0", t)
        self.assertIn("بانتظار رد الموظف 1 المقعد محجوز حتى يرد", t)

    def test_day_strip_lists_the_shift_days_with_their_calendar(self):
        html = self.c.get("/board?date=2030-10-14").content.decode()
        strip = html[html.index('class="days"'):html.index("</ol>", html.index('class="days"'))]
        for d in ("2030-10-13", "2030-10-14", "2030-10-15"):
            self.assertIn(f'href="?date={d}"', strip)
        self.assertIn('href="?date=2030-10-14" aria-current="date"', strip)
        self.assertIn("اليوم الوطني لليابان", strip)                   # from CalendarEvent, not typed in
        self.assertIn("بلا أحداث في التقويم", strip)                   # Sunday has none

    def test_confirmed_gap_comes_before_anticipated(self):
        shift = Shift.objects.get(date="2030-10-14", slot="morning")
        emp = shift.assignments.filter(site__code="S01").first().employee
        Absence.objects.create(employee=emp, kind="no_show", starts_at=shift.starts_at, ends_at=shift.ends_at,
                               reported_at=shift.starts_at)
        html = self.c.get("/board?date=2030-10-14").content.decode()
        morning = html[html.index('id="panel-morning"'):html.index('id="panel-evening"')]
        # round 5: the gaps lead the site grid, confirmed before anticipated
        order = re.findall(r'class="site open (\w+)"\s+data-site="(S\d+)"', morning)
        self.assertEqual(order, [("confirmed", "S01"), ("anticipated", "S03")])
        first = re.findall(r'data-site="(S\d+)" data-state=', morning)[:2]
        self.assertEqual(first, ["S01", "S03"])
        card = morning[morning.index('data-site="S01"'):morning.index('data-site="S03"')]
        self.assertIn("<b>السبب:</b> غياب مسجّل: " + emp.name, card)    # the reason, from the seats

    def test_site_card_previews_the_same_first_person_without_writing_audit_rows(self):
        before = CandidateEvaluation.objects.count()
        t = self.text(self.c.get("/board?date=2030-10-14"))
        self.assertIn("المرشح الأول: نوره القحطاني 60.83 من 100", t)
        self.assertEqual(CandidateEvaluation.objects.count(), before, "the board preview must not be audited")
        self.c.get(f"/requirements/{self.g1().id}/?slot=1")                 # the candidates page still is
        self.assertGreater(CandidateEvaluation.objects.count(), before)

    def test_site_cards_say_open_waiting_or_covered(self):
        self.approve_noura()
        shift = Shift.objects.get(date="2030-10-15", slot="evening")      # G2 (S10) stays open on Tuesday
        html = self.c.get("/board?date=2030-10-15").content.decode()
        self.assertRegex(html, r'class="site open confirmed"\s+data-site="S10" data-state="open"')
        self.assertRegex(html, r'data-site="S01" data-state="covered"')
        html = self.c.get("/board?date=2030-10-14").content.decode()
        self.assertRegex(html, r'data-site="S03" data-state="waiting"')
        self.assertTrue(shift)

    def test_shift_tab_opens_on_the_shift_with_the_gap(self):
        html = self.c.get("/board?date=2030-10-15").content.decode()  # G2 is in the evening
        self.assertIn('data-tab="evening"\n              aria-selected="true"', html)
        self.assertIn('class="shift active" id="panel-evening"', html)
        html = self.c.get("/board?date=2030-10-14").content.decode()  # G1 is in the morning
        self.assertIn('class="shift active" id="panel-morning"', html)

    def test_live_fragment_carries_the_kpis_and_tabs(self):
        frag = self.c.get("/board/live", {"date": "2030-10-14", "v": "old"}).content.decode()
        self.assertIn('class="kpis"', frag)
        self.assertIn("data-tab=", frag)
        self.assertNotIn('class="days"', frag)                         # the strip is not polled


class CandidatesFunnel(RedesignCase):
    def page(self):
        return self.c.get(f"/requirements/{self.g1().id}/?slot=1")

    def test_funnel_is_every_rule_in_check_order_and_adds_up(self):
        html = self.page().content.decode()
        labels = re.findall(r'<span class="f-label">([^<]+)</span>', html)
        self.assertEqual(labels, [ExclusionCode(c).label for c in ExclusionCode.values])
        removed = [int(n) for n in re.findall(r'aria-label="استُبعد (\d+)"', html)]
        self.assertEqual(80 - sum(removed), 2)                         # Noura and Khaled
        self.assertIn("كيف وصلنا من 80 إلى 2", self.text(self.page()))

    def test_rules_that_removed_nobody_say_so(self):
        t = self.text(self.page())
        self.assertIn("يتجاوز سقف الساعات لم تستبعد أحدًا", t)

    def test_extra_skills_note_when_it_cannot_tell_people_apart(self):
        self.assertIn("«مهارات إضافية» 20 من 20 لكل المرشحين", self.text(self.page()))

    def test_more_than_two_candidates_fold_after_the_second(self):
        importer.load_folder(settings.COVERCAST_DEMO_DATA_DIR, scenario="riyadh_peak")
        self.c.get("/board?date=2030-10-24")
        req = CoverageRequirement.objects.get(site__code="S10", shift__date="2030-10-24")
        html = self.c.get(f"/requirements/{req.id}/?slot=1").content.decode()
        featured, rest = html.split('<details class="more">')
        self.assertEqual(featured.count('<li class="person'), 2)
        self.assertEqual(rest.count('<li class="person'), 9)
        self.assertIn("عرض 9 مرشحين آخرين", rest)
        self.assertEqual(html.count('<dialog class="confirm"'), 11)      # every one can still be approved


class RequestsPage(RedesignCase):
    def test_empty_list_says_what_will_appear(self):
        r = self.c.get("/requests")
        self.assertContains(r, "لا طلبات بعد")
        self.assertContains(r, 'aria-current="page">الطلبات')

    def test_waiting_then_accepted(self):
        result = self.approve_noura()
        t = self.text(self.c.get("/requests"))
        self.assertIn("نوره القحطاني", t)
        self.assertIn("بانتظار الرد", t)
        self.assertIn("منقول من المركز الطبي", t)
        self.assertIn("اعتمده نوف", t)
        respond(offer_token(result.assignment), accept=True)
        t = self.text(self.c.get("/requests"))
        self.assertIn("قبِل التكليف مؤكد", t)

    def test_declined_points_back_to_the_board(self):
        result = self.approve_noura()
        respond(offer_token(result.assignment), accept=False)
        html = self.c.get("/requests").content.decode()
        self.assertIn('data-state="declined"', html)
        self.assertIn('href="/board?date=2030-10-14">افتح لوحة اليوم</a>', html)

    def test_only_supervisors(self):
        self.assertEqual(Client().get("/requests").status_code, 302)
        c = Client()
        c.force_login(get_user_model().objects.create_user("viewer"))
        self.assertEqual(c.get("/requests").status_code, 403)

    def test_live_poll_is_read_only_and_never_redirects(self):
        page = self.c.get("/requests").content.decode()
        v = re.search(r'data-version="([0-9a-f]+)"', page).group(1)
        with CaptureQueriesContext(connection) as q:
            r = self.c.get("/requests/live", {"all": "1", "v": v})
        self.assertEqual(r.status_code, 204)
        self.assertEqual([x for x in q.captured_queries if x["sql"].lstrip().upper().startswith(("INSERT", "UPDATE"))], [])
        self.approve_noura()
        r = self.c.get("/requests/live", {"all": "1", "v": v})
        self.assertEqual(r.status_code, 200)
        self.assertNotEqual(r["X-Board-Version"], v)
        self.assertIn("نوره القحطاني", r.content.decode())
        anon = Client().get("/requests/live", {"all": "1", "v": v})
        self.assertEqual((anon.status_code, anon["X-Board-Auth"]), (403, "login"))
        self.assertNotIn("Location", anon)
        self.assertEqual(self.c.post("/requests/live?all=1").status_code, 405)


class NoFakeActions(RedesignCase):
    """Shahad's proposals that need a backend we do not have must not appear as working buttons."""

    def test_no_button_promises_a_missing_feature(self):
        self.approve_noura()
        pages = [self.c.get(u).content.decode() for u in
                 ("/board?date=2030-10-14", "/board?date=2030-10-15", "/requests")]
        req = CoverageRequirement.objects.get(site__code="S10", shift__date="2030-10-15", shift__slot="evening")
        pages.append(self.c.get(f"/requirements/{req.id}/?slot=1").content.decode())
        for html in pages:
            for fake in ("إعادة إرسال", "إلغاء الاعتماد", "التبديل إلى", "تشغيل التنبؤ", "إضافة عامل",
                         "إضافة موقع", "تصدير", "رسالة نصية", "متبقي"):
                self.assertNotIn(fake, html)

    def test_one_way_into_a_gap_the_button(self):
        # Round 5: the empty seat is a label; «اختر بديلًا» is the only link to the candidates page.
        html = self.c.get("/board?date=2030-10-14").content.decode()
        self.assertNotIn('<a class="seat open"', html)
        self.assertIn('<div class="seat open">', html)
        g1 = self.g1()
        links = re.findall(rf'<a [^>]*href="/requirements/{g1.id}/\?slot=\d+"[^>]*>([^<]*)</a>', html)
        self.assertTrue(links)
        self.assertEqual({s.strip() for s in links}, {"اختر بديلًا"})
        # round 5: nothing on the empty seat invites a click (no «+»); it lists what the seat needs
        cand = self.c.get(f"/requirements/{g1.id}/?slot=1").content.decode()
        for page in (html, cand):
            self.assertNotIn('class="plus-badge"', page)
        self.assertIn('<span class="open-label">مقعد فارغ، يحتاج</span>', html)

    def test_tab_icon_is_the_brand_mark(self):
        svg = (Path(__file__).resolve().parents[1] / "static" / "coverage" / "seat.svg").read_text(encoding="utf-8")
        self.assertIn('fill="#0B6B57"', svg)          # --brand
        self.assertNotIn("#B3322A", svg)              # the old red dashed seat


class OfferLinkAgain(RedesignCase):
    """The supervisor can copy the employee's link again after leaving the page, but
    showing it again NEVER extends it: it carries the approval time, so it expires
    OFFER_TOKEN_MAX_AGE_HOURS after the approval, exactly like the first one."""

    def link_on(self, url):
        html = self.c.get(url).content.decode()
        m = re.search(r'data-copy="(http[^"]+/offer/[^"]+)"', html)
        return m.group(1) if m else None

    def test_requests_page_shows_a_working_link_while_waiting(self):
        self.approve_noura()
        url = self.link_on("/requests")
        self.assertIsNotNone(url)
        r = Client().get(url.split("testserver", 1)[1])                    # the employee, no account
        self.assertContains(r, "مرحبًا نوره")
        self.assertContains(r, "أقبل التكليف")
        self.assertIsNone(self.link_on("/board?date=2030-10-14"), "Nouf: the link belongs to the Requests page")

    def test_the_link_keeps_the_original_expiry(self):
        from datetime import timedelta
        from django.core import signing
        from coverage.services.errors import CoverCastError
        from coverage.services.offers import offer_expires_at, read_token

        from django.utils import timezone
        from coverage.models import Assignment
        a = self.approve_noura().assignment
        # approved 23 hours ago: a link re-made NOW would live 24 more hours; the right one, 1 hour
        Assignment.objects.filter(pk=a.pk).update(approved_at=timezone.now() - timedelta(hours=23))
        a.refresh_from_db()
        token = self.link_on("/requests").rsplit("/offer/", 1)[1]
        self.assertEqual(read_token(token), a.pk)
        expires = offer_expires_at(a)
        self.assertEqual(expires, a.approved_at + timedelta(hours=policy.OFFER_TOKEN_MAX_AGE_HOURS))
        with mock.patch.object(signing.time, "time", return_value=(expires - timedelta(minutes=1)).timestamp()):
            self.assertEqual(read_token(token), a.pk)                              # still valid just before
        with mock.patch.object(signing.time, "time", return_value=(expires + timedelta(minutes=1)).timestamp()):
            with self.assertRaises(CoverCastError):                                # and not one minute after
                read_token(token)

    def test_no_link_once_answered_or_expired(self):
        result = self.approve_noura()
        a = result.assignment
        from datetime import timedelta
        from django.utils import timezone
        from coverage.models import Assignment
        approved = a.approved_at
        Assignment.objects.filter(pk=a.pk).update(approved_at=timezone.now() - timedelta(hours=25))
        self.assertIn("انتهت صلاحية الرابط", self.c.get("/requests").content.decode())
        self.assertIsNone(self.link_on("/requests"))
        Assignment.objects.filter(pk=a.pk).update(approved_at=approved)
        self.assertIsNotNone(self.link_on("/requests"))
        respond(offer_token(a), accept=True)
        self.assertIsNone(self.link_on("/requests"))
        self.assertIsNone(self.link_on("/board?date=2030-10-14"))

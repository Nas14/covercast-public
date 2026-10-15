"""Phase 8b: the Arabic screens. They are built from the API dicts, so these
tests check what a person SEES: the right words, numbers and actions."""

import io
import os
import re
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client, SimpleTestCase, TestCase

from coverage import importer, policy
from coverage.models import CoverageRequirement, Shift, Site
from coverage.views import counted, gaps_phrase, minutes_phrase, people_phrase

APP = Path(__file__).resolve().parents[1]


class PageCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        importer.load_folder(settings.COVERCAST_DEMO_DATA_DIR)
        cls.sup = get_user_model().objects.create_user("supervisor", first_name="نوف")
        cls.sup.groups.add(Group.objects.create(name=policy.SUPERVISOR_GROUP))

    def setUp(self):
        self.c = Client()
        self.c.force_login(self.sup)

    def text(self, r) -> str:
        """Visible text, whitespace collapsed (tags removed)."""
        return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", r.content.decode()))

    def g1(self):
        self.c.get("/board?date=2030-10-14")
        return CoverageRequirement.objects.get(site__code="S03", shift__date="2030-10-14", shift__slot="morning")


class AccessTests(PageCase):
    def test_anonymous_goes_to_login_and_login_works(self):
        anon = Client()
        r = anon.get("/board?date=2030-10-14")
        self.assertRedirects(r, "/login/?next=/board%3Fdate%3D2030-10-14", fetch_redirect_response=False)
        page = anon.get("/login/")
        self.assertContains(page, 'dir="rtl"')
        self.assertContains(page, "دخول المشرف")
        get_user_model().objects.create_user("x", password="a-Strong-pass-42").groups.add(self.sup.groups.first())
        r = anon.post("/login/", {"username": "x", "password": "a-Strong-pass-42"})
        self.assertRedirects(r, "/", fetch_redirect_response=False)
        bad = anon.post("/login/", {"username": "x", "password": "wrong"})
        self.assertContains(bad, "اسم المستخدم أو كلمة المرور غير صحيحة")

    def test_logged_in_but_not_a_supervisor(self):
        c = Client()
        c.force_login(get_user_model().objects.create_user("viewer"))
        r = c.get("/board?date=2030-10-14")
        self.assertEqual(r.status_code, 403)
        self.assertContains(r, "هذه الصفحة للمشرفين فقط", status_code=403)

    def test_home_opens_the_nearest_day_with_shifts(self):
        self.assertRedirects(self.c.get("/"), "/board?date=2030-10-13", fetch_redirect_response=False)


class SimulatedDataIsAlwaysDisclosed(PageCase):
    """NFR-1: no screen of the demo can pass for real data."""

    def test_every_kind_of_page_says_simulated(self):
        self.assertContains(self.c.get("/board?date=2030-10-14"), "بيانات محاكاة")
        self.assertContains(self.c.get(f"/requirements/{self.g1().id}/"), "بيانات محاكاة")
        self.assertContains(Client().get("/login/"), "محاكاة لأغراض العرض")

    def test_disappears_only_when_no_simulated_person_is_left(self):
        from workforce.models import Employee
        Employee.objects.update(data_origin="official")
        self.assertNotContains(self.c.get("/board?date=2030-10-14"), "بيانات محاكاة")


class BoardPageTests(PageCase):
    def test_monday_puts_g1_first_with_the_shape_of_the_missing_person(self):
        r = self.c.get("/board?date=2030-10-14")
        t = self.text(r)
        self.assertIn("يحتاج قرارك: فجوة واحدة", t)
        decide = r.content.decode().split('class="shift"')[0]          # the top section only
        self.assertIn('data-site="S03"', decide)
        self.assertIn("إسعاف أولي متقدم + اليابانية + مسعف", decide)
        self.assertIn("فجوة متوقعة", decide)
        self.assertIn("المطلوب 4، الحاضر 5", t)                       # S12 has one more than its minimum
        self.assertIn("كل المواقع مغطاة", t)                          # the evening shift

    def test_tuesday_shows_e019_struck_through_and_the_red_gap(self):
        r = self.c.get("/board?date=2030-10-15")
        self.assertContains(r, "غياب مسجّل، لا يُحسب")
        self.assertContains(r, "فجوة مؤكدة")
        self.assertContains(r, "إدارة الحشود")

    def test_quiet_day_says_so(self):
        t = self.text(self.c.get("/board?date=2030-10-13"))
        self.assertIn("لا شيء ينتظر قرارك", t)

    def test_absence_buttons_point_at_the_api(self):
        r = self.c.get("/board?date=2030-10-14")
        self.assertRegex(r.content.decode(), r'data-absence="/api/assignments/\d+/absence" data-kind="no_show"')
        self.assertIn("csrftoken", r.cookies)                         # needed by the buttons

    def test_bad_date_goes_home(self):
        self.assertRedirects(self.c.get("/board?date=yesterday"), "/", fetch_redirect_response=False)


class CandidatesPageTests(PageCase):
    def test_g1_page_reads_like_the_hand_derivation(self):
        r = self.c.get(f"/requirements/{self.g1().id}/?slot=1")
        t = self.text(r)
        self.assertIn("المقعد الفارغ يحتاج", t)
        self.assertIn("بسبب اليوم الوطني لليابان - حفل الاحتفال", t)
        self.assertIn("فحصنا 80 موظفًا بثماني قواعد. 2 فقط يجتازونها كلها", t)
        self.assertIn("60.83", t)
        self.assertIn("45.00", t)                                      # two decimals, always
        self.assertIn("نقله لا يكسر الحد الأدنى في موقعه الحالي (المركز الطبي)", t)
        self.assertContains(r, "كان الاختيار سيقع على <b>احمد الدوسري</b>، على بعد 10 دقائق.")
        self.assertIn("لكن الموقع خارج نطاق الإعارة، ونقله يُنقص البوابة الشمالية شخصًا واحدًا.", t)
        self.assertIn("74 لا يحمل المهارة المطلوبة", t)
        self.assertContains(r, 'data-code="E061"')
        self.assertContains(r, 'style="width:22.5%;background:var(--p-proximity)"')   # not "22,5"

    def test_covered_site_explains_instead_of_crashing(self):
        self.c.get("/board?date=2030-10-14")
        s01 = CoverageRequirement.objects.get(site__code="S01", shift__date="2030-10-14", shift__slot="morning")
        r = self.c.get(f"/requirements/{s01.id}/")
        self.assertEqual(r.status_code, 409)
        self.assertContains(r, "هذه الخانة لم تعد مفتوحة", status_code=409)
        self.assertEqual(self.c.get("/requirements/999999/").status_code, 404)


class OfflineTests(SimpleTestCase):
    """The demo must run with no internet: no CDN, no web font service."""

    def test_no_external_address_in_templates_css_or_js(self):
        files = [*APP.glob("templates/**/*.html"), *APP.glob("static/**/*.css"), *APP.glob("static/**/*.js")]
        self.assertGreater(len(files), 6)
        for f in files:
            with self.subTest(file=f.name):
                self.assertNotRegex(f.read_text(encoding="utf-8"), r"(https?:)?//[a-z0-9.-]+\.[a-z]{2,}/")

    def test_font_files_and_licence_are_in_the_repo(self):
        fonts = APP / "static/coverage/fonts"
        self.assertEqual(len(list(fonts.glob("*.woff2"))), 6)
        self.assertIn("SIL Open Font License", (fonts / "OFL-LICENSE.txt").read_text(encoding="utf-8"))


class ArabicNumberTests(SimpleTestCase):
    def test_reason_texts_agree_with_the_number(self):
        from coverage.services.reasons import render
        self.assertEqual([render("NEAR", {"minutes": n}) for n in (1, 2, 10, 25)],
                         ["الأقرب: على بعد دقيقة واحدة من الموقع", "الأقرب: على بعد دقيقتين من الموقع",
                          "الأقرب: على بعد 10 دقائق من الموقع", "الأقرب: على بعد 25 دقيقة من الموقع"])
        self.assertEqual(render("REST_GAP", {"rest": 11}), "فاصل الراحة أقل من 11 ساعة")
        self.assertEqual(render("REST_GAP", {"rest": 8}), "فاصل الراحة أقل من 8 ساعات")
        self.assertEqual(render("NEAR", {"minutes": 10}, lang="en"), "Only 10 min away")

    def test_agreement(self):
        self.assertEqual([gaps_phrase(n) for n in (1, 2, 3, 10, 11, 103)],
                         ["فجوة واحدة", "فجوتان", "3 فجوات", "10 فجوات", "11 فجوة", "103 فجوات"])
        self.assertEqual([minutes_phrase(n) for n in (1, 2, 5, 25, 60)],
                         ["دقيقة واحدة", "دقيقتين", "5 دقائق", "25 دقيقة", "60 دقيقة"])
        self.assertEqual(people_phrase(1), "شخصًا واحدًا")
        self.assertEqual(counted(0, "a", "b", "c", "d"), "0 d")


class CreateSupervisorTests(TestCase):
    def test_creates_a_supervisor_without_admin_access(self):
        with mock.patch.dict(os.environ, {"COVERCAST_SUPERVISOR_PASSWORD": "Expo-2030-cover"}):
            call_command("create_supervisor", "nouf", "--name", "نوف", stdout=io.StringIO())
        u = get_user_model().objects.get(username="nouf")
        self.assertTrue(u.check_password("Expo-2030-cover"))
        self.assertEqual((u.first_name, u.is_staff, u.is_superuser), ("نوف", False, False))
        self.assertTrue(u.groups.filter(name=policy.SUPERVISOR_GROUP).exists())

    def test_weak_password_is_refused(self):
        with mock.patch.dict(os.environ, {"COVERCAST_SUPERVISOR_PASSWORD": "123"}):
            with self.assertRaises(CommandError):
                call_command("create_supervisor", "weak", stdout=io.StringIO())
        self.assertFalse(get_user_model().objects.filter(username="weak").exists())


class ForecastPanelPages(TestCase):
    """P0-6: the panel says the level, where it came from, and what it adds, in words."""

    @classmethod
    def setUpTestData(cls):
        from coverage.forecast_import import import_path
        importer.load_folder(settings.COVERCAST_DEMO_DATA_DIR, scenario="riyadh_peak")
        import_path(settings.BASE_DIR / "forecast" / "out" / "export" / "osaka_replay.json")
        cls.sup = get_user_model().objects.create_user("supervisor", first_name="نوف")
        cls.sup.groups.add(Group.objects.create(name=policy.SUPERVISOR_GROUP))

    def setUp(self):
        self.c = Client()
        self.c.force_login(self.sup)

    def text(self, r) -> str:
        return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", r.content.decode()))

    def test_scenario_day_panel_and_gap_reason(self):
        t = self.text(self.c.get("/board?date=2030-10-24"))
        self.assertIn("ضغط الزوار: ذروة", t)
        self.assertIn("سيناريو محاكى، ليس تنبؤًا", t)
        self.assertIn("أثره على الاحتياج: +13 في 10 مواقع", t)
        self.assertIn("البوابات والأمن", t)
        self.assertIn("ليست معيار توظيف رسميًا", t)
        self.assertIn("المطلوب = 3 حد أدنى + 1 بسبب ضغط الزوار: ذروة (افتراض سيناريو محاكى، ليس تنبؤًا)", t)
        self.assertIn("إعادة عرض أوساكا 2025", t)

    def test_osaka_day_is_official_and_not_riyadh(self):
        t = self.text(self.c.get("/board?date=2025-09-13"))
        self.assertIn("بيانات رسمية · أوساكا 2025", t)
        self.assertIn("ليس توقعًا للرياض 2030", t)
        self.assertIn("209,837", t)
        self.assertIn("218,130", t)
        self.assertIn("للتوضيح فقط", t)
        self.assertIn("لا مناوبات في هذا اليوم", t)
        self.assertIn("أرقام الزوار في هذه الصفحة رسمية", t)
        self.assertNotIn("كل الأسماء والأرقام في هذه الصفحة محاكاة", t)

    def test_g1_day_has_no_panel(self):
        t = self.text(self.c.get("/board?date=2030-10-14"))
        self.assertNotIn("ضغط الزوار", t)
        self.assertIn("يحتاج قرارك: فجوة واحدة", t)

    def test_candidates_page_names_the_level_as_the_reason(self):
        self.c.get("/board?date=2030-10-24")
        req = CoverageRequirement.objects.get(site__code="S10", shift__date="2030-10-24")
        t = self.text(self.c.get(f"/requirements/{req.id}/?slot=1"))
        self.assertIn("بسبب ضغط الزوار: ذروة (افتراض سيناريو محاكى، ليس تنبؤًا)", t)
        self.assertIn("80.83", t)


class ShahadDesignPages(PageCase):
    """Shahad's design (Expo2030.pdf): the approval dialog, the score breakdown, the waiting state."""

    def candidates(self):
        return self.c.get(f"/requirements/{self.g1().id}/?slot=1").content.decode()

    def test_every_candidate_has_an_approval_dialog_with_from_and_to(self):
        html = self.candidates()
        self.assertEqual(html.count('<dialog class="confirm"'), 2)               # Noura and Khaled
        noura = html[html.index('id="confirm-E061"'):html.index("</dialog>", html.index('id="confirm-E061"'))]
        self.assertIn("اعتماد نوره القحطاني؟", noura)
        self.assertIn("<small>من</small><b>المركز الطبي</b>", noura)
        self.assertIn("(S03)", noura)
        self.assertIn("تأكيد الاعتماد", noura)
        self.assertNotIn('class="warn"', noura)

    def test_near_the_hours_cap_the_dialog_warns_and_says_so_on_the_button(self):
        html = self.candidates()
        khaled = html[html.index('id="confirm-E012"'):html.index("</dialog>", html.index('id="confirm-E012"'))]
        self.assertIn("يقترب من سقف الساعات", khaled)
        self.assertIn("تصير ساعاته 48 من 48", khaled)
        self.assertIn("اعتماد رغم التنبيه", khaled)
        self.assertIn("خارج المناوبة (استدعاء)", khaled)

    def test_score_breakdown_adds_up_to_the_score(self):
        html = self.candidates()
        self.assertIn("تفصيل الدرجة 60.83", html)
        for part in ("22.50 / 30", "8.33 / 25", "20.00 / 20", "0.00 / 15", "10.00 / 10"):
            self.assertIn(part, html)

    def test_board_shows_the_waiting_state_after_approval(self):
        from datetime import datetime
        from unittest import mock
        from zoneinfo import ZoneInfo

        from coverage.services.approval import approve
        g1 = self.g1()
        with mock.patch("django.utils.timezone.now",
                        return_value=datetime(2030, 10, 14, 5, tzinfo=ZoneInfo("Asia/Riyadh"))):
            approve(g1.site, g1.shift, "E061", idempotency_key="w", supervisor=self.sup)
        t = self.text(self.c.get("/board?date=2030-10-14"))
        self.assertIn("بانتظار ردّ واحد", t)
        self.assertIn("المقعد محجوز لـنوره القحطاني حتى يقبل أو يعتذر", t)
        self.assertNotIn("يحتاج قرارك", t)

    def test_brand_and_actions_use_the_design_green(self):
        css = (APP / "static" / "coverage" / "covercast.css").read_text(encoding="utf-8")
        self.assertIn("--brand: #0B6B57;", css)
        self.assertIn("--waiting: #3B6FD8;", css)


class ThemeTests(PageCase):
    def test_light_is_the_default_and_the_system_theme_is_ignored(self):
        html = self.c.get("/board?date=2030-10-14").content.decode()
        self.assertIn('<html lang="ar" dir="rtl" data-theme="light">', html)
        self.assertIn("data-theme-toggle", html)
        css = (APP / "static" / "coverage" / "covercast.css").read_text(encoding="utf-8")
        self.assertNotIn("prefers-color-scheme", css)
        self.assertIn(':root[data-theme="dark"]', css)

    def test_employee_and_login_pages_are_light_too(self):
        anon = Client()
        self.assertIn('data-theme="light"', anon.get("/login/").content.decode())

    def test_no_template_comment_leaks_onto_the_page(self):
        # A Django {# #} comment must fit on one line; a two-line one is printed as text.
        for html in (self.c.get("/board?date=2030-10-14").content.decode(),
                     Client().get("/login/").content.decode()):
            self.assertNotIn("{#", html)
            self.assertNotIn("#}", html)

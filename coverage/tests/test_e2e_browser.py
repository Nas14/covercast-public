"""Phase 9: the demo story in a real browser, against a live server.

docs/14-demo-scenario.md, step by step, as the supervisor and the employee
would click it. Runs inside `manage.py test` when Playwright and a Chromium
build are available; otherwise it is skipped with the reason printed, so the
normal suite never needs a browser or the internet.

    pip install -r requirements-dev.txt
    python -m playwright install chromium      # once; downloads the browser

What only a browser can prove: the JavaScript sends ONE idempotency key per
approve button, the confirm dialog and fetch() with CSRF work, the local font
loads, and the page makes no request outside the server.
"""

import os
import re
import secrets

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.staticfiles.testing import StaticLiveServerTestCase

from coverage import importer, policy
from coverage.models import Assignment

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # optional dependency (requirements-dev.txt)
    sync_playwright = None


class DemoStoryInABrowser(StaticLiveServerTestCase):
    @classmethod
    def setUpClass(cls):
        if sync_playwright is None:
            cls.skip_reason = "Playwright not installed (pip install -r requirements-dev.txt)"
        else:
            # Playwright's sync API runs an event loop in this thread; Django then
            # refuses ORM calls from "async" code. Safe here: one thread, tests only.
            os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"
            try:
                cls.pw = sync_playwright().start()
                cls.browser = cls.pw.chromium.launch()
                cls.skip_reason = None
            except Exception as exc:  # noqa: BLE001 - no browser build on this machine
                cls.skip_reason = f"No Chromium for Playwright ({exc.__class__.__name__}); run: python -m playwright install chromium"
                cls.pw.stop()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        if getattr(cls, "skip_reason", "x") is None:
            cls.browser.close()
            cls.pw.stop()
            os.environ.pop("DJANGO_ALLOW_ASYNC_UNSAFE", None)

    def setUp(self):
        if self.skip_reason:
            self.skipTest(self.skip_reason)
        importer.load_folder(settings.COVERCAST_DEMO_DATA_DIR)
        self.password = secrets.token_urlsafe(16)
        u = get_user_model().objects.create_user("nouf", password=self.password, first_name="نوف")
        u.groups.add(Group.objects.get_or_create(name=policy.SUPERVISOR_GROUP)[0])

    def page(self, **kw):
        ctx = self.browser.new_context(locale="ar-SA", **kw)
        self.addCleanup(ctx.close)
        p = ctx.new_page()
        p.on("dialog", lambda d: d.accept())
        p.external = []
        p.on("request", lambda r: p.external.append(r.url) if not r.url.startswith(self.live_server_url) else None)
        return p

    def sign_in(self, p):
        p.goto(self.live_server_url + "/")
        p.fill("#id_username", "nouf")
        p.fill("#id_password", self.password)
        p.click("button.btn")
        p.wait_for_load_state("networkidle")

    def test_the_whole_demo(self):
        p = self.page(viewport={"width": 1280, "height": 900})
        self.sign_in(p)

        # 1. Monday: G1 is the only thing that needs a decision
        p.goto(self.live_server_url + "/board?date=2030-10-14")
        self.assertEqual(p.inner_text(".decide h2"), "يحتاج قرارك: فجوة واحدة")
        self.assertIn("إسعاف أولي متقدم + اليابانية + مسعف", p.inner_text(".gap-card .seat.open"))
        self.assertTrue(p.evaluate("document.fonts.check('16px \"Plex Arabic\"')"))

        # 2. Candidates: Noura first, the comparison line, the 78 excluded
        p.click(".gap-card a.btn")
        p.wait_for_load_state("networkidle")
        self.assertEqual(p.inner_text("li.person.top .score b"), "60.83")
        self.assertIn("ونقله يُنقص البوابة الشمالية شخصًا واحدًا", p.inner_text(".versus"))

        # 3. Approve, then press again with the same key: one assignment only
        approve = "li.person.top button[data-approve]"
        keys = []
        p.on("request", lambda r: keys.append(re.search(r'"idempotency_key":\s*"([^"]+)"', r.post_data or "").group(1))
             if r.method == "POST" and "/approve" in r.url else None)
        p.click(approve)
        # the approval dialog (Shahad's design): who, from where, to where, what it changes
        dlg = p.locator("dialog[open]")
        self.assertIn("اعتماد نوره القحطاني؟", dlg.inner_text())
        self.assertIn("المركز الطبي", dlg.locator(".move-box").inner_text())
        self.assertIn("نقطة الإسعاف 3", dlg.locator(".move-box").inner_text())
        self.assertEqual(len(keys), 0, "nothing is sent before the supervisor confirms")
        dlg.locator("[data-cancel]").click()                    # back out: nothing happens
        self.assertEqual(p.locator("dialog[open]").count(), 0)
        self.assertEqual(len(keys), 0)
        p.click(approve)
        p.click("dialog[open] [data-confirm-approve]")
        p.wait_for_selector("li.person.top .result:not([hidden])")
        self.assertIn("تم اعتماد نوره", p.inner_text("li.person.top .result"))
        p.evaluate(f"document.querySelector('{approve}').disabled = false")
        p.click(approve)
        p.click("dialog[open] [data-confirm-approve]")
        p.wait_for_function("document.querySelector('li.person.top .result').innerText.includes('مسجّل من قبل')")
        self.assertEqual(len(keys), 2)
        self.assertEqual(len(set(keys)), 1)
        self.assertEqual(Assignment.objects.filter(origin="replacement").count(), 1)
        offer_url = p.input_value("li.person.top .offer-link input")

        # 4. The employee, on a phone, with no account
        phone = self.page(viewport={"width": 390, "height": 760})
        phone.goto(offer_url)
        self.assertIn("مرحبًا نوره", phone.inner_text("h1"))
        phone.click("button[value=accept]")
        phone.wait_for_selector("[data-result=confirmed]")
        self.assertEqual(Assignment.objects.get(origin="replacement").status, "confirmed")

        # 5. Back on the board: G1 closed; a live absence opens a red gap at S01
        p.goto(self.live_server_url + "/board?date=2030-10-14")
        self.assertEqual(p.inner_text(".decide h2"), "لا شيء ينتظر قرارك")
        seat = p.locator(".shift .site[data-site='S01'] .seat details").first
        seat.locator("summary").click()
        with p.expect_navigation():
            seat.locator("button[data-kind='no_show']").click()
        self.assertIn("فجوة مؤكدة", p.inner_text(".gap-card"))
        self.assertIn("مسعف", p.inner_text(".gap-card .seat.open"))

        # 6. Tuesday on a phone: G2 is the first thing on the screen
        mobile = self.page(viewport={"width": 390, "height": 844}, storage_state=p.context.storage_state())
        mobile.goto(self.live_server_url + "/board?date=2030-10-15")
        box = mobile.locator(".gap-card").bounding_box()
        self.assertLess(box["y"], 844, "G2 must be visible without scrolling on a phone")
        self.assertIn("إدارة الحشود", mobile.inner_text(".gap-card"))
        mobile.click(".gap-card a.btn")
        mobile.wait_for_load_state("networkidle")
        self.assertIn("غادة السبيعي", mobile.inner_text("li.person.top h3"))

        # Offline: nothing was requested from outside the server, by any page
        self.assertEqual(p.external + phone.external + mobile.external, [])

    def test_forecast_to_coverage_journey(self):
        """P0-7: visitor-pressure level -> requirement -> gap -> ranked replacements ->
        approval -> employee answer -> covered (simulated Riyadh scenario), and the
        Osaka 2025 replay shown as official data, never as Riyadh."""
        importer.load_folder(settings.COVERCAST_DEMO_DATA_DIR, scenario="riyadh_peak")
        from coverage.forecast_import import import_path
        import_path(settings.BASE_DIR / "forecast" / "out" / "export" / "osaka_replay.json")
        p = self.page(viewport={"width": 1280, "height": 900})
        self.sign_in(p)

        # 1. Level and its provenance, in words
        p.goto(self.live_server_url + "/board?date=2030-10-24")
        panel = p.inner_text(".pressure")
        self.assertIn("ضغط الزوار: ذروة", panel)
        self.assertIn("سيناريو محاكى، ليس تنبؤًا", panel)
        self.assertIn("+13", panel)
        self.assertNotIn("زائر", panel)                      # no visitor number for a scenario

        # 2-3. The requirement and the one gap it creates, with its reason
        self.assertEqual(p.inner_text(".decide h2"), "يحتاج قرارك: فجوة واحدة")
        card = p.inner_text(".gap-card")
        self.assertIn("محطة النقل والمكوكات", card)
        self.assertIn("المطلوب = 3 حد أدنى + 1", card)

        # 4. Ranked, explained replacements
        p.click(".gap-card a.btn")
        p.wait_for_load_state("networkidle")
        self.assertEqual(p.inner_text("li.person.top .score b"), "80.83")
        self.assertIn("ضغط الزوار: ذروة", p.inner_text("main"))

        # 5. Supervisor approval (confirmed in the dialog)
        self.approve_top(p)
        offer_url = p.input_value("li.person.top .offer-link input")

        # 6. Employee response, on a phone
        phone = self.page(viewport={"width": 390, "height": 760})
        phone.goto(offer_url)
        phone.click("button[value=accept]")
        phone.wait_for_selector("[data-result=confirmed]")

        # 7. Updated coverage status
        p.goto(self.live_server_url + "/board?date=2030-10-24")
        self.assertEqual(p.inner_text(".decide h2"), "لا شيء ينتظر قرارك")
        self.assertEqual(Assignment.objects.get(origin="replacement").employee.code, "E066")

        # Osaka replay: official, compared with the actual, and changes no shift
        p.click("text=إعادة عرض أوساكا 2025")
        p.wait_for_load_state("networkidle")
        panel = p.inner_text(".pressure")
        self.assertIn("بيانات رسمية · أوساكا 2025", panel)
        self.assertIn("ليس توقعًا للرياض 2030", panel)
        self.assertIn("الفعلي", panel)
        self.assertEqual(p.inner_text(".decide h2"), "لا مناوبات في هذا اليوم")
        self.assertIn("رسمية", p.inner_text("footer.sim-note"))
        self.assertEqual(p.external + phone.external, [])

    # ------------------------------------------------------------------ live board
    def approve_top(self, p):
        """Click the top candidate's approve button, and confirm in the in-page dialog if there is one."""
        p.click("li.person.top button[data-approve]")
        p.click("dialog[open] [data-confirm-approve]")         # Shahad's approval dialog
        p.wait_for_selector("li.person.top .result:not([hidden])")

    def live_requests(self, p):
        seen = []
        p.on("request", lambda r: seen.append(r.url) if "/board/live" in r.url else None)
        return seen

    def test_board_updates_itself_after_the_employee_answers(self):
        """Two sessions: the supervisor watches the board; the employee answers on a phone.
        The board follows on its own (5-second poll), with no reload and no scroll jump."""
        from datetime import date as d

        from workforce.models import Absence
        from coverage.models import Shift

        importer.load_folder(settings.COVERCAST_DEMO_DATA_DIR, scenario="riyadh_peak")
        board_url = self.live_server_url + "/board?date=2030-10-24"
        sup = self.page(viewport={"width": 1280, "height": 900})
        self.sign_in(sup)
        sup.goto(board_url)
        self.assertEqual(sup.inner_text(".decide h2"), "يحتاج قرارك: فجوة واحدة")
        sup.evaluate("window.__sameDocument = 'yes'")          # gone if the page ever reloads
        polls = self.live_requests(sup)

        # Scroll so a row low on the page sits at a known place on screen.
        row = "#board-live .site[data-site='S11']"
        sup.locator(row).scroll_into_view_if_needed()
        sup.evaluate(f"window.scrollBy(0, document.querySelector(\"{row}\").getBoundingClientRect().top - 400)")
        top_before = sup.evaluate(f"document.querySelector(\"{row}\").getBoundingClientRect().top")

        # 1. Another tab of the same supervisor approves E066.
        tab = sup.context.new_page()
        tab.on("dialog", lambda dlg: dlg.accept())
        tab.goto(board_url)
        tab.click(".gap-card a.btn")
        tab.wait_for_load_state("networkidle")
        self.approve_top(tab)
        offer_url = tab.input_value("li.person.top .offer-link input")
        tab.close()

        # The watched board drops the gap by itself: the seat is held for E066.
        sup.wait_for_function("!document.querySelector('#board-live .gap-card')", timeout=12000)
        self.assertIn("بانتظار الرد", sup.inner_text("#board-live .site[data-site='S10']"))
        self.assertEqual(sup.inner_text(".decide h2"), "بانتظار ردّ واحد")       # the design's blue state
        self.assertIn("ليلى اليامي", sup.inner_text("#board-live .wait-card[data-site='S10']"))

        # 2. The employee, in a separate browser session with no account, accepts.
        phone = self.page(viewport={"width": 390, "height": 760})
        phone.goto(offer_url)
        phone.click("button[value=accept]")
        phone.wait_for_selector("[data-result=confirmed]")

        # 3. The board follows: no "awaiting", same document, same scroll place.
        sup.wait_for_function(
            "!document.querySelector(\"#board-live .site[data-site='S10']\").innerText.includes('بانتظار الرد')",
            timeout=12000)
        self.assertEqual(sup.inner_text(".decide h2"), "لا شيء ينتظر قرارك")
        self.assertIn("ليلى اليامي", sup.inner_text("#board-live .site[data-site='S10']"))
        self.assertEqual(sup.evaluate("window.__sameDocument"), "yes")
        top_after = sup.evaluate(f"document.querySelector(\"{row}\").getBoundingClientRect().top")
        self.assertLess(abs(top_after - top_before), 3, "the row the supervisor was reading must not jump")
        self.assertIn("تحدّثت اللوحة", sup.inner_text("[data-live-status]"))
        self.assertGreater(len(polls), 1)

        # 4. Hidden tab: no polling at all; visible again: one check at once.
        sup.evaluate("""Object.defineProperty(document, 'hidden', {configurable: true, get: () => true});
                        document.dispatchEvent(new Event('visibilitychange'))""")
        n = len(polls)
        sup.wait_for_timeout(7000)
        self.assertEqual(len(polls), n, "a hidden tab must not poll")
        sup.evaluate("""Object.defineProperty(document, 'hidden', {configurable: true, get: () => false});
                        document.dispatchEvent(new Event('visibilitychange'))""")
        sup.wait_for_timeout(500)
        self.assertEqual(len(polls), n + 1, "back on the tab: one check at once")

        # 5. An open absence menu is never swapped away; the change lands after it closes.
        menu = "#board-live .site[data-site='S04'] .seat details"
        sup.locator(menu).first.locator("summary").click()
        shift = Shift.objects.get(date=d(2030, 10, 24), slot="morning")
        emp = shift.assignments.get(site__code="S05", employee__code="E080").employee
        Absence.objects.create(employee=emp, kind="sick", starts_at=shift.starts_at, ends_at=shift.ends_at,
                               reported_at=shift.starts_at)
        sup.wait_for_timeout(7000)
        self.assertTrue(sup.evaluate(f"document.querySelector(\"{menu}\").open"), "the open menu must stay open")
        self.assertEqual(sup.locator("#board-live .gap-card[data-site='S05']").count(), 0)
        sup.locator(menu).first.locator("summary").click()      # close it
        # S05 needs 5 at peak and now has 4, still above its base of 3: an anticipated gap
        sup.wait_for_function("document.querySelector(\"#board-live .gap-card[data-site='S05']\")", timeout=12000)
        self.assertEqual(sup.evaluate("window.__sameDocument"), "yes")

        # 6. Network down: a calm message and retries; back up: it recovers on its own.
        sup.route("**/board/live*", lambda route: route.abort())
        sup.wait_for_function("document.querySelector('[data-live-status]').dataset.state === 'error'", timeout=12000)
        self.assertIn("تعذّر التحديث", sup.inner_text("[data-live-status]"))
        sup.unroute("**/board/live*")
        sup.wait_for_function("document.querySelector('[data-live-status]').dataset.state !== 'error'", timeout=25000)

        # 7. Session over: polling stops and says how to sign in again, never shows the login form inside the board.
        sup.context.clear_cookies()
        sup.wait_for_function("document.querySelector('[data-live-status]').dataset.state === 'stopped'", timeout=35000)
        self.assertIn("انتهت الجلسة", sup.inner_text("[data-live-status]"))
        self.assertNotIn("دخول المشرف", sup.inner_text("#board-live"))
        self.assertEqual(sup.external + phone.external, [])

    # ------------------------------------------------------------------ theme
    def test_light_by_default_even_on_a_dark_system_and_the_toggle_remembers(self):
        """A dark operating-system theme must not darken the demo; only the toggle does."""
        p = self.page(viewport={"width": 1280, "height": 900}, color_scheme="dark")
        self.sign_in(p)
        p.goto(self.live_server_url + "/board?date=2030-10-14")
        bg = "getComputedStyle(document.body).backgroundColor"
        self.assertEqual(p.evaluate(bg), "rgb(245, 246, 248)")              # Shahad's light page
        toggle = p.locator("[data-theme-toggle]")
        self.assertEqual(toggle.inner_text(), "الوضع الداكن")
        toggle.click()
        self.assertEqual(p.evaluate("document.documentElement.dataset.theme"), "dark")
        self.assertEqual(p.evaluate(bg), "rgb(15, 20, 18)")
        self.assertEqual(toggle.get_attribute("aria-pressed"), "true")
        p.reload()
        self.assertEqual(p.evaluate(bg), "rgb(15, 20, 18)", "the choice is remembered in this browser")
        p.locator("[data-theme-toggle]").click()
        self.assertEqual(p.evaluate(bg), "rgb(245, 246, 248)")
        p.reload()
        self.assertEqual(p.evaluate(bg), "rgb(245, 246, 248)")
        # the live board keeps working after a theme change (the toggle is outside the polled part)
        self.assertEqual(p.locator("#board-live[data-live]").count(), 1)

"""Phase 9: access control over EVERY route, error pages, response headers.

The route test walks the URL configuration itself, so an endpoint added
later without protection fails here even if nobody remembers to test it.
"""

from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import URLPattern, URLResolver, get_resolver

from coverage import importer

PUBLIC = {"login", "logout", "offer"}       # offer is protected by its signature instead


def named_routes(resolver=None, prefix=""):
    for p in (resolver or get_resolver()).url_patterns:
        if isinstance(p, URLResolver):
            if str(p.pattern).startswith("admin/"):
                continue                     # Django admin: its own staff login
            yield from named_routes(p, prefix + str(p.pattern))
        elif isinstance(p, URLPattern) and p.name:
            yield p.name, prefix + str(p.pattern)


def sample_url(pattern: str) -> str:
    return "/" + pattern.replace("<int:requirement_id>", "1").replace("<int:assignment_id>", "1") \
                        .replace("<str:token>", "not-a-real-token")


class EveryRouteIsProtected(TestCase):
    @classmethod
    def setUpTestData(cls):
        importer.load_folder(settings.COVERCAST_DEMO_DATA_DIR)

    def test_anonymous_never_gets_a_page_or_data(self):
        routes = dict(named_routes())
        self.assertGreaterEqual(len(routes), 10, routes)
        anon = Client()
        for name, pattern in routes.items():
            if name in PUBLIC:
                continue
            for method in ("get", "post"):
                with self.subTest(route=name, method=method):
                    r = getattr(anon, method)(sample_url(pattern))
                    self.assertIn(r.status_code, (302, 403, 405), f"{name} answered {r.status_code}")
                    if r.status_code == 302:
                        self.assertTrue(r["Location"].startswith("/login/"), r["Location"])

    def test_a_logged_in_non_supervisor_never_gets_data(self):
        c = Client()
        c.force_login(get_user_model().objects.create_user("viewer"))
        for name, pattern in named_routes():
            if name in PUBLIC or name == "home":
                continue
            with self.subTest(route=name):
                r = c.get(sample_url(pattern) + ("?date=2030-10-14" if "board" in name else ""))
                self.assertIn(r.status_code, (403, 405), f"{name} answered {r.status_code}")

    def test_offer_link_without_a_valid_signature_is_refused(self):
        self.assertEqual(Client().get("/offer/1:abc:def").status_code, 403)


@override_settings(DEBUG=False)
class ErrorPagesWhenDebugIsOff(TestCase):
    def test_404_is_arabic_and_reveals_nothing(self):
        r = Client().get("/opportunities/")
        self.assertEqual(r.status_code, 404)
        self.assertContains(r, "الصفحة غير موجودة", status_code=404)
        self.assertNotContains(r, "URLconf", status_code=404)          # no route list
        self.assertNotContains(r, "config.urls", status_code=404)

    def test_500_is_arabic_and_reveals_nothing(self):
        sup = get_user_model().objects.create_superuser("s", "s@example.com", "x")
        c = Client(raise_request_exception=False)
        c.force_login(sup)
        with mock.patch("coverage.views.day_board", side_effect=RuntimeError("secret detail")):
            r = c.get("/board?date=2030-10-14")
        self.assertEqual(r.status_code, 500)
        self.assertContains(r, "حدث خطأ في الخادم", status_code=500)
        self.assertNotContains(r, "secret detail", status_code=500)
        self.assertNotContains(r, "Traceback", status_code=500)


class Headers(TestCase):
    def test_pages_cannot_be_framed_or_sniffed_and_do_not_leak_the_offer_link(self):
        r = Client().get("/login/")
        self.assertEqual(r["X-Frame-Options"], "DENY")
        self.assertEqual(r["X-Content-Type-Options"], "nosniff")
        self.assertEqual(r["Referrer-Policy"], "same-origin")

    def test_session_is_bounded_and_not_readable_by_scripts(self):
        self.assertEqual(settings.SESSION_COOKIE_AGE, 12 * 3600)
        self.assertTrue(settings.SESSION_COOKIE_HTTPONLY)

    def test_refuses_to_start_in_production_mode_without_a_secret_key(self):
        import os
        import subprocess
        import sys
        env = {k: v for k, v in os.environ.items() if not k.startswith("COVERCAST_")}
        env.update(COVERCAST_DEBUG="0", DJANGO_SETTINGS_MODULE="config.settings")
        r = subprocess.run([sys.executable, "-c", "import django; django.setup()"], cwd=settings.BASE_DIR,
                           env=env, capture_output=True, text=True, timeout=60)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("COVERCAST_SECRET_KEY must be set", r.stderr)

"""CoverCast settings.

One settings module, driven by environment variables:
- COVERCAST_SECRET_KEY  required when COVERCAST_DEBUG is off
- COVERCAST_DEBUG       "1" (default) for local development and the offline demo
- COVERCAST_ALLOWED_HOSTS  comma-separated, defaults to localhost only
- COVERCAST_HTTPS       "1" only when served over HTTPS: secure cookies, redirect, HSTS
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

DEBUG = os.environ.get("COVERCAST_DEBUG", "1") == "1"

SECRET_KEY = os.environ.get("COVERCAST_SECRET_KEY", "")
if not SECRET_KEY:
    if not DEBUG:
        raise RuntimeError("COVERCAST_SECRET_KEY must be set when COVERCAST_DEBUG is off.")
    SECRET_KEY = "dev-only-insecure-key-not-for-deployment"

ALLOWED_HOSTS = [
    h.strip()
    for h in os.environ.get("COVERCAST_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
    if h.strip()
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "workforce",
    "coverage",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "coverage.context_processors.demo_data",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# SQLite for the MVP. "IMMEDIATE" makes every transaction take the write lock
# at BEGIN, so two approvals for the same gap run one after the other instead
# of both reading "gap = 1" and both writing (docs/16-learning-notes.md §8).
# select_for_update() is a no-op on SQLite, so we do not rely on it.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
        "OPTIONS": {
            "transaction_mode": "IMMEDIATE",
            "timeout": 20,
        },
        # Tests use a real file, not memory: the Phase 6 concurrency gate needs
        # two threads with two connections competing for the same lock.
        "TEST": {"NAME": BASE_DIR / "test_covercast.sqlite3"},
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# --- security ---
# `check --deploy` with COVERCAST_HTTPS=1 leaves only W005 and W021 (HSTS for
# subdomains / browser preload): domain-wide choices for whoever owns the
# domain, not for this app. Left visible on purpose, not silenced.
# Always on: Django's defaults already send X-Frame-Options: DENY,
# X-Content-Type-Options: nosniff and Referrer-Policy: same-origin (the last
# one keeps the employee's signed link from leaking to other sites).
SESSION_COOKIE_AGE = 12 * 60 * 60          # a supervisor signs in once per shift (A-29)
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = False               # the page's script must read it to send X-CSRFToken

# Only behind real HTTPS. The offline demo runs on http://127.0.0.1, where
# secure-only cookies would make sign-in impossible.
if os.environ.get("COVERCAST_HTTPS") == "1":
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 3600             # short on purpose: raise only once HTTPS is permanent

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "home"
LOGOUT_REDIRECT_URL = "login"

# The API answers a missing/expired CSRF token with the agreed JSON error.
CSRF_FAILURE_VIEW = "coverage.api.views.csrf_failure"

LANGUAGE_CODE = "ar"
TIME_ZONE = "Asia/Riyadh"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Folder holding the demo data pack that `load_demo` reads.
COVERCAST_DEMO_DATA_DIR = BASE_DIR / "data" / "demo" / "v0.2"

# The supervisor board asks the server every N ms whether anything changed
# (GET /board/live; 204 when nothing did). Polling, not WebSockets: no new
# infrastructure. Paused while the tab is hidden.
COVERCAST_BOARD_POLL_MS = int(os.environ.get("COVERCAST_BOARD_POLL_MS", "5000"))

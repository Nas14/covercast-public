"""CoverCast URLs.

/                 -> the board of the nearest day with shifts (supervisors)
/board?date=      the day's board
/board/live       the board's changing part, polled every 5 s (204 when nothing changed)
/requirements/<id>/   who can fill the open seat, and why
/requests         approved replacements and the employees' answers (read-only)
/requests/live    the same list for the 5-second poll (204 when nothing changed)
/api/...          JSON for the same screens (coverage/api, docs/06)
/offer/<token>    the employee's answer, by signed link (no login)
/login/ /logout/  supervisor session
/admin/           Django admin
"""

from django.contrib import admin
from django.contrib.auth import views as auth
from django.urls import include, path

from coverage import views

urlpatterns = [
    path("", views.home, name="home"),
    path("board", views.board, name="board"),
    path("board/live", views.board_live, name="board-live"),
    path("requirements/<int:requirement_id>/", views.candidates, name="candidates"),
    path("requests", views.requests_page, name="requests"),
    path("requests/live", views.requests_live, name="requests-live"),
    path("offer/<str:token>", views.offer, name="offer"),
    path("login/", auth.LoginView.as_view(template_name="coverage/login.html"), name="login"),
    path("logout/", auth.LogoutView.as_view(), name="logout"),
    path("api/", include("coverage.api.urls")),
    path("admin/", admin.site.urls),
]

from django.urls import path

from coverage.api import views

urlpatterns = [
    path("board", views.board, name="api-board"),
    path("forecast", views.forecast, name="api-forecast"),
    path("requirements/<int:requirement_id>/candidates", views.candidates, name="api-candidates"),
    path("requirements/<int:requirement_id>/approve", views.approve, name="api-approve"),
    path("assignments/<int:assignment_id>/absence", views.absence, name="api-absence"),
]

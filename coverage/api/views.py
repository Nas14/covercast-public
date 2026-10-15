"""The API endpoints of docs/06-api-contracts.md (the offer page is HTML,
in coverage/views.py). Each view: access check -> input -> one service -> JSON."""

from __future__ import annotations

import json
from datetime import date
from functools import wraps

from django.http import JsonResponse
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from coverage import policy
from coverage.api import serializers as S
from coverage.codes import ErrorCode
from coverage.models import Assignment, CoverageRequirement
from coverage.services.absence import report_absence
from coverage.services.approval import approve as approve_service
from coverage.services import snapshot
from coverage.services.board import day_board
from coverage.services.errors import CoverCastError
from coverage.services.evaluation import SlotNotOpen, evaluate_slot
from coverage.services.forecast_view import day_forecast
from coverage.services.offers import offer_token


def answer(data: dict, status: int = 200) -> JsonResponse:
    return JsonResponse(data, status=status, json_dumps_params={"ensure_ascii": False})


def error(exc: CoverCastError) -> JsonResponse:
    return answer(exc.as_dict(), status=exc.http_status)


def is_supervisor(user) -> bool:
    return user.is_authenticated and (user.is_superuser or user.groups.filter(name=policy.SUPERVISOR_GROUP).exists())


def supervisor_api(*methods):
    """Login + supervisor group + allowed methods; CoverCastError -> agreed JSON error."""
    def deco(view):
        @require_http_methods(list(methods))
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            try:
                if not request.user.is_authenticated:
                    raise CoverCastError(ErrorCode.AUTH_REQUIRED)
                if not is_supervisor(request.user):
                    raise CoverCastError(ErrorCode.NOT_SUPERVISOR)
                return view(request, *args, **kwargs)
            except CoverCastError as exc:
                return error(exc)
        return wrapped
    return deco


def body(request) -> dict:
    try:
        data = json.loads(request.body or b"{}")
    except (ValueError, UnicodeDecodeError) as exc:
        raise CoverCastError(ErrorCode.INVALID_INPUT, field="body") from exc
    if not isinstance(data, dict):
        raise CoverCastError(ErrorCode.INVALID_INPUT, field="body")
    return data


def get_or_404(model, pk, what: str):
    obj = model.objects.select_related(*({"CoverageRequirement": ("site", "shift"),
                                          "Assignment": ("site", "shift", "employee")}[model.__name__])
                                       ).filter(pk=pk).first()
    if obj is None:
        raise CoverCastError(ErrorCode.NOT_FOUND, what=what, id=pk)
    return obj


@supervisor_api("GET")
def board(request):
    raw = request.GET.get("date", "")
    try:
        day = date.fromisoformat(raw)
    except ValueError as exc:
        raise CoverCastError(ErrorCode.INVALID_INPUT, field="date", example="2030-10-14") from exc
    return answer(S.board_json(day, day_board(day), snapshot.skill_names()))


@supervisor_api("GET")
def forecast(request):
    """P0-6: the day's visitor-pressure level, its provenance and its staffing effect."""
    raw = request.GET.get("date", "")
    try:
        day = date.fromisoformat(raw)
    except ValueError as exc:
        raise CoverCastError(ErrorCode.INVALID_INPUT, field="date", example="2030-10-24") from exc
    return answer(S.forecast_json(day_forecast(day)))


@supervisor_api("GET")
def candidates(request, requirement_id: int):
    req = get_or_404(CoverageRequirement, requirement_id, "requirement")
    try:
        slot = int(request.GET.get("slot", "1"))
    except ValueError as exc:
        raise CoverCastError(ErrorCode.INVALID_INPUT, field="slot") from exc
    try:
        result = evaluate_slot(req.site, req.shift, slot)
    except SlotNotOpen as exc:
        raise CoverCastError(ErrorCode.GAP_ALREADY_FILLED, site=req.site.code, slot=slot) from exc
    return answer(S.candidates_json(result, snapshot.skill_names(), snapshot.site_names()))


@supervisor_api("POST")
def approve(request, requirement_id: int):
    req = get_or_404(CoverageRequirement, requirement_id, "requirement")
    data = body(request)
    code, key = data.get("employee_code"), data.get("idempotency_key")
    if not isinstance(code, str) or not code:
        raise CoverCastError(ErrorCode.INVALID_INPUT, field="employee_code")
    if not isinstance(key, str):
        raise CoverCastError(ErrorCode.INVALID_INPUT, field="idempotency_key")
    slot = data.get("slot", 1)
    if not isinstance(slot, int) or isinstance(slot, bool):
        raise CoverCastError(ErrorCode.INVALID_INPUT, field="slot")
    result = approve_service(req.site, req.shift, code, idempotency_key=key, supervisor=request.user,
                             slot_number=slot)
    url = request.build_absolute_uri(reverse("offer", args=[offer_token(result.assignment)]))
    return answer(S.approval_json(result, url), status=200 if result.replayed else 201)


@supervisor_api("POST")
def absence(request, assignment_id: int):
    a = get_or_404(Assignment, assignment_id, "assignment")
    kind = body(request).get("kind")
    if not isinstance(kind, str):
        raise CoverCastError(ErrorCode.INVALID_INPUT, field="kind")
    r = report_absence(a, kind)
    req = CoverageRequirement.objects.select_related("site", "shift").get(site=a.site, shift=a.shift)
    return answer({"absence_id": r.absence.id, "created": r.created, "kind": r.absence.kind,
                   "requirement": S.requirement_json(req, r.gap, snapshot.skill_names())}, status=201 if r.created else 200)


def csrf_failure(request, reason=""):
    """settings.CSRF_FAILURE_VIEW: JSON for the API, Django's page elsewhere."""
    if request.path.startswith("/api/"):
        return error(CoverCastError(ErrorCode.CSRF_FAILED))
    from django.views.csrf import csrf_failure as default
    return default(request, reason=reason)

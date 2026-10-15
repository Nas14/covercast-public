"""HTML pages for the supervisor (board, candidates) and the employee (offer).

The pages are built from the SAME dicts as the JSON API (coverage/api/
serializers.py), so a screen can never show something the API would not
say. Approve and absence buttons call the API with fetch(); nothing here
writes to the database except through the services.
"""

from __future__ import annotations

from datetime import date
from functools import wraps

from django.conf import settings
from django.contrib.auth.views import redirect_to_login
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseForbidden, HttpResponseNotAllowed
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_http_methods

from coverage import policy
from coverage.api import serializers as S
from coverage.api.views import is_supervisor
from coverage.codes import AssignmentStatus, ErrorCode, ExclusionCode
from coverage.models import Assignment, CoverageRequirement, Shift
from coverage.services import snapshot
from coverage.services.board import board_version, day_board
from coverage.services.errors import CoverCastError
from coverage.services.evaluation import SlotNotOpen, evaluate_slot
from coverage.services.forecast_view import LEVELS, day_forecast
from coverage.services.offers import read_token, respond
from coverage.services.reasons import counted, minutes_phrase
from workforce.models import CategoryRule

NATURE_AR = {"confirmed": "فجوة مؤكدة", "anticipated": "فجوة متوقعة"}
# How the comparison line names the rule "nearest" would break (gender-neutral wording).
BASELINE_RULE_AR = {
    "ABSENT": "يوجد غياب مسجّل في المناوبة",
    "CERT_EXPIRED": "الشهادة منتهية في تاريخ المناوبة",
    "SCOPE_OUT": "الموقع خارج نطاق الإعارة",
    "HOURS_CAP": "سقف الساعات الأسبوعي سيُتجاوز",
    "REST_GAP": "فاصل الراحة غير كافٍ",
}
PART_AR = {"proximity": "القرب", "hours_headroom": "هامش الساعات", "extra_skills": "مهارات إضافية",
           "donor_safety": "أمان السحب", "fairness": "العدالة"}


def gaps_phrase(n: int) -> str:
    return counted(n, "فجوة واحدة", "فجوتان", "فجوات", "فجوة")


def people_phrase(n: int) -> str:
    return counted(n, "شخصًا واحدًا", "شخصين", "أشخاص", "شخصًا")
KIND_ICON = {"positive": "✓", "caution": "!", "neutral": "•"}


def supervisor_page(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        if not is_supervisor(request.user):
            return render(request, "coverage/message.html",
                          {"title": "هذه الصفحة للمشرفين فقط",
                           "text": "الحساب الذي دخلت به ليس ضمن مجموعة المشرفين."}, status=403)
        return view(request, *args, **kwargs)
    return wrapped


def first_name(name: str) -> str:
    return name.split()[0] if name else name


# ------------------------------------------------------------------ board

def _seats(site: dict, ended: bool) -> list[dict]:
    """People and empty places of one site, in the order they are drawn."""
    seats = []
    for a in site["assignments"]:
        kind = "absent" if a["absent"] else ("awaiting" if a["awaiting_response"] else "present")
        seats.append({"kind": kind, "name": a["name"], "assignment_id": a["assignment_id"],
                      "can_report": not ended and not a["absent"]})
    for s in site["open_slots"]:
        seats.append({"kind": "open", "nature": site["gap_nature"], "slot": s["slot"],
                      "must_have_ar": s["must_have_ar"]})
    return seats


def _site_order(site: dict):
    rank = {"confirmed": 0, "anticipated": 1}.get(site["gap_nature"], 2)
    return (rank, -site["importance"], site["site_code"])


@supervisor_page
def home(request):
    days = list(Shift.objects.order_by("date").values_list("date", flat=True).distinct())
    if not days:
        return render(request, "coverage/message.html",
                      {"title": "لا توجد مناوبات", "text": "حمّل بيانات العرض بالأمر: python manage.py load_demo"})
    today = timezone.localdate()
    target = next((d for d in days if d >= today), days[-1])
    return redirect(f"{reverse('board')}?date={target.isoformat()}")


@supervisor_page
@ensure_csrf_cookie
def board(request):
    try:
        day = date.fromisoformat(request.GET.get("date", ""))
    except ValueError:
        return redirect("home")
    version = board_version(day)          # before the context: it never reads the snapshots it writes
    ctx = _board_context(day)
    ctx.update(live_version=version, poll_ms=getattr(settings, "COVERCAST_BOARD_POLL_MS", 5000))
    return render(request, "coverage/board.html", ctx)


def board_live(request):
    """The board's changing part, for the 5-second poll (coverage/static/coverage/covercast.js).

    GET /board/live?date=YYYY-MM-DD&v=<version the page has>
    - 204: nothing changed (read-only check: a few SELECTs, no writes, no lock).
    - 200: the fragment `_board_live.html`, its new version in X-Board-Version.
    - 403: not signed in, or not a supervisor. NEVER a redirect to the login page,
      so the script cannot mistake the login form for the board.
    - 400: bad date. 405: not GET.
    """
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    if not is_supervisor(request.user):
        return HttpResponseForbidden("", headers={"X-Board-Auth": "login" if not request.user.is_authenticated
                                                   else "forbidden"})
    try:
        day = date.fromisoformat(request.GET.get("date", ""))
    except ValueError:
        return HttpResponseBadRequest("")
    version = board_version(day)
    headers = {"X-Board-Version": version, "Cache-Control": "no-store"}
    if request.GET.get("v") == version:
        return HttpResponse(status=204, headers=headers)
    response = render(request, "coverage/_board_live.html", _board_context(day))
    for k, v in headers.items():
        response[k] = v
    return response


def _board_context(day: date) -> dict:
    """Everything the board shows for `day`: the full page and the live fragment share it."""
    days = list(Shift.objects.order_by("date").values_list("date", flat=True).distinct())
    data = S.board_json(day, day_board(day), snapshot.skill_names())
    f = day_forecast(day)
    forecast = _forecast_panel(S.forecast_json(f))
    now = timezone.now()
    ends = dict(Shift.objects.filter(date=day).values_list("slot", "ends_at"))
    shifts, decisions, waiting = [], [], []
    for sh in data["shifts"]:
        ended = now >= ends[sh["slot"]]
        sites = sorted(sh["sites"], key=_site_order)
        for site in sites:
            site["seats"] = _seats(site, ended)
            site["nature_ar"] = NATURE_AR.get(site["gap_nature"])
            site["volume_reason"] = next((d for d in site["drivers"] if d["kind"] == "forecast"), None)
        open_sites = [s for s in sites if s["gap_nature"]]
        for s in open_sites:
            decisions.append({**s, "shift_label": sh["label_ar"], "start_hm": sh["starts_at"][11:16],
                              "end_hm": sh["ends_at"][11:16]})
        # Approved, waiting for the employee (design: the blue state). The seat
        # already counts as covered (ADR 0005), so these are not decisions.
        for s in sites:
            names = [seat["name"] for seat in s["seats"] if seat["kind"] == "awaiting"]
            if names and not s["gap_nature"]:
                waiting.append({**s, "shift_label": sh["label_ar"], "start_hm": sh["starts_at"][11:16],
                                "end_hm": sh["ends_at"][11:16], "waiting_names": names})
        shifts.append({**sh, "sites": sites, "open_count": len(open_sites),
                       "open_phrase": f"{gaps_phrase(len(open_sites))} من {len(sites)} موقعًا",
                       "start_hm": sh["starts_at"][11:16], "end_hm": sh["ends_at"][11:16]})
    n = data["summary"]["open"]
    if not shifts:
        verdict = ("هذا يوم من إعادة عرض أوساكا 2025: للاطلاع على التنبؤ ومقارنته بالفعلي فقط، فلا مناوبات فيه ولا فجوات."
                   if forecast and forecast["mode"] == "osaka_replay" else "لا توجد مناوبات في هذا اليوم.")
    elif not n:
        verdict = ("كل المواقع الحرجة مغطاة في المناوبتين. لا شيء ينتظر قرارك." if len(shifts) == 2 else
                   "كل المواقع الحرجة مغطاة في هذا اليوم. لا شيء ينتظر قرارك.")
    else:
        verdict = "المقعد الفارغ مكتوب عليه من يملؤه. افتحه لترى المرشحين ولماذا."
    waiting_verdict = "اعتُمد البديل، والمقعد محجوز له حتى يرد. تتحدّث اللوحة وحدها عند وصول الرد."
    # Navigation: through the shift days, or (on an Osaka replay day) through the replay days.
    nav = days if day in days or day not in f.replay_dates else list(f.replay_dates)
    i = nav.index(day) if day in nav else None
    return {
        "decisions_phrase": gaps_phrase(len(decisions)),
        "day": day, "shifts": shifts, "decisions": decisions, "summary": data["summary"], "verdict": verdict,
        "prev_day": nav[i - 1] if i else None,
        "next_day": nav[i + 1] if i is not None and i + 1 < len(nav) else None,
        "forecast": forecast,
        "replay_link": f.replay_dates[-1] if f.replay_dates and day in days else None,
        "back_to_shifts": days[0] if days and day not in days else None,
        "no_shifts": not shifts,
        "waiting": waiting, "waiting_verdict": waiting_verdict,
        "waiting_phrase": counted(len(waiting), "ردّ واحد", "ردّين", "ردود", "ردًّا"),
    }


def _forecast_panel(fj: dict) -> dict | None:
    """The forecast dict of the API plus what the panel draws (no new facts)."""
    fc = fj["forecast"]
    if fc is None:
        return None
    eff = fc["staffing_effect"]
    rows = [c for c in eff["by_category"] if c["extra_staff_per_site"]]
    for c in rows:
        c["sites_phrase"] = "، ".join(c["sites"])
        c["total"] = c["extra_staff_per_site"] * len(c["sites"])
    v = fc["visitors"]
    if v:
        # A number line wide enough to show both level cut-points, the 80% band and the actual.
        th, tp = v["threshold_high"], v["threshold_peak"]
        lo, hi = min(v["p10"], v["actual"], th), max(v["p90"], v["actual"], tp)
        pad = (hi - lo) * 0.06 or 1
        lo, hi = lo - pad, hi + pad
        pos = lambda x: round(100 * (x - lo) / (hi - lo), 1)  # noqa: E731
        v["bar"] = {"p10": pos(v["p10"]), "p50": pos(v["p50"]), "p90": pos(v["p90"]), "actual": pos(v["actual"]),
                    "high": pos(th), "peak": pos(tp)}
        v["error_pct"] = round(100 * (v["p50"] - v["actual"]) / v["actual"], 1)
        v["horizon_phrase"] = counted(v["horizon_days"], "يوم واحد", "يومين", "أيام", "يومًا")
        v["fmt"] = {k: f"{round(v[k]):,}" for k in ("p10", "p50", "p90", "actual", "threshold_high", "threshold_peak")}
        v["actual_level_ar"] = {"normal": "عادي", "high": "مرتفع", "peak": "ذروة"}.get(v["actual_level"] or "", "")
    return {**fc, "effect_rows": rows, "total_added": eff["total_added"],
            "sites_phrase": counted(sum(len(c["sites"]) for c in rows), "موقع واحد", "موقعين", "مواقع", "موقعًا"),
            "applies": eff["applies_to_shifts"],
            "rules_loaded": fj["rules_loaded"],
            "meter": [{"level": lv, "label": {"normal": "عادي", "high": "مرتفع", "peak": "ذروة"}[lv],
                       "on": LEVELS.index(lv) <= LEVELS.index(fc["level"]), "current": lv == fc["level"]}
                      for lv in LEVELS]}


# ------------------------------------------------------------- candidates

@supervisor_page
@ensure_csrf_cookie
def candidates(request, requirement_id: int):
    req = CoverageRequirement.objects.select_related("site", "shift").filter(pk=requirement_id).first()
    if req is None:
        return render(request, "coverage/message.html",
                      {"title": "الخانة غير موجودة", "text": "ارجع إلى اللوحة واختر الموقع من جديد."}, status=404)
    try:
        slot = int(request.GET.get("slot", "1"))
        result = evaluate_slot(req.site, req.shift, slot)
    except (ValueError, SlotNotOpen):
        return render(request, "coverage/message.html", {
            "title": "هذه الخانة لم تعد مفتوحة",
            "text": f"{req.site.name} مغطى الآن في هذه المناوبة.",
            "back": f"{reverse('board')}?date={req.shift.date.isoformat()}"}, status=409)
    site_names = snapshot.site_names()
    d = S.candidates_json(result, snapshot.skill_names(), site_names)
    labels = dict(CategoryRule.Category.choices)
    for c in d["candidates"]:
        c["donor_site_name"] = site_names.get(c["donor_site"], c["donor_site"])
        c["score_display"] = f"{c['score']:.2f}"
        c["category_ar"] = labels.get(c["category"], c["category"])
        c["first_name"] = first_name(c["name"])
        for r in c["reasons"]:
            r["icon"] = KIND_ICON.get(r["kind"], "")
        c["parts"] = [{"key": k, "label": PART_AR[k], "value": v, "weight": policy.WEIGHTS[k],
                       "pct": round(100 * float(v) / policy.WEIGHTS[k], 1)} for k, v in c["score_parts"].items()]
        c["near_cap"] = any(r["code"] == "NEAR_HOURS_CAP" for r in c["reasons"])
    groups = []
    for code, n in d["excluded_summary"].items():
        people = [x for x in d["excluded"] if x["failed_rule"] == code]
        for x in people:
            x["category_ar"] = labels.get(x["category"], x["category"])
        groups.append({"code": code, "label": ExclusionCode(code).label, "count": n, "people": people})
    b = d["baseline"]
    if b:
        b["breaks_site_name"] = site_names.get(b["breaks_site"], b["breaks_site"])
        b["rule_ar"] = BASELINE_RULE_AR.get(b["violates"] or "")
    source = d["slot"]["source"]
    why = next((dr["label"] for dr in d["requirement"]["drivers"]
                if source.startswith("event:") and dr.get("kind") == "event"), None)
    rq = d["requirement"]
    if why is None and source == "headcount" and rq["volume_uplift"] and rq["present"] >= rq["base"]:
        # the seat exists only because of the visitor-pressure level (P0-6)
        why = next((dr["label"] for dr in rq["drivers"] if dr.get("kind") == "forecast"), None)
    shift = d["requirement"]["shift"]
    return render(request, "coverage/candidates.html", {
        "d": d, "req": req, "groups": groups, "why": why,
        "nature_ar": NATURE_AR.get(d["requirement"]["gap_nature"]),
        "start_hm": shift["starts_at"][11:16], "end_hm": shift["ends_at"][11:16],
        "back": f"{reverse('board')}?date={req.shift.date.isoformat()}",
        "evaluated": len(d["candidates"]) + len(d["excluded"]),
        "baseline_minutes": minutes_phrase(b["travel"]) if b else "",
        "baseline_short": people_phrase(b["donor_short_by"]) if b else "",
    })


# ------------------------------------------------------------------ offer

@require_http_methods(["GET", "POST"])
def offer(request, token: str):
    try:
        pk = read_token(token)
        assignment = (Assignment.objects.select_related("site", "shift", "employee", "moved_from__site")
                      .filter(pk=pk, origin="replacement").first())
        if assignment is None:
            raise CoverCastError(ErrorCode.OFFER_INVALID)
        outcome = None
        if request.method == "POST":
            action = request.POST.get("action")
            if action not in ("accept", "decline"):
                raise CoverCastError(ErrorCode.INVALID_INPUT, field="action")
            outcome = respond(token, accept=action == "accept")
            assignment = outcome.assignment
    except CoverCastError as exc:
        return render(request, "coverage/offer.html", {"error": exc}, status=exc.http_status)
    return render(request, "coverage/offer.html", {
        "a": assignment, "outcome": outcome, "first_name": first_name(assignment.employee.name),
        "starts": timezone.localtime(assignment.shift.starts_at),
        "ends": timezone.localtime(assignment.shift.ends_at),
        "open": assignment.status == AssignmentStatus.PLANNED and assignment.responded_at is None,
    })

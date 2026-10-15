"""Turn service results into the JSON shapes of docs/06-api-contracts.md.

Pure functions: no queries, no request. Field names here ARE the contract;
test_api.ApiContractTests fails if one changes.
"""

from __future__ import annotations

from collections import Counter
from decimal import Decimal

from django.utils.timezone import localtime

from coverage import policy
from coverage.codes import AttendanceLevel, ExclusionCode, ForecastMode, ShiftSlot
from coverage.services.forecast_view import PROVENANCE_AR
from coverage.services.reasons import TEMPLATES, render


def num(x):
    """Decimal -> JSON number with the same digits (60.83 stays 60.83)."""
    return float(x) if isinstance(x, Decimal) else x


def shift_json(shift) -> dict:
    return {"date": shift.date.isoformat(), "slot": shift.slot, "label_ar": ShiftSlot(shift.slot).label,
            # Riyadh local time (TIME_ZONE), e.g. 2030-10-14T07:00:00+03:00: same instant, readable hour
            "starts_at": localtime(shift.starts_at).isoformat(), "ends_at": localtime(shift.ends_at).isoformat()}


def slot_json(slot, names: dict | None = None) -> dict:
    must = sorted(slot.must_have)
    return {"slot": slot.number, "must_have": must,
            "must_have_ar": [(names or {}).get(s, s) for s in must], "source": slot.source}


def arabic_params(detail: dict, names: dict) -> dict:
    """Reason parameters with skill codes shown by their Arabic names."""
    params = dict(detail)
    if detail.get("skills"):
        params["skill"] = "، ".join(names.get(s, s) for s in detail["skills"])
    return params


def requirement_json(requirement, gap, names: dict | None = None) -> dict:
    """One site in one shift: what it needs, who covers it, what is open."""
    return {
        "id": requirement.id,
        "site_code": requirement.site.code,
        "site_name": requirement.site.name,
        "shift": shift_json(requirement.shift),
        "base": requirement.base,
        "event_uplift": requirement.event_uplift,
        "volume_uplift": requirement.volume_uplift,
        "required_total": requirement.required_total,
        "present": gap.filled,
        "headcount_gap": gap.headcount_gap,
        "gap_nature": gap.nature,
        "open_slots": [slot_json(s, names) for s in gap.open_slots],
        "drivers": requirement.drivers,
    }


def reason_json(code: str, params: dict, site_names: dict | None = None) -> dict:
    shown = dict(params)
    if site_names and params.get("site") in site_names:
        shown["site"] = site_names[params["site"]]
    return {"code": code, "kind": TEMPLATES[code][0], "text_ar": render(code, shown)}


def candidate_json(ev, must_total: int, site_names: dict | None = None) -> dict:
    return {
        "rank": ev.rank,
        "employee_code": ev.code,
        "name": ev.name,
        "category": ev.category,
        "tag": ev.tag,
        "score": num(ev.score),
        "score_parts": {k: num(v) for k, v in ev.score_parts.items()},
        "requirements_met": f"{must_total}/{must_total}",
        "travel": ev.travel_minutes,
        "hours_after": ev.hours_after,
        "hours_cap": ev.hours_cap,
        "donor_site": ev.donor_site,
        "reasons": [reason_json(c, p, site_names) for c, p in ev.reasons],
    }


def excluded_json(ev, names: dict | None = None) -> dict:
    return {
        "employee_code": ev.code,
        "name": ev.name,
        "category": ev.category,
        "failed_rule": ev.failed_rule,
        "reason_ar": render(ev.failed_rule, arabic_params(ev.detail, names or {})),
        "detail": {k: (v if isinstance(v, (str, int, float, bool, list, type(None))) else str(v))
                   for k, v in ev.detail.items()},
    }


def baseline_json(b) -> dict | None:
    if b is None:
        return None
    return {
        "employee": b.code,
        "name": b.name,
        "travel": b.travel_minutes,
        "violates": b.violates or None,
        "violates_ar": ExclusionCode(b.violates).label if b.violates else None,
        "breaks_site": b.breaks_site,
        "donor_short_by": b.donor_short_by,
    }


def candidates_json(result, names: dict | None = None, site_names: dict | None = None) -> dict:
    must_total = len(result.slot.must_have)
    return {
        "requirement": requirement_json(result.requirement, result.gap, names),
        "slot": slot_json(result.slot, names),
        "run_id": str(result.run_id),
        "candidates": [candidate_json(v, must_total, site_names) for v in result.ranked],
        "excluded": [excluded_json(v, names) for v in result.excluded],
        # in check order, so the UI can show "74 لا يحملون المهارة" first-rule-first
        "excluded_summary": {code: n for code in ExclusionCode.values
                             if (n := Counter(v.failed_rule for v in result.excluded)[code])},
        "baseline": baseline_json(result.baseline),
    }


def approval_json(result, offer_url: str) -> dict:
    a = result.assignment
    return {"assignment_id": a.id, "employee_code": a.employee.code, "status": a.status,
            "replayed": result.replayed, "moved_from_site": result.moved_from_site, "offer_url": offer_url}


def post_json(p) -> dict:
    return {"assignment_id": p.assignment_id, "employee_code": p.employee_code, "name": p.name,
            "status": p.status, "origin": p.origin, "absent": p.absent, "awaiting_response": p.awaiting_response}


def forecast_json(f) -> dict:
    """GET /api/forecast and the board's forecast panel: the same dict, built once.

    `forecast` is null when no active run covers the day. `visitors` is null for
    a Riyadh scenario day: its level is an assumption, no visitor number exists.
    """
    if f.row is None:
        return {"date": f.day.isoformat(), "forecast": None, "rules_loaded": f.rules_loaded}
    r, run = f.row, f.run
    visitors = None
    if r.p50 is not None:
        visitors = {"p10": r.p10, "p50": r.p50, "p90": r.p90, "actual": r.actual,
                    "actual_level": f.actual_level,
                    "as_of": r.as_of.isoformat() if r.as_of else None, "horizon_days": r.horizon_days,
                    "threshold_high": r.threshold_high, "threshold_peak": r.threshold_peak}
    return {
        "date": f.day.isoformat(),
        "forecast": {
            "level": r.level,
            "level_ar": AttendanceLevel(r.level).label,
            "mode": run.mode,
            "mode_ar": ForecastMode(run.mode).label,
            "data_origin": run.data_origin,
            "provenance_ar": PROVENANCE_AR[run.mode],
            "run": {"code": run.code, "model": run.model_name, "level_method": run.level_method,
                    "note": run.note, "freeze_sha256": run.freeze_sha256 or None},
            "visitors": visitors,
            "staffing_effect": {
                "applies_to_shifts": f.has_shifts,
                "total_added": f.total_added,
                "by_category": [
                    {"category": e.category, "category_ar": policy.VOLUME_CATEGORY_AR.get(e.category, e.category),
                     "site_types": list(e.site_types), "sites": list(e.sites), "extra_staff_per_site": e.per_site}
                    for e in f.effects
                ],
            },
        },
        "rules_loaded": f.rules_loaded,
    }


def board_json(day, rows, names: dict) -> dict:
    """GET /api/board and the board page: the same dict, built once."""
    shifts = []
    for row in rows:
        sites = []
        for c in row.cells:
            site = requirement_json(c.requirement, c.gap, names)
            site.update(zone=c.site.zone.code, importance=c.site.importance, site_type=c.site.site_type,
                        assignments=[post_json(p) for p in c.posts])
            sites.append(site)
        shifts.append({**shift_json(row.shift), "sites": sites})
    cells = [c for r in rows for c in r.cells]
    return {
        "date": day.isoformat(),
        "shifts": shifts,
        "summary": {"cells": len(cells),
                    "open": sum(c.gap.is_open for c in cells),
                    "confirmed": sum(c.gap.nature == "confirmed" for c in cells),
                    "anticipated": sum(c.gap.nature == "anticipated" for c in cells)},
    }

"""day_board(): every site in every shift of one day, for the supervisor's board.

For each site x shift: the live gap (same gap_for as everywhere), the stored
requirement snapshot (refreshed, so the API has a stable id to point at),
and who is assigned, including people with an absence and replacements
still waiting for the employee's answer.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from django.db import transaction
from django.utils import timezone

from coverage.codes import AssignmentOrigin, AssignmentStatus
from coverage.models import Assignment, CoverageRequirement, Shift, Site
from coverage.services import snapshot
from coverage.services.requirements import gap_for, refresh_requirement
from coverage.services.types import Gap
from workforce.models import Absence


@dataclass(frozen=True)
class Post:
    assignment_id: int
    employee_code: str
    name: str
    status: str
    origin: str
    absent: bool             # an Absence overlaps the shift: assigned, but not counted
    awaiting_response: bool  # approved replacement, employee has not answered yet


@dataclass(frozen=True)
class SiteCell:
    site: Site
    requirement: CoverageRequirement
    gap: Gap
    posts: tuple[Post, ...]


@dataclass(frozen=True)
class ShiftRow:
    shift: Shift
    cells: tuple[SiteCell, ...]


def day_board(day: date, *, now: datetime | None = None) -> list[ShiftRow]:
    now = now or timezone.now()
    shifts = list(Shift.objects.filter(date=day).order_by("starts_at"))
    sites = list(Site.objects.select_related("zone").order_by("code"))
    rules, parents = snapshot.demand_rules(), snapshot.skill_parents()

    assignments = list(Assignment.objects.filter(shift__in=shifts).exclude(status=AssignmentStatus.CANCELLED)
                       .select_related("employee").order_by("employee__code"))
    absences = list(Absence.objects.filter(starts_at__lt=max((s.ends_at for s in shifts), default=now),
                                           ends_at__gt=min((s.starts_at for s in shifts), default=now))
                    .values_list("employee_id", "starts_at", "ends_at"))

    def is_absent(emp_id: int, shift: Shift) -> bool:
        return any(e == emp_id and a < shift.ends_at and b > shift.starts_at for e, a, b in absences)

    rows = []
    with transaction.atomic():
        for shift in shifts:
            cells = []
            for site in sites:
                gap = gap_for(site, shift, rules=rules, parents=parents)
                requirement = refresh_requirement(site, shift, now=now, demand=gap.demand)
                requirement.site, requirement.shift = site, shift   # avoid two lookups per cell
                posts = tuple(
                    Post(a.id, a.employee.code, a.employee.name, a.status, a.origin,
                         absent=is_absent(a.employee_id, shift),
                         awaiting_response=(a.origin == AssignmentOrigin.REPLACEMENT
                                            and a.status == AssignmentStatus.PLANNED and a.responded_at is None))
                    for a in assignments if a.shift_id == shift.id and a.site_id == site.id
                )
                cells.append(SiteCell(site, requirement, gap, posts))
            rows.append(ShiftRow(shift, tuple(cells)))
    return rows


def board_version(day: date) -> str:
    """A short fingerprint of everything that can change what the board shows
    for `day` (P1 polling). READ-ONLY: a few SELECTs, no writes, so a browser
    can ask every few seconds without taking SQLite's write lock.

    Covers: the day's shifts and their assignments (status, site, response),
    absences touching the day, the day's forecast from an active run, the
    demand rules, event impacts and site requirements, and the skills of the
    people assigned that day. CoverageRequirement is left out on purpose: it is
    a snapshot the board itself rewrites, derived from the rest.
    """
    import hashlib
    from datetime import datetime, time, timedelta

    from coverage.models import AttendanceForecast, DemandRule, EventImpact, SiteRequirement
    from workforce.models import EmployeeSkill

    shifts = list(Shift.objects.filter(date=day).order_by("id").values_list("id", "starts_at", "ends_at"))
    tz = timezone.get_current_timezone()
    lo = min((s for _, s, _ in shifts), default=timezone.make_aware(datetime.combine(day, time.min), tz))
    hi = max((e for _, _, e in shifts), default=lo + timedelta(days=1))
    assignments = list(Assignment.objects.filter(shift_id__in=[s[0] for s in shifts]).order_by("id").values_list(
        "id", "employee_id", "site_id", "status", "origin", "responded_at", "moved_from_id"))
    people = sorted({a[1] for a in assignments})
    parts = [
        shifts,
        assignments,
        list(Absence.objects.filter(starts_at__lt=hi, ends_at__gt=lo).order_by("id")
             .values_list("id", "employee_id", "kind", "starts_at", "ends_at")),
        list(AttendanceForecast.objects.filter(date=day, run__is_active=True).order_by("id")
             .values_list("id", "run__code", "level")),
        list(DemandRule.objects.order_by("id").values_list("id", "site_type", "level", "extra_staff")),
        list(EventImpact.objects.order_by("id").values_list("id", "extra_staff")),
        list(SiteRequirement.objects.order_by("id").values_list("id", "min_count", "skill_id", "event_id")),
        list(EmployeeSkill.objects.filter(employee_id__in=people).order_by("id")
             .values_list("id", "skill_id", "expires_on")),
    ]
    return hashlib.sha256(repr(parts).encode()).hexdigest()[:16]

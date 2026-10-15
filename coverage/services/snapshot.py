"""The only place where the engine reads the database.

Turns ORM rows into the plain dataclasses of services/types.py, so that the
rules in demand.py and gaps.py never touch the ORM and can be tested with
hand-written inputs.
"""

from __future__ import annotations

from coverage.codes import ACTIVE_ASSIGNMENT_STATUSES
from coverage.models import AttendanceForecast, CalendarEvent, DemandRule, EventImpact, Shift, Site, SiteRequirement
from coverage.services.skills import effective_skills
from coverage.services.types import (
    EventInfo,
    ForecastInfo,
    ImpactLine,
    Person,
    RequirementLine,
    ShiftInfo,
    SiteInfo,
)
from workforce.models import Absence, EmployeeSkill, Skill


def site_info(site: Site) -> SiteInfo:
    return SiteInfo(code=site.code, base_staff=site.base_staff, site_type=site.site_type,
                    importance=site.importance, zone=site.zone.code)


def shift_info(shift: Shift) -> ShiftInfo:
    return ShiftInfo(date=shift.date, slot=shift.slot, starts_at=shift.starts_at, ends_at=shift.ends_at)


def event_info(event: CalendarEvent) -> EventInfo:
    return EventInfo(code=event.code, kind=event.kind, title=event.title,
                     starts_at=event.starts_at, ends_at=event.ends_at)


def requirement_lines(site: Site) -> list[RequirementLine]:
    rows = SiteRequirement.objects.filter(site=site).select_related("skill", "event")
    return [
        RequirementLine(skill=r.skill.code, min_count=r.min_count,
                        event=event_info(r.event) if r.event else None, bundle=r.bundle)
        for r in rows
    ]


def impact_lines(site: Site) -> list[ImpactLine]:
    rows = EventImpact.objects.filter(site=site).select_related("event")
    return [ImpactLine(event=event_info(r.event), extra_staff=r.extra_staff) for r in rows]


def forecast_for(shift: Shift) -> ForecastInfo | None:
    """The forecast of the shift's date from an ACTIVE run, or None.

    Only active runs count (at most one per mode, and the modes use disjoint
    dates, checked at import), so a day has at most one forecast. Rows without
    a run (from before ForecastRun existed) are never used.
    """
    row = (AttendanceForecast.objects.filter(date=shift.date, run__is_active=True)
           .select_related("run").order_by("run__mode").first())
    if row is None:
        return None
    return ForecastInfo(level=row.level, p50=row.p50, model_version=row.model_version,
                        mode=row.run.mode, run_code=row.run.code)


def demand_rules() -> dict[tuple[str, str], int]:
    return {(r.site_type, r.level): r.extra_staff for r in DemandRule.objects.all()}


def skill_parents() -> dict[str, str | None]:
    return {code: parent for code, parent in Skill.objects.values_list("code", "parent__code")}


def present_people(site: Site, shift: Shift, parents: dict[str, str | None] | None = None) -> list[Person]:
    """People really covering the site in this shift.

    Active assignment (planned / confirmed) AND no Absence record overlapping
    the shift. The second check matters: G2's no-show is recorded as an
    Absence while the assignment itself still says 'confirmed'.
    """
    parents = skill_parents() if parents is None else parents
    codes = dict(
        site.assignments.filter(shift=shift, status__in=ACTIVE_ASSIGNMENT_STATUSES)
        .values_list("employee_id", "employee__code")
    )
    absent = set(
        Absence.objects.filter(employee_id__in=codes, starts_at__lt=shift.ends_at, ends_at__gt=shift.starts_at)
        .values_list("employee_id", flat=True)
    )
    held: dict[int, list[tuple[str, object]]] = {}
    for emp_id, skill_code, expires_on in (
        EmployeeSkill.objects.filter(employee_id__in=codes).values_list("employee_id", "skill__code", "expires_on")
    ):
        held.setdefault(emp_id, []).append((skill_code, expires_on))
    people = [
        Person(code=code, skills=effective_skills(held.get(emp_id, []), parents, shift.date))
        for emp_id, code in codes.items()
        if emp_id not in absent
    ]
    return sorted(people, key=lambda p: p.code)


# ------------------------------------------------- candidates (Phase 5)
def skill_names() -> dict[str, str]:
    """code -> Arabic name, for texts shown to people (codes stay in the data)."""
    return dict(Skill.objects.values_list("code", "name_ar"))


def site_names() -> dict[str, str]:
    return dict(Site.objects.values_list("code", "name"))


def week_bounds(day):
    """Sunday-to-Saturday week containing `day` (policy.WEEK_START_WEEKDAY)."""
    from datetime import timedelta

    from coverage import policy

    start = day - timedelta(days=(day.weekday() - policy.WEEK_START_WEEKDAY) % 7)
    return start, start + timedelta(days=7)


def travel_matrix() -> dict[tuple[str, str], int]:
    from workforce.models import ZoneDistance

    return {(a, b): m for a, b, m in ZoneDistance.objects.values_list("from_zone__code", "to_zone__code", "minutes")}


def candidate_facts(site: Site, shift: Shift, parents: dict | None = None) -> list:
    """Facts about every active employee for one slot, in a fixed number of queries
    (independent of how many employees there are)."""
    from coverage.codes import AssignmentOrigin
    from coverage.models import Assignment
    from coverage.services.types import CandidateFacts, DonorSite
    from workforce.models import Employee

    parents = skill_parents() if parents is None else parents
    week_start, week_end = week_bounds(shift.date)

    employees = list(Employee.objects.filter(is_active=True).select_related("category")
                     .prefetch_related("scope_zones").order_by("code"))
    held: dict[int, list] = {}
    for emp_id, code, exp in EmployeeSkill.objects.values_list("employee_id", "skill__code", "expires_on"):
        held.setdefault(emp_id, []).append((code, exp))
    absences: dict[int, list] = {}
    for emp_id, kind, start, end in Absence.objects.values_list("employee_id", "kind", "starts_at", "ends_at"):
        absences.setdefault(emp_id, []).append((kind, start, end))

    rows = (Assignment.objects.filter(status__in=ACTIVE_ASSIGNMENT_STATUSES)
            .values_list("employee_id", "shift_id", "shift__date", "shift__starts_at", "shift__ends_at",
                         "site_id", "origin"))
    in_target: dict[int, int] = {}
    others: dict[int, list] = {}
    week_count: dict[int, int] = {}
    replacements: dict[int, int] = {}
    for emp_id, shift_id, day, start, end, site_id, origin in rows:
        if shift_id == shift.id:
            in_target[emp_id] = site_id
            continue
        others.setdefault(emp_id, []).append((start, end))
        if week_start <= day < week_end:
            week_count[emp_id] = week_count.get(emp_id, 0) + 1
            if origin == AssignmentOrigin.REPLACEMENT:
                replacements[emp_id] = replacements.get(emp_id, 0) + 1

    donors: dict[int, DonorSite] = {}

    def donor_for(site_id: int) -> DonorSite:
        if site_id not in donors:
            d_site = Site.objects.select_related("zone").get(pk=site_id)
            from coverage.services.requirements import demand_for

            donors[site_id] = DonorSite(
                code=d_site.code, zone=d_site.zone.code, importance=d_site.importance,
                demand=demand_for(d_site, shift), present=tuple(present_people(d_site, shift, parents)),
            )
        return donors[site_id]

    facts = []
    for e in employees:
        here = in_target.get(e.id)
        facts.append(CandidateFacts(
            code=e.code, name=e.name, category=e.category_id,
            hours_cap=e.category.max_weekly_hours, min_rest_hours=e.category.min_rest_hours,
            enforce_scope=e.category.enforce_scope,
            scope_zones=frozenset(z.code for z in e.scope_zones.all()),
            held=tuple(sorted(held.get(e.id, []), key=lambda h: h[0])),
            hours_before_window=e.hours_before_window,
            week_shift_count=week_count.get(e.id, 0),
            other_shifts=tuple(sorted(others.get(e.id, []))),
            absences=tuple(sorted(absences.get(e.id, []), key=lambda a: a[1])),
            assigned_here=here == site.id,
            donor=donor_for(here) if here is not None and here != site.id else None,
            recent_replacements=replacements.get(e.id, 0),
        ))
    return facts

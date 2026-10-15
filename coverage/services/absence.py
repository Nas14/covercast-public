"""report_absence(): the supervisor says someone did not come.

An Absence row is the single source of truth the engine reads (exactly how
the demo pack records E019 in G2): the assignment keeps its status, and the
person simply stops counting as present. The gap therefore appears by
itself on the next look, with no second place to keep in sync.
Reporting the same absence twice creates nothing new.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from django.db import transaction
from django.utils import timezone

from coverage.codes import ACTIVE_ASSIGNMENT_STATUSES, ErrorCode
from coverage.models import Assignment
from coverage.services.errors import CoverCastError
from coverage.services.requirements import gap_for, refresh_requirement
from coverage.services.types import Gap
from workforce.models import Absence


@dataclass(frozen=True)
class AbsenceReport:
    absence: Absence
    created: bool
    gap: Gap          # the site's gap right after the report


def report_absence(assignment: Assignment, kind: str, *, now: datetime | None = None) -> AbsenceReport:
    now = now or timezone.now()
    if kind not in Absence.Kind.values:
        raise CoverCastError(ErrorCode.INVALID_INPUT, field="kind", allowed=list(Absence.Kind.values))
    with transaction.atomic():
        a = Assignment.objects.select_related("shift", "site", "employee").get(pk=assignment.pk)
        if a.status not in ACTIVE_ASSIGNMENT_STATUSES:
            raise CoverCastError(ErrorCode.ASSIGNMENT_NOT_ACTIVE, status=a.status)
        if now >= a.shift.ends_at:
            raise CoverCastError(ErrorCode.SHIFT_ENDED, shift=str(a.shift))
        existing = Absence.objects.filter(employee=a.employee, starts_at__lt=a.shift.ends_at,
                                          ends_at__gt=a.shift.starts_at).first()
        if existing:
            absence, created = existing, False
        else:
            absence = Absence.objects.create(employee=a.employee, kind=kind, starts_at=a.shift.starts_at,
                                             ends_at=a.shift.ends_at, reported_at=now)
            created = True
        refresh_requirement(a.site, a.shift, now=now)
    return AbsenceReport(absence, created, gap_for(a.site, a.shift))

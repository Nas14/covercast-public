"""Timings for usability sessions (UX research plan, metric "approval time").

For every approved replacement:
- viewed_at:   the LAST time the candidates screen was opened for that site and
               shift before the approval (every opening stores audit rows with
               a timestamp; a refresh counts as a new opening)
- approved_at: when the supervisor pressed "approve"
- responded_at: when the employee answered on the phone, if they did

Read-only: nothing here changes data.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from coverage.codes import AssignmentOrigin
from coverage.models import Assignment, CandidateEvaluation


@dataclass(frozen=True)
class ApprovalTiming:
    site: str
    shift: str
    employee: str
    viewed_at: datetime | None
    approved_at: datetime
    responded_at: datetime | None
    status: str

    @property
    def seconds_to_approve(self) -> int | None:
        return None if self.viewed_at is None else round((self.approved_at - self.viewed_at).total_seconds())

    @property
    def seconds_to_respond(self) -> int | None:
        return None if self.responded_at is None else round((self.responded_at - self.approved_at).total_seconds())


def approval_timings(since: datetime | None = None) -> list[ApprovalTiming]:
    rows = (Assignment.objects.filter(origin=AssignmentOrigin.REPLACEMENT)
            .select_related("site", "shift", "employee").order_by("approved_at"))
    if since is not None:
        rows = rows.filter(approved_at__gte=since)
    out = []
    for a in rows:
        viewed = (CandidateEvaluation.objects
                  .filter(requirement__site=a.site, requirement__shift=a.shift, evaluated_at__lte=a.approved_at)
                  .order_by("-evaluated_at").values_list("evaluated_at", flat=True).first())
        out.append(ApprovalTiming(
            site=a.site.name, shift=f"{a.shift.date} {a.shift.get_slot_display()}",
            employee=a.employee.name, viewed_at=viewed, approved_at=a.approved_at,
            responded_at=a.responded_at, status=a.get_status_display(),
        ))
    return out

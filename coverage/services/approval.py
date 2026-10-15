"""approve(): the only way a replacement assignment comes into existence.

    approve(site, shift, employee_code, idempotency_key=..., supervisor=user)

Safety, step by step:

1. Same key again?  The first approval is returned, nothing new is created
   (a double click, or a retry after a lost response).
2. One transaction, opened with BEGIN IMMEDIATE (settings: transaction_mode).
   SQLite then lets only one writer in at a time: a second approval waits at
   the door until the first has committed, and only then reads the database.
   So the second one always sees the first one's result.
3. Inside, everything is checked AGAIN from the live database with the SAME
   functions that produced the ranking (gap_for, candidate_facts, evaluate).
   What the supervisor saw on screen may be minutes old; this check is not.
4. If the person is working at another site in this shift, that assignment
   is cancelled and remembered (moved_from) so a decline can restore it.
5. The new assignment starts as "planned": it already closes the gap
   (ADR 0005), and becomes "confirmed" when the employee accepts.
6. The database constraints are the last line of defence: one active
   assignment per person per shift, one row per idempotency key.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from django.db import IntegrityError, transaction
from django.utils import timezone

from coverage.codes import AssignmentOrigin, AssignmentStatus, ErrorCode
from coverage.models import Assignment, Shift, Site
from coverage.services import snapshot
from coverage.services.eligibility import evaluate
from coverage.services.errors import CoverCastError
from coverage.services.requirements import gap_for, refresh_requirement
from coverage.services.types import SlotContext
from workforce.models import Employee


@dataclass(frozen=True)
class ApprovalResult:
    assignment: Assignment
    replayed: bool          # True = this key was already approved; nothing new was created
    moved_from_site: str | None


def _replay(key: str, site: Site, shift: Shift, employee_code: str) -> ApprovalResult | None:
    found = Assignment.objects.select_related("employee", "moved_from__site").filter(idempotency_key=key).first()
    if found is None:
        return None
    if (found.site_id, found.shift_id, found.employee.code) != (site.id, shift.id, employee_code):
        raise CoverCastError(ErrorCode.IDEMPOTENCY_KEY_REUSED, idempotency_key=key)
    return ApprovalResult(found, replayed=True,
                          moved_from_site=found.moved_from.site.code if found.moved_from else None)


def approve(site: Site, shift: Shift, employee_code: str, *, idempotency_key: str, supervisor,
            slot_number: int = 1, now: datetime | None = None) -> ApprovalResult:
    now = now or timezone.now()
    if not idempotency_key or len(idempotency_key) > 64:
        raise CoverCastError(ErrorCode.INVALID_INPUT, field="idempotency_key")
    if supervisor is None:
        raise CoverCastError(ErrorCode.INVALID_INPUT, field="supervisor")

    # 1 (fast path, outside the lock; repeated inside because only that one counts)
    replay = _replay(idempotency_key, site, shift, employee_code)
    if replay:
        return replay

    with transaction.atomic():  # 2: BEGIN IMMEDIATE, one writer at a time
        replay = _replay(idempotency_key, site, shift, employee_code)
        if replay:
            return replay
        if now >= shift.ends_at:
            raise CoverCastError(ErrorCode.SHIFT_ENDED, shift=str(shift))
        employee = Employee.objects.filter(code=employee_code, is_active=True).first()
        if employee is None:
            raise CoverCastError(ErrorCode.EMPLOYEE_NOT_FOUND, employee_code=employee_code)

        # 3: the live gap, the live facts, the same rules as the ranking
        parents = snapshot.skill_parents()
        gap = gap_for(site, shift, parents=parents)
        slot = next((s for s in gap.open_slots if s.number == slot_number), None)
        if slot is None:
            raise CoverCastError(ErrorCode.GAP_ALREADY_FILLED, site=site.code, shift=str(shift),
                                 slot=slot_number, open_slots=len(gap.open_slots))
        ctx = SlotContext(site=gap.demand.site, shift=gap.demand.shift, slot=slot,
                          travel=snapshot.travel_matrix(), parents=parents)
        facts = next(f for f in snapshot.candidate_facts(site, shift, parents) if f.code == employee_code)
        verdict = evaluate(facts, ctx)
        if not verdict.eligible:
            raise CoverCastError(ErrorCode.CANDIDATE_NOT_ELIGIBLE, employee_code=employee_code,
                                 failed_rule=verdict.failed_rule, detail=_plain(verdict.detail))

        # 4: moving from another site in this shift?
        donor = (Assignment.objects.select_for_update().select_related("site")
                 .filter(employee=employee, shift=shift, status__in=(AssignmentStatus.PLANNED,
                                                                     AssignmentStatus.CONFIRMED))
                 .exclude(site=site).first())
        prior_status = ""
        if donor is not None:
            prior_status = donor.status
            donor.status = AssignmentStatus.CANCELLED
            donor.save(update_fields=["status"])

        # 5 + 6
        try:
            with transaction.atomic():  # savepoint: a constraint failure leaves no half-done move
                assignment = Assignment.objects.create(
                    employee=employee, site=site, shift=shift,
                    status=AssignmentStatus.PLANNED, origin=AssignmentOrigin.REPLACEMENT,
                    approved_by=supervisor, approved_at=now, idempotency_key=idempotency_key,
                    moved_from=donor, moved_from_status=prior_status,
                )
        except IntegrityError as exc:  # should be unreachable while step 2 holds
            raise CoverCastError(ErrorCode.GAP_ALREADY_FILLED, site=site.code, shift=str(shift),
                                 reason="constraint") from exc

        refresh_requirement(site, shift, now=now)
        if donor is not None:
            refresh_requirement(donor.site, shift, now=now)

    return ApprovalResult(assignment, replayed=False, moved_from_site=donor.site.code if donor else None)


def _plain(detail: dict) -> dict:
    return {k: (v if isinstance(v, (str, int, float, bool, list, type(None))) else str(v)) for k, v in detail.items()}

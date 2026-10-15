"""The employee's answer to an approved replacement, through a signed link.

    token = offer_token(assignment)          # goes into /offer/<token>
    respond(token, accept=True | False)

The link carries only the assignment id, signed with SECRET_KEY and a salt
(django.core.signing.TimestampSigner). Nobody can forge or alter it, and it
stops working after policy.OFFER_TOKEN_MAX_AGE_HOURS or when the shift ends.

  accept  -> planned  -> confirmed   (answering twice is harmless)
  decline -> planned  -> cancelled   the gap reopens by itself, because the
             engine only counts planned/confirmed. If the person had been
             MOVED from another site, that assignment is restored with the
             status it had, so declining never leaves them with no post.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from django.core import signing
from django.db import transaction
from django.utils import timezone

from coverage import policy
from coverage.codes import AssignmentOrigin, AssignmentStatus, ErrorCode
from coverage.models import Assignment
from coverage.services.errors import CoverCastError
from coverage.services.requirements import refresh_requirement


@dataclass(frozen=True)
class OfferResponse:
    assignment: Assignment
    accepted: bool
    repeated: bool               # the same answer was already recorded
    restored_site: str | None    # declined after a move: back at this site


def _signer() -> signing.TimestampSigner:
    return signing.TimestampSigner(salt=policy.OFFER_TOKEN_SALT)


def offer_token(assignment: Assignment, *, signed_at: datetime | None = None) -> str:
    """The employee's link. With `signed_at` (the approval time), the SAME validity
    window is rebuilt for a supervisor who needs the link again: it expires
    OFFER_TOKEN_MAX_AGE_HOURS after the approval, exactly like the first one,
    so showing it again never extends it."""
    signer = _signer()
    if signed_at is not None:
        stamp = signing.b62_encode(int(signed_at.timestamp()))
        signer.timestamp = lambda: stamp
    return signer.sign(str(assignment.pk))


def offer_expires_at(assignment: Assignment) -> datetime | None:
    """When the link of an approved replacement stops working (approval + max age)."""
    if assignment.approved_at is None:
        return None
    from datetime import timedelta
    return assignment.approved_at + timedelta(hours=policy.OFFER_TOKEN_MAX_AGE_HOURS)


def read_token(token: str) -> int:
    try:
        value = _signer().unsign(token, max_age=policy.OFFER_TOKEN_MAX_AGE_HOURS * 3600)
    except signing.SignatureExpired as exc:
        raise CoverCastError(ErrorCode.OFFER_EXPIRED) from exc
    except signing.BadSignature as exc:
        raise CoverCastError(ErrorCode.OFFER_INVALID) from exc
    return int(value)


def respond(token: str, *, accept: bool, now: datetime | None = None) -> OfferResponse:
    now = now or timezone.now()
    pk = read_token(token)
    with transaction.atomic():
        a = (Assignment.objects.select_for_update().select_related("shift", "site", "moved_from__site")
             .filter(pk=pk, origin=AssignmentOrigin.REPLACEMENT).first())
        if a is None:
            raise CoverCastError(ErrorCode.OFFER_INVALID)
        wanted = AssignmentStatus.CONFIRMED if accept else AssignmentStatus.CANCELLED
        if a.responded_at is not None:
            if a.status == wanted:
                return OfferResponse(a, accepted=accept, repeated=True, restored_site=None)
            raise CoverCastError(ErrorCode.OFFER_CLOSED, status=a.status)
        if a.status != AssignmentStatus.PLANNED:
            raise CoverCastError(ErrorCode.OFFER_CLOSED, status=a.status)  # e.g. withdrawn
        if now >= a.shift.ends_at:
            raise CoverCastError(ErrorCode.OFFER_EXPIRED, shift_ended_at=a.shift.ends_at.isoformat())

        a.status, a.responded_at = wanted, now
        a.save(update_fields=["status", "responded_at"])

        restored = None
        if not accept and a.moved_from is not None:
            old = a.moved_from
            if old.status == AssignmentStatus.CANCELLED:
                old.status = a.moved_from_status
                old.save(update_fields=["status"])
                restored = old.site.code
                refresh_requirement(old.site, a.shift, now=now)
        refresh_requirement(a.site, a.shift, now=now)
    return OfferResponse(a, accepted=accept, repeated=False, restored_site=restored)

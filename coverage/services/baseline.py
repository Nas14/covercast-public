"""The comparison CoverCast is judged against: "send the nearest qualified".

This is what a supervisor does today with a phone and a list: find someone
who holds the skills with a valid certificate, and pick the nearest. It
ignores secondment scope, hours, rest and, above all, the site that person
is leaving. We then ask CoverCast's own rules what that choice would break.
(pure, no ORM; idea from Mohammed's dataset, rebuilt on our data and rules)
"""

from __future__ import annotations

from collections.abc import Iterable

from coverage.services.eligibility import donor_damage, evaluate, travel_minutes
from coverage.services.skills import effective_skills
from coverage.services.types import Baseline, CandidateFacts, SlotContext


def nearest_qualified(people: Iterable[CandidateFacts], ctx: SlotContext) -> Baseline | None:
    pool = []
    for f in people:
        if f.assigned_here:
            continue
        if any(s < ctx.shift.ends_at and e > ctx.shift.starts_at for _, s, e in f.absences):
            continue
        if not ctx.slot.must_have <= effective_skills(f.held, ctx.parents, ctx.shift.date):
            continue
        pool.append((travel_minutes(f, ctx), f.code, f))
    if not pool:
        return None
    travel, _, pick = min(pool)  # nearest; equal distance -> lowest employee code
    verdict = evaluate(pick, ctx)
    damage = donor_damage(pick)
    return Baseline(
        code=pick.code, name=pick.name, travel_minutes=travel,
        violates="" if verdict.eligible else verdict.failed_rule,
        breaks_site=pick.donor.code if damage else None,
        donor_short_by=damage,
    )


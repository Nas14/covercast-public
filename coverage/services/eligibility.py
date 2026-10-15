"""filter_eligible: may this person fill this slot? (pure, no ORM)

Eight hard rules, checked in a fixed order. The FIRST failure is recorded,
so every excluded person carries exactly one clear reason, and the order
itself is part of the contract (tested).

  1 ABSENT            an absence overlaps the shift
  2 ALREADY_ASSIGNED  already at the target site in the target shift
  3 MISSING_SKILL     does not hold a required skill at all
  4 CERT_EXPIRED      holds it, but it is not valid on the SHIFT date
  5 SCOPE_OUT         secondment scope does not include the site's zone
  6 HOURS_CAP         weekly hours after the assignment exceed the cap
  7 REST_GAP          less rest than required next to another shift
  8 BREAKS_MINIMUM    leaving their current site would open a gap there

Rule 1 comes before 2 on purpose: in G2, E019's assignment at S10 still says
"confirmed" but he did not show up; "absent" is the true reason, not
"already assigned here" (this swaps rules 1 and 2 of phase-0-audit §9.4).
Rule 3 comes before 4 on purpose: someone who never had the certificate is
told "missing", someone whose certificate lapsed is told "expired".
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date

from coverage import policy
from coverage.codes import ExclusionCode
from coverage.services.gaps import detect_gaps
from coverage.services.skills import effective_skills
from coverage.services.types import CandidateFacts, Evaluation, SlotContext

NEVER = date.max  # "judge as if on the last possible day": used to ask "held at all?"


def travel_minutes(facts: CandidateFacts, ctx: SlotContext) -> int:
    """Zone-to-zone minutes from where they work this shift; off-duty = fixed call-in time."""
    if facts.donor is None:
        return policy.OFF_DUTY_CALL_IN_MINUTES
    return ctx.travel[(facts.donor.zone, ctx.site.zone)]


def hours_after(facts: CandidateFacts) -> int:
    """Weekly hours if assigned to the target shift.

    week_shift_count EXCLUDES the target shift, so the target shift is always
    added once: for an off-duty person it is a new shift; for someone moved
    from another site in the same shift it is the shift they were working
    anyway, now at a different site. Either way they work it (errors log E-06).
    """
    return facts.hours_before_window + policy.SHIFT_HOURS * (facts.week_shift_count + 1)


def donor_damage(facts: CandidateFacts) -> int:
    """How many open slots their current site would gain if they left (0 = safe)."""
    if facts.donor is None:
        return 0
    before = detect_gaps(facts.donor.demand, facts.donor.present)
    after = detect_gaps(facts.donor.demand, [p for p in facts.donor.present if p.code != facts.code])
    return max(0, len(after.open_slots) - len(before.open_slots))


def evaluate(facts: CandidateFacts, ctx: SlotContext) -> Evaluation:
    def fail(code: ExclusionCode, **detail) -> Evaluation:
        return Evaluation(code=facts.code, name=facts.name, category=facts.category,
                          eligible=False, failed_rule=code.value, detail=detail)

    shift = ctx.shift
    must = ctx.slot.must_have

    # 1
    for kind, start, end in facts.absences:
        if start < shift.ends_at and end > shift.starts_at:
            return fail(ExclusionCode.ABSENT, kind=kind)
    # 2
    if facts.assigned_here:
        return fail(ExclusionCode.ALREADY_ASSIGNED, site=ctx.site.code)
    # 3 held at all (validity ignored) -> 4 valid on the shift date
    ever = effective_skills(((code, None) for code, _ in facts.held), ctx.parents, NEVER)
    missing = sorted(must - ever)
    if missing:
        return fail(ExclusionCode.MISSING_SKILL, skill="، ".join(missing), skills=missing)
    valid = effective_skills(facts.held, ctx.parents, shift.date)
    expired = sorted(must - valid)
    if expired:
        dates = sorted(str(exp) for code, exp in facts.held if code in expired and exp)
        return fail(ExclusionCode.CERT_EXPIRED, skill="، ".join(expired), date="، ".join(dates),
                    skills=expired, shift_date=str(shift.date))
    # 5
    if facts.enforce_scope and ctx.site.zone not in facts.scope_zones:
        return fail(ExclusionCode.SCOPE_OUT, zone=ctx.site.zone, scope=sorted(facts.scope_zones))
    # 6
    after = hours_after(facts)
    if after > facts.hours_cap:
        return fail(ExclusionCode.HOURS_CAP, hours_after=after, cap=facts.hours_cap)
    # 7
    for start, end in facts.other_shifts:
        gap_h = ((shift.starts_at - end) if end <= shift.starts_at else (start - shift.ends_at)).total_seconds() / 3600
        if gap_h < facts.min_rest_hours:
            return fail(ExclusionCode.REST_GAP, rest=facts.min_rest_hours, rest_hours=round(max(gap_h, 0), 1))
    # 8
    damage = donor_damage(facts)
    if damage:
        return fail(ExclusionCode.BREAKS_MINIMUM, site=facts.donor.code, short_by=damage)

    return Evaluation(
        code=facts.code, name=facts.name, category=facts.category, eligible=True,
        travel_minutes=travel_minutes(facts, ctx), hours_after=after, hours_cap=facts.hours_cap,
        donor_site=facts.donor.code if facts.donor else None,
        donor_importance=facts.donor.importance if facts.donor else None,
        recent_replacements=facts.recent_replacements,
    )


def filter_eligible(people: Iterable[CandidateFacts], ctx: SlotContext) -> list[Evaluation]:
    """Judge everyone; returns one Evaluation per person, ordered by code."""
    return [evaluate(f, ctx) for f in sorted(people, key=lambda f: f.code)]

"""rank: order the eligible people by a score out of 100. (pure, no ORM)

Five parts (weights in policy.WEIGHTS). Each part is rounded to 0.01 and the
score is the SUM of the rounded parts, so the bar shown on screen always
adds up exactly to the number next to it.

  proximity       30  nearer is better; 5 min = 30, 25 min or more = 0
  hours_headroom  25  hours left before the weekly cap, after this shift
  extra_skills    20  share of nice-to-have skills held (none defined yet -> full 20 for all)
  donor_safety    15  15 if not working elsewhere; less the more critical the site they leave
  fairness        10  minus 5 per replacement shift already given this week

Ties: score (high first), then travel, then hours after, then employee code.
No randomness anywhere: the same input always gives the same order.

A score is a transparent ranking aid, NOT a probability that the choice is right.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from decimal import ROUND_HALF_UP, Decimal

from coverage import policy
from coverage.services.types import Evaluation

CENT = Decimal("0.01")


def _q(value) -> Decimal:
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


def score_parts(ev: Evaluation, nice_to_have: frozenset[str] = frozenset(), held: frozenset[str] = frozenset()) -> dict[str, Decimal]:
    w = policy.WEIGHTS
    best, worst = policy.PROXIMITY_BEST_MINUTES, policy.PROXIMITY_WORST_MINUTES
    closeness = min(1, max(0, (worst - ev.travel_minutes) / (worst - best)))
    headroom = max(0, ev.hours_cap - ev.hours_after) / ev.hours_cap
    extra = (len(nice_to_have & held) / len(nice_to_have)) if nice_to_have else 1
    donor = 1 if ev.donor_site is None else (5 - ev.donor_importance) / 4
    fair = max(0, w["fairness"] - policy.FAIRNESS_PENALTY_PER_RECENT_REPLACEMENT * ev.recent_replacements)
    return {
        "proximity": _q(w["proximity"] * closeness),
        "hours_headroom": _q(w["hours_headroom"] * headroom),
        "extra_skills": _q(w["extra_skills"] * extra),
        "donor_safety": _q(w["donor_safety"] * donor),
        "fairness": _q(fair),
    }


def _reasons(ev: Evaluation, must_total: int, nearest: int) -> tuple[tuple[str, dict], ...]:
    # "the nearest" only for whoever really is (ties included); everyone else
    # gets a neutral distance line (errors log E-13).
    distance = "NEAR" if ev.travel_minutes == nearest else "TRAVEL"
    out = [("REQ_MET", {"met": must_total, "total": must_total}),
           (distance, {"minutes": ev.travel_minutes})]
    if ev.donor_site:
        out.append(("KEEPS_MIN_AT_SITE", {"site": ev.donor_site}))
    near_cap = ev.hours_after >= ev.hours_cap - policy.SOFT_HOURS_MARGIN
    out.append(("NEAR_HOURS_CAP" if near_cap else "HOURS_OK", {"hours_after": ev.hours_after, "cap": ev.hours_cap}))
    return tuple(out)


def rank(evaluations: Iterable[Evaluation], *, must_total: int = 0) -> list[Evaluation]:
    """Eligible people, scored and ordered. Excluded people are dropped here
    (they keep their own record from filter_eligible)."""
    scored = []
    for ev in evaluations:
        if not ev.eligible:
            continue
        parts = score_parts(ev)
        scored.append(replace(ev, score_parts=parts, score=sum(parts.values(), Decimal("0"))))
    scored.sort(key=lambda e: (-e.score, e.travel_minutes, e.hours_after, e.code))
    nearest = min((e.travel_minutes for e in scored), default=0)
    ranked = []
    for i, ev in enumerate(scored, 1):
        tag = "recommended" if i == 1 else ("backup" if i == 2 else None)
        ranked.append(replace(ev, rank=i, tag=tag, reasons=_reasons(ev, must_total, nearest)))
    return ranked

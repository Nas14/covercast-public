"""detect_gaps: who is missing from one site in one shift. (pure, no ORM)

A gap has two dimensions, and either can exist without the other:

- headcount: fewer people present than required_total.
      G2: S10 needs 3, E019 did not show up, 2 are present.
- skill:     enough people, but nobody holding a required skill (or bundle).
      G1: S03 needs 2 and has 2, but neither is a Japanese-speaking paramedic.

Open slots are built so that one new person can close both at once:
skill slots first (each with its required skills), then only as many
generic headcount slots as the skill slots do not already cover.
"""

from __future__ import annotations

from collections.abc import Iterable

from coverage import policy
from coverage.services.types import Demand, Gap, Person, Slot


def detect_gaps(demand: Demand, present: Iterable[Person]) -> Gap:
    people = sorted(present, key=lambda p: p.code)
    n = len(people)
    headcount_gap = max(0, demand.required_total - n)
    base_headcount_short = max(0, demand.base - n)

    base_slots: list[tuple[frozenset[str], str]] = []
    event_slots: list[tuple[frozenset[str], str]] = []
    for need in demand.skill_needs:
        covered = sum(1 for p in people if need.skills <= p.skills)
        shortfall = max(0, need.count - covered)
        from_base = min(shortfall, max(0, need.base_count - covered))
        base_slots += [(need.skills, "base")] * from_base
        event_slots += [(need.skills, need.source)] * (shortfall - from_base)

    skill_slots = base_slots + event_slots
    generic_count = max(0, headcount_gap - len(skill_slots))
    generic_must_have: frozenset[str] = frozenset()
    if policy.GENERIC_SLOT_REQUIRES_BASE_SKILLS:
        generic_must_have = frozenset().union(*(nd.skills for nd in demand.skill_needs if nd.is_base))
    all_slots = skill_slots + [(generic_must_have, "headcount")] * generic_count

    slots = tuple(Slot(number=i, must_have=skills, source=src) for i, (skills, src) in enumerate(all_slots, 1))

    if not slots:
        nature = None
    elif base_slots or base_headcount_short > 0:
        nature = "confirmed"      # the permanent minimum itself is not met
    else:
        nature = "anticipated"    # only what events / the crowd add is missing

    return Gap(
        demand=demand, present=tuple(p.code for p in people),
        headcount_gap=headcount_gap, open_slots=slots, nature=nature,
    )

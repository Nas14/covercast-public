"""Which skills does a person really hold on a given day? (pure, no ORM)"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date


def effective_skills(
    held: Iterable[tuple[str, date | None]],
    parents: Mapping[str, str | None],
    on_date: date,
) -> frozenset[str]:
    """Skills valid on `on_date`, plus every ancestor they satisfy.

    held:    (skill_code, expires_on) pairs; expires_on None = never expires.
    parents: skill_code -> parent code (Advanced First Aid -> Basic First Aid).

    The hierarchy is walked UPWARD only: holding Advanced satisfies Basic,
    holding Basic never satisfies Advanced. An expired certificate gives
    nothing, not even its parent: if your advanced card expired, you cannot
    claim the basic one through it.
    """
    result: set[str] = set()
    for code, expires_on in held:
        if expires_on is not None and expires_on < on_date:
            continue
        node: str | None = code
        while node is not None and node not in result:
            result.add(node)
            node = parents.get(node)
    return frozenset(result)

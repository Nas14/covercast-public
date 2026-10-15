"""Who can fill an open slot, ranked, explained, compared, and recorded.

    evaluate_slot(site, shift, slot_number=1)

1. live gap for the site and shift (gaps.py); the slot must be open
2. CoverageRequirement snapshot refreshed (one row per site x shift)
3. facts about every active employee (snapshot.candidate_facts)
4. filter_eligible  -> one verdict per person, first failed rule
5. rank             -> score out of 100, parts, order, tags, reasons
6. baseline         -> what "nearest qualified" would have done
7. one CandidateEvaluation row per person (audit), grouped by run_id

Nothing here creates an assignment: that needs a supervisor (Phase 6).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from django.db import transaction
from django.utils import timezone

from coverage.models import CandidateEvaluation, CoverageRequirement, Shift, Site
from coverage.services import snapshot
from coverage.services.baseline import nearest_qualified
from coverage.services.eligibility import filter_eligible
from coverage.services.ranking import rank
from coverage.services.requirements import gap_for, refresh_requirement
from coverage.services.types import Baseline, Evaluation, Gap, Slot, SlotContext


class SlotNotOpen(Exception):
    """The requested slot does not exist (the site is covered, or fewer slots are open)."""


@dataclass(frozen=True)
class SlotEvaluation:
    run_id: uuid.UUID
    requirement: CoverageRequirement
    gap: Gap
    slot: Slot
    ranked: tuple[Evaluation, ...]
    excluded: tuple[Evaluation, ...]
    baseline: Baseline | None


def _jsonable(d: dict) -> dict:
    return {k: (str(v) if not isinstance(v, (str, int, float, bool, list, dict, type(None))) else v)
            for k, v in d.items()}


def evaluate_slot(site: Site, shift: Shift, slot_number: int = 1, *, persist: bool = True,
                  now: datetime | None = None) -> SlotEvaluation:
    now = now or timezone.now()
    parents = snapshot.skill_parents()
    gap = gap_for(site, shift, parents=parents)
    slot = next((s for s in gap.open_slots if s.number == slot_number), None)
    if slot is None:
        raise SlotNotOpen(f"{site.code} {shift}: no open slot #{slot_number}")

    ctx = SlotContext(site=gap.demand.site, shift=gap.demand.shift, slot=slot,
                      travel=snapshot.travel_matrix(), parents=parents)
    facts = snapshot.candidate_facts(site, shift, parents)
    verdicts = filter_eligible(facts, ctx)
    ranked = tuple(rank(verdicts, must_total=len(slot.must_have)))
    excluded = tuple(v for v in verdicts if not v.eligible)
    baseline = nearest_qualified(facts, ctx)

    run_id = uuid.uuid4()
    with transaction.atomic():
        requirement = refresh_requirement(site, shift, now=now)
        if persist:
            ids = dict(site.assignments.model._meta.get_field("employee").related_model.objects
                       .values_list("code", "id"))
            CandidateEvaluation.objects.bulk_create(
                CandidateEvaluation(
                    requirement=requirement, employee_id=ids[v.code], run_id=run_id, slot=slot.number,
                    eligible=v.eligible, failed_rule=v.failed_rule, failed_detail=_jsonable(v.detail),
                    score=v.score, score_parts={k: str(x) for k, x in v.score_parts.items()},
                    rank=v.rank, tag=v.tag or "", evaluated_at=now,
                )
                for v in (*ranked, *excluded)
            )
    return SlotEvaluation(run_id=run_id, requirement=requirement, gap=gap, slot=slot,
                          ranked=ranked, excluded=excluded, baseline=baseline)

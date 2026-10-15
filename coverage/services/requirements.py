"""Connects the database snapshot to the pure engine functions.

- gap_for(site, shift)              read-only: the live gap right now
- refresh_requirement(site, shift)  stores the CoverageRequirement snapshot
- window_gaps()                     every site x shift, for the board

The gap is always computed from live assignments, never read from storage,
so it cannot disagree with the schedule.
"""

from __future__ import annotations

from datetime import datetime

from django.db import transaction
from django.utils import timezone

from coverage.models import CoverageRequirement, Shift, Site
from coverage.services import snapshot
from coverage.services.demand import build_demand
from coverage.services.gaps import detect_gaps
from coverage.services.types import Demand, Gap


def demand_for(site: Site, shift: Shift, *, rules: dict | None = None) -> Demand:
    return build_demand(
        site=snapshot.site_info(site),
        shift=snapshot.shift_info(shift),
        lines=snapshot.requirement_lines(site),
        impacts=snapshot.impact_lines(site),
        forecast=snapshot.forecast_for(shift),
        demand_rules=snapshot.demand_rules() if rules is None else rules,
    )


def gap_for(site: Site, shift: Shift, *, rules: dict | None = None, parents: dict | None = None) -> Gap:
    demand = demand_for(site, shift, rules=rules)
    return detect_gaps(demand, snapshot.present_people(site, shift, parents))


@transaction.atomic
def refresh_requirement(site: Site, shift: Shift, *, now: datetime | None = None,
                        demand: Demand | None = None) -> CoverageRequirement:
    """Store (or update in place) the one requirement row for this site and shift.

    Pass `demand` when it was just computed (the board does, for 24 cells) to
    avoid building it twice; it must be the demand of this same site and shift.
    """
    if demand is None:
        demand = demand_for(site, shift)
    elif (demand.site.code, demand.shift.date, demand.shift.slot) != (site.code, shift.date, shift.slot):
        raise ValueError("demand belongs to another site or shift")
    row, _ = CoverageRequirement.objects.update_or_create(
        site=site, shift=shift,
        defaults={
            "base": demand.base,
            "event_uplift": demand.event_uplift,
            "volume_uplift": demand.volume_uplift,
            "required_total": demand.required_total,
            "drivers": [d.as_dict() for d in demand.drivers],
            "computed_at": now or timezone.now(),
        },
    )
    return row


def window_gaps(shifts=None, sites=None) -> list[Gap]:
    """Every site x shift (all of them by default), ordered by time then site."""
    shifts = list(shifts if shifts is not None else Shift.objects.order_by("starts_at"))
    sites = list(sites if sites is not None else Site.objects.select_related("zone").order_by("code"))
    rules, parents = snapshot.demand_rules(), snapshot.skill_parents()
    return [gap_for(site, shift, rules=rules, parents=parents) for shift in shifts for site in sites]

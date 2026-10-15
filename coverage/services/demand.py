"""build_demand: what one site needs in one shift, and why. (pure, no ORM)

    required_total = base + event_uplift + volume_uplift

- base          the site's permanent minimum headcount.
- event_uplift  extra headcount from events NOT covered by a forecast
                (delegation, VIP visit, conference ...).
- volume_uplift extra headcount for the expected crowd: DemandRule for this
                site type at the forecast level. 0 when there is no forecast.
- skill_needs   which skills must be present, from base lines and from the
                events running during the shift.

Double counting (policy.FORECAST_COVERED_EVENT_KINDS, docs/18 section 3.1):
a national day or holiday raises the crowd.
- A forecast covers the day: the forecast already sees that crowd, so the
  event adds SKILLS only; its headcount is ignored and reported as such.
- No forecast for the day: the event's headcount is the only estimate of its
  crowd, so it is counted (fallback), and volume_uplift is 0.
The two cases exclude each other, so the crowd is never counted twice.
(Until 2026-10-15 the headcount was ignored even with no forecast: defect.)
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping

from coverage import policy
from coverage.services.types import (
    Demand,
    Driver,
    EventInfo,
    ForecastInfo,
    ImpactLine,
    RequirementLine,
    ShiftInfo,
    SiteInfo,
    SkillNeed,
)


LEVEL_AR = {"normal": "عادي", "high": "مرتفع", "peak": "ذروة"}


def forecast_label(forecast: ForecastInfo) -> str:
    """Why the crowd adds staff, with its provenance in words (never by colour alone)."""
    level = LEVEL_AR.get(forecast.level, forecast.level)
    if forecast.mode == "riyadh_scenario":
        return f"ضغط الزوار: {level} (افتراض سيناريو محاكى، ليس تنبؤًا)"
    if forecast.p50 is None:
        return f"ضغط الزوار: {level}"
    return f"حضور متوقع: {level} ({forecast.p50:,} زائر)"


def _active(event: EventInfo | None, shift: ShiftInfo) -> bool:
    return event is None or shift.overlaps(event.starts_at, event.ends_at)


def build_demand(
    site: SiteInfo,
    shift: ShiftInfo,
    lines: Iterable[RequirementLine],
    impacts: Iterable[ImpactLine] = (),
    forecast: ForecastInfo | None = None,
    demand_rules: Mapping[tuple[str, str], int] | None = None,
) -> Demand:
    drivers: list[Driver] = [Driver("base", "الحد الأدنى الدائم للموقع", adds=site.base_staff)]
    active_lines = [ln for ln in lines if _active(ln.event, shift)]

    # ---- headcount from events
    event_uplift = 0
    event_adds: dict[str, int] = defaultdict(int)
    for impact in impacts:
        if not _active(impact.event, shift) or impact.extra_staff == 0:
            continue
        if impact.event.kind in policy.FORECAST_COVERED_EVENT_KINDS:
            if forecast is not None:
                drivers.append(Driver(
                    "ignored",
                    f"{impact.event.title}: +{impact.extra_staff} أُهملت لأن مستوى الزوار لهذا اليوم يشمل أثر هذا الحدث",
                ))
                continue
            drivers.append(Driver(
                "fallback",
                f"{impact.event.title}: لا يوجد مستوى زوار لهذا اليوم، فاحتُسب أثر الحدث (+{impact.extra_staff})",
            ))
        event_uplift += impact.extra_staff
        event_adds[impact.event.code] += impact.extra_staff

    # ---- skill needs
    needs: list[SkillNeed] = []

    # Lines without a bundle: per skill, the largest count wins (base or any
    # active event); they are NOT added. base_count remembers the base part,
    # so a shortfall can later be split into "base" vs "event".
    singles: dict[str, list[RequirementLine]] = defaultdict(list)
    bundles: dict[tuple[str, str], list[RequirementLine]] = defaultdict(list)
    for ln in active_lines:
        if ln.bundle:
            bundles[(ln.event.code if ln.event else "", ln.bundle)].append(ln)
        else:
            singles[ln.skill].append(ln)

    for skill in sorted(singles):
        group = singles[skill]
        base_count = max((ln.min_count for ln in group if ln.event is None), default=0)
        top = max(group, key=lambda ln: (ln.min_count, ln.event is None))
        source = "base" if top.event is None or top.min_count == base_count else f"event:{top.event.code}"
        needs.append(SkillNeed(frozenset({skill}), top.min_count, source, base_count=base_count))

    for (event_code, _bundle), group in sorted(bundles.items()):
        count = group[0].min_count  # equal across a bundle (checked at import)
        source = f"event:{event_code}" if event_code else "base"
        needs.append(SkillNeed(frozenset(ln.skill for ln in group), count, source,
                               base_count=count if source == "base" else 0))

    # ---- one driver per active event: what it adds
    events = {ln.event.code: ln.event for ln in active_lines if ln.event}
    events.update({i.event.code: i.event for i in impacts if _active(i.event, shift)})
    for code in sorted(events):
        ev = events[code]
        skills = tuple(sorted({ln.skill for ln in active_lines if ln.event and ln.event.code == code}))
        if skills or event_adds[code]:
            drivers.append(Driver("event", ev.title, adds=event_adds[code], skills=skills))

    # ---- headcount from the expected crowd (optional input)
    volume_uplift = 0
    if forecast is not None:
        volume_uplift = (demand_rules or {}).get((site.site_type, forecast.level), 0)
        if volume_uplift:
            drivers.append(Driver("forecast", forecast_label(forecast), adds=volume_uplift))

    needs.sort(key=lambda n: (not n.is_base, n.source, sorted(n.skills)))
    return Demand(
        site=site, shift=shift, base=site.base_staff, event_uplift=event_uplift,
        volume_uplift=volume_uplift, skill_needs=tuple(needs), drivers=tuple(drivers),
    )

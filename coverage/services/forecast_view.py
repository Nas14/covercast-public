"""day_forecast(day): the visitor-pressure level of one day, where it came from,
and what it adds to staffing. Read-only; used by GET /api/forecast and the board.

The staffing effect is computed from the same DemandRule table the engine uses
(services/demand.py), per site type, so the panel can never claim an effect
the engine does not apply. For an Osaka 2025 replay day there are no shifts:
the effect is shown as "what these rules WOULD add", and says so.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from coverage import policy
from coverage.codes import AttendanceLevel, ForecastMode
from coverage.forecast_import import level_of
from coverage.models import AttendanceForecast, ForecastRun, Shift, Site
from coverage.services import snapshot

PROVENANCE_AR = {
    ForecastMode.OSAKA_REPLAY: "أوساكا 2025، بيانات حضور رسمية: توقع النموذج المعتمد بالبروتوكول. ليس توقعًا للرياض 2030.",
    ForecastMode.RIYADH_SCENARIO: "الرياض 2030، سيناريو محاكى: المستوى مفروض افتراضًا، وليس تنبؤًا بالحضور.",
}


@dataclass(frozen=True)
class CategoryEffect:
    category: str
    site_types: tuple[str, ...]
    sites: tuple[str, ...]
    per_site: int


@dataclass(frozen=True)
class DayForecast:
    day: date
    row: AttendanceForecast | None
    run: ForecastRun | None
    effects: tuple[CategoryEffect, ...]
    rules_loaded: bool
    has_shifts: bool
    replay_dates: tuple[date, ...]   # every day of the active Osaka replay (for navigation)

    @property
    def total_added(self) -> int:
        return sum(e.per_site * len(e.sites) for e in self.effects)

    @property
    def actual_level(self) -> str | None:
        r = self.row
        if r is None or r.actual is None or r.threshold_high is None:
            return None
        return level_of(r.actual, r.threshold_high, r.threshold_peak)


def day_forecast(day: date) -> DayForecast:
    row = (AttendanceForecast.objects.filter(date=day, run__is_active=True)
           .select_related("run").order_by("run__mode").first())
    rules = snapshot.demand_rules()
    effects: list[CategoryEffect] = []
    if row is not None:
        by_cat: dict[str, dict] = {}
        for code, site_type in Site.objects.order_by("code").values_list("code", "site_type"):
            cat = policy.VOLUME_CATEGORY_BY_SITE_TYPE.get(site_type, site_type)
            add = rules.get((site_type, row.level), 0)
            e = by_cat.setdefault(cat, {"types": set(), "sites": [], "adds": set()})
            e["types"].add(site_type)
            e["sites"].append(code)
            e["adds"].add(add)
        order = list(policy.VOLUME_CATEGORY_AR)
        for cat in sorted(by_cat, key=lambda c: (order.index(c) if c in order else len(order), c)):
            e = by_cat[cat]
            # one value per category (the loader checks it); if a hand edit broke that, show the largest
            effects.append(CategoryEffect(cat, tuple(sorted(e["types"])), tuple(e["sites"]), max(e["adds"])))
    replay = ForecastRun.objects.filter(mode=ForecastMode.OSAKA_REPLAY, is_active=True).first()
    return DayForecast(
        day=day, row=row, run=row.run if row else None, effects=tuple(effects), rules_loaded=bool(rules),
        has_shifts=Shift.objects.filter(date=day).exists(),
        replay_dates=tuple(replay.days.order_by("date").values_list("date", flat=True)) if replay else (),
    )


LEVELS = tuple(AttendanceLevel.values)

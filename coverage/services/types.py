"""Plain data that flows through the coverage engine.

Frozen dataclasses: once built they cannot change, so a function that
receives one cannot surprise its caller by editing it. None of these know
about the database; services/snapshot.py builds them from the ORM.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime


# ---------------------------------------------------------------- inputs
@dataclass(frozen=True)
class SiteInfo:
    code: str
    base_staff: int
    site_type: str
    importance: int
    zone: str


@dataclass(frozen=True)
class ShiftInfo:
    date: date
    slot: str
    starts_at: datetime
    ends_at: datetime

    def overlaps(self, start: datetime, end: datetime) -> bool:
        return start < self.ends_at and end > self.starts_at


@dataclass(frozen=True)
class EventInfo:
    code: str
    kind: str
    title: str
    starts_at: datetime
    ends_at: datetime


@dataclass(frozen=True)
class RequirementLine:
    """One SiteRequirement row. event is None for a permanent base line."""

    skill: str
    min_count: int
    event: EventInfo | None = None
    bundle: str = ""


@dataclass(frozen=True)
class ImpactLine:
    """One EventImpact row: extra headcount while the event runs."""

    event: EventInfo
    extra_staff: int


@dataclass(frozen=True)
class ForecastInfo:
    """The visitor-pressure level for a day, from an ACTIVE forecast run.

    p50 is None for a Riyadh scenario day: the level is a scenario assumption
    and no visitor number is invented. mode/run_code say where it came from.
    """

    level: str
    p50: int | None
    model_version: str
    mode: str = ""
    run_code: str = ""


@dataclass(frozen=True)
class Person:
    """Someone assigned to the slot's shift, with the skills valid on the shift date."""

    code: str
    skills: frozenset[str]


# ---------------------------------------------------------------- outputs
@dataclass(frozen=True)
class Driver:
    """Why a part of the requirement exists (shown to the supervisor)."""

    kind: str          # "base" | "event" | "fallback" | "forecast" | "ignored"
    label: str
    adds: int = 0      # headcount it adds
    skills: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {"kind": self.kind, "label": self.label, "adds": self.adds, "skills": list(self.skills)}


@dataclass(frozen=True)
class SkillNeed:
    """`count` people who EACH hold every skill in `skills`.

    base_count is the part of `count` that exists without any event. Example:
    S04 needs 1 security licence always and 2 during the VIP visit, so
    count=2, base_count=1. If only 1 holder is present, the shortfall is the
    event's part (anticipated); if none are present, 1 of the 2 missing is base
    (confirmed).
    """

    skills: frozenset[str]
    count: int
    source: str        # "base" or "event:<code>"
    base_count: int = 0

    @property
    def is_base(self) -> bool:
        return self.source == "base"


@dataclass(frozen=True)
class Demand:
    site: SiteInfo
    shift: ShiftInfo
    base: int
    event_uplift: int
    volume_uplift: int
    skill_needs: tuple[SkillNeed, ...]
    drivers: tuple[Driver, ...]

    @property
    def required_total(self) -> int:
        return self.base + self.event_uplift + self.volume_uplift


@dataclass(frozen=True)
class Slot:
    """One open place. must_have empty = any eligible person."""

    number: int
    must_have: frozenset[str]
    source: str        # "base", "event:<code>" or "headcount"


@dataclass(frozen=True)
class Gap:
    demand: Demand
    present: tuple[str, ...]          # codes of people actually covering
    headcount_gap: int
    open_slots: tuple[Slot, ...] = field(default=())
    nature: str | None = None         # None (covered) | "confirmed" | "anticipated"

    @property
    def filled(self) -> int:
        return len(self.present)

    @property
    def is_open(self) -> bool:
        return bool(self.open_slots)


# ------------------------------------------------- candidates (Phase 5)
@dataclass(frozen=True)
class DonorSite:
    """Where a candidate is working during the target shift, if anywhere else."""

    code: str
    zone: str
    importance: int
    demand: Demand
    present: tuple[Person, ...]


@dataclass(frozen=True)
class CandidateFacts:
    """Everything the rules need to judge one person for one slot."""

    code: str
    name: str
    category: str
    hours_cap: int
    min_rest_hours: int
    enforce_scope: bool
    scope_zones: frozenset[str]
    held: tuple[tuple[str, date | None], ...]          # (skill, expires_on), ignoring validity
    hours_before_window: int
    week_shift_count: int                               # active shifts this week, target shift excluded
    other_shifts: tuple[tuple[datetime, datetime], ...] # active shifts, target shift excluded
    absences: tuple[tuple[str, datetime, datetime], ...]
    assigned_here: bool                                 # already at the target site in the target shift
    donor: DonorSite | None                             # working elsewhere during the target shift
    recent_replacements: int = 0


@dataclass(frozen=True)
class SlotContext:
    site: SiteInfo
    shift: ShiftInfo
    slot: Slot
    travel: dict                                        # (from_zone, to_zone) -> minutes
    parents: dict                                       # skill -> parent skill


@dataclass(frozen=True)
class Evaluation:
    code: str
    name: str
    category: str
    eligible: bool
    failed_rule: str = ""
    detail: dict = field(default_factory=dict)
    travel_minutes: int | None = None
    hours_after: int | None = None
    hours_cap: int | None = None
    donor_site: str | None = None
    donor_importance: int | None = None
    recent_replacements: int = 0
    # filled by rank()
    score_parts: dict = field(default_factory=dict)
    score: object = None                                # Decimal
    rank: int | None = None
    tag: str | None = None
    reasons: tuple[tuple[str, dict], ...] = ()


@dataclass(frozen=True)
class Baseline:
    """What 'send the nearest qualified person' would have done."""

    code: str
    name: str
    travel_minutes: int
    violates: str            # first rule CoverCast would have failed them on, "" if none
    breaks_site: str | None  # donor site left below what it needs
    donor_short_by: int

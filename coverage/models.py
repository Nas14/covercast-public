"""Where people must be, who is there, and what the engine concluded.

No business logic here: models hold data and the integrity rules the
database itself can enforce. Rules that compare people, shifts and sites
live in coverage/services/.
"""

from django.conf import settings
from django.db import models
from django.db.models import F, Q

from coverage import policy
from coverage.codes import (
    ACTIVE_ASSIGNMENT_STATUSES,
    AssignmentOrigin,
    AssignmentStatus,
    AttendanceLevel,
    DataOrigin,
    EventKind,
    ExclusionCode,
    ForecastMode,
    ShiftSlot,
)
from workforce.models import Employee, Skill, Zone


class Site(models.Model):
    code = models.CharField(max_length=8, unique=True)
    name = models.CharField(max_length=128)
    zone = models.ForeignKey(Zone, on_delete=models.PROTECT, related_name="sites")
    site_type = models.CharField(max_length=32)
    base_staff = models.PositiveSmallIntegerField(help_text="Minimum headcount at all times.")
    importance = models.PositiveSmallIntegerField(help_text="1 (low) to 5 (critical).")

    class Meta:
        ordering = ["code"]
        constraints = [
            models.CheckConstraint(condition=Q(base_staff__gte=1), name="site_base_staff_min_1"),
            models.CheckConstraint(condition=Q(importance__gte=1, importance__lte=5), name="site_importance_1_5"),
        ]

    def __str__(self) -> str:
        return f"{self.code} {self.name}"


class CalendarEvent(models.Model):
    code = models.CharField(max_length=16, unique=True)
    kind = models.CharField(max_length=16, choices=EventKind.choices)
    title = models.CharField(max_length=200)
    country = models.CharField(max_length=64, blank=True)
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    data_origin = models.CharField(max_length=16, choices=DataOrigin.choices, default=DataOrigin.SIMULATED)

    class Meta:
        ordering = ["starts_at", "code"]
        constraints = [
            models.CheckConstraint(condition=Q(ends_at__gt=F("starts_at")), name="event_ends_after_start"),
        ]

    def __str__(self) -> str:
        return f"{self.code} {self.title}"

    @property
    def is_forecast_covered(self) -> bool:
        return self.kind in policy.FORECAST_COVERED_EVENT_KINDS


class SiteRequirement(models.Model):
    """A skill a site needs.

    - event is empty: permanent base requirement.
    - event set, bundle empty: minimum TOTAL for that skill while the event
      runs (the larger of base and event wins; they are not added).
    - same event + same bundle on several rows: `min_count` people who each
      hold ALL the bundle's skills.
    """

    site = models.ForeignKey(Site, on_delete=models.CASCADE, related_name="requirements")
    skill = models.ForeignKey(Skill, on_delete=models.PROTECT, related_name="site_requirements")
    min_count = models.PositiveSmallIntegerField()
    event = models.ForeignKey(
        CalendarEvent, null=True, blank=True, on_delete=models.CASCADE, related_name="site_requirements"
    )
    bundle = models.CharField(max_length=16, blank=True)
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(min_count__gte=1), name="site_requirement_min_count_1"),
            # SQL treats every NULL as different, so one unique constraint on
            # (site, skill, event) would NOT stop two identical base rows.
            # Base rows and event rows therefore get one constraint each.
            models.UniqueConstraint(
                fields=["site", "skill"], condition=Q(event__isnull=True), name="site_requirement_unique_base"
            ),
            models.UniqueConstraint(
                fields=["site", "skill", "event"],
                condition=Q(event__isnull=False),
                name="site_requirement_unique_event",
            ),
        ]

    def __str__(self) -> str:
        scope = f"event {self.event_id}" if self.event_id else "base"
        return f"{self.site_id} {self.skill_id} x{self.min_count} ({scope})"


class EventImpact(models.Model):
    """Extra HEADCOUNT an event adds to a site (skills go in SiteRequirement)."""

    event = models.ForeignKey(CalendarEvent, on_delete=models.CASCADE, related_name="impacts")
    site = models.ForeignKey(Site, on_delete=models.CASCADE, related_name="event_impacts")
    extra_staff = models.PositiveSmallIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["event", "site"], name="event_impact_unique"),
        ]

    # No clean() refusal any more for national_day / holiday headcount (decision
    # approved 2026-10-15, docs/18 section 3.1): it is stored as the FALLBACK used
    # only when no forecast covers the day. The double-counting guard moved to
    # services/demand.py, where the forecast is known.


class Shift(models.Model):
    date = models.DateField()
    slot = models.CharField(max_length=8, choices=ShiftSlot.choices)
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()

    class Meta:
        ordering = ["starts_at"]
        constraints = [
            models.UniqueConstraint(fields=["date", "slot"], name="shift_unique_date_slot"),
            models.CheckConstraint(condition=Q(ends_at__gt=F("starts_at")), name="shift_ends_after_start"),
        ]

    def __str__(self) -> str:
        return f"{self.date} {self.slot}"


class Assignment(models.Model):
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="assignments")
    site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name="assignments")
    shift = models.ForeignKey(Shift, on_delete=models.PROTECT, related_name="assignments")
    status = models.CharField(max_length=12, choices=AssignmentStatus.choices, default=AssignmentStatus.PLANNED)
    origin = models.CharField(max_length=12, choices=AssignmentOrigin.choices, default=AssignmentOrigin.SCHEDULE)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="approvals"
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    responded_at = models.DateTimeField(null=True, blank=True)
    # One approval request = one key. A second click with the same key finds
    # the row instead of creating another.
    idempotency_key = models.CharField(max_length=64, null=True, blank=True)
    # When an approved replacement MOVES someone from another site in the same
    # shift, their old assignment is cancelled and remembered here, with the
    # status it had, so that a decline can put them back exactly as they were.
    moved_from = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="moved_to"
    )
    moved_from_status = models.CharField(max_length=12, choices=AssignmentStatus.choices, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["shift__starts_at", "site__code", "employee__code"]
        constraints = [
            # The core guarantee: one active assignment per person per shift.
            models.UniqueConstraint(
                fields=["employee", "shift"],
                condition=Q(status__in=ACTIVE_ASSIGNMENT_STATUSES),
                name="assignment_one_active_per_shift",
            ),
            models.UniqueConstraint(
                fields=["idempotency_key"],
                condition=Q(idempotency_key__isnull=False),
                name="assignment_unique_idempotency_key",
            ),
            # A replacement exists only because a person approved it.
            models.CheckConstraint(
                condition=Q(origin=AssignmentOrigin.SCHEDULE)
                | Q(approved_by__isnull=False, approved_at__isnull=False),
                name="assignment_replacement_needs_approval",
            ),
            models.CheckConstraint(
                condition=Q(moved_from__isnull=True, moved_from_status="")
                | Q(moved_from__isnull=False, origin=AssignmentOrigin.REPLACEMENT)
                & Q(moved_from_status__in=ACTIVE_ASSIGNMENT_STATUSES),
                name="assignment_move_is_a_replacement_with_prior_status",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.employee_id}@{self.site_id} {self.shift} [{self.status}]"

    @property
    def is_active(self) -> bool:
        return self.status in ACTIVE_ASSIGNMENT_STATUSES


class CoverageRequirement(models.Model):
    """Snapshot of what one site needs in one shift, with where each part came from.

    The gap is NOT stored: it is required_total minus the live active
    assignments, so it can never disagree with the schedule.
    """

    site = models.ForeignKey(Site, on_delete=models.CASCADE, related_name="coverage_requirements")
    shift = models.ForeignKey(Shift, on_delete=models.CASCADE, related_name="coverage_requirements")
    base = models.PositiveSmallIntegerField()
    event_uplift = models.PositiveSmallIntegerField(default=0)
    volume_uplift = models.PositiveSmallIntegerField(default=0)
    required_total = models.PositiveSmallIntegerField()
    drivers = models.JSONField(default=list, blank=True)
    computed_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["site", "shift"], name="coverage_requirement_unique_site_shift"),
            models.CheckConstraint(
                condition=Q(required_total=F("base") + F("event_uplift") + F("volume_uplift")),
                name="coverage_requirement_total_is_sum",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.site_id} {self.shift}: {self.required_total}"


class CandidateEvaluation(models.Model):
    """Audit row: how one person was judged for one open slot in one run."""

    requirement = models.ForeignKey(CoverageRequirement, on_delete=models.CASCADE, related_name="evaluations")
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="evaluations")
    run_id = models.UUIDField(help_text="Groups the rows of one evaluation run.")
    slot = models.PositiveSmallIntegerField(default=1)
    eligible = models.BooleanField()
    failed_rule = models.CharField(max_length=24, choices=ExclusionCode.choices, blank=True)
    failed_detail = models.JSONField(default=dict, blank=True)
    score = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    score_parts = models.JSONField(default=dict, blank=True)
    rank = models.PositiveSmallIntegerField(null=True, blank=True)
    tag = models.CharField(max_length=16, blank=True)
    evaluated_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["run_id", "slot", "employee"], name="evaluation_unique_per_run"),
            # Either eligible with no failed rule, or excluded with exactly one reason.
            models.CheckConstraint(
                condition=(Q(eligible=True) & Q(failed_rule=""))
                | (Q(eligible=False) & ~Q(failed_rule="")),
                name="evaluation_reason_matches_eligibility",
            ),
            models.CheckConstraint(
                condition=Q(rank__isnull=True) | Q(eligible=True),
                name="evaluation_rank_only_if_eligible",
            ),
        ]

    def __str__(self) -> str:
        verdict = f"rank {self.rank}" if self.eligible else self.failed_rule
        return f"{self.employee_id} for {self.requirement_id}: {verdict}"


class ForecastRun(models.Model):
    """One imported forecast file (docs/18 section 3; P0-2).

    The engine reads forecasts of ACTIVE runs only, at most one active run per
    mode. The two modes never mix: an Osaka replay is official 2025 data and
    adds staff to nothing (2025 has no shifts); a Riyadh scenario is simulated,
    carries no visitor numbers and no accuracy claim.
    """

    code = models.CharField(max_length=80, unique=True)
    mode = models.CharField(max_length=16, choices=ForecastMode.choices)
    model_name = models.CharField(max_length=64, help_text="seasonal_naive, or scenario_assumption")
    data_origin = models.CharField(max_length=16, choices=DataOrigin.choices)
    level_method = models.CharField(max_length=300)
    freeze_sha256 = models.CharField(max_length=64, blank=True)
    source_sha256 = models.CharField(max_length=64, help_text="sha256 of the imported file (idempotency)")
    note = models.CharField(max_length=300, blank=True)
    is_active = models.BooleanField(default=True)
    imported_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["mode", "-imported_at"]
        constraints = [
            models.UniqueConstraint(fields=["mode"], condition=Q(is_active=True), name="forecast_run_one_active_per_mode"),
            models.CheckConstraint(
                condition=(Q(mode=ForecastMode.OSAKA_REPLAY) & Q(data_origin=DataOrigin.OFFICIAL))
                | (Q(mode=ForecastMode.RIYADH_SCENARIO) & Q(data_origin=DataOrigin.SIMULATED)),
                name="forecast_run_mode_matches_origin",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.code} ({self.mode}{', active' if self.is_active else ''})"


class AttendanceForecast(models.Model):
    """One day of a ForecastRun.

    Osaka replay: p10/p50/p90, actual, as_of, horizon_days and the level
    thresholds of that origin are all required (checked at import).
    Riyadh scenario: only the level; no visitor numbers are invented.
    """

    run = models.ForeignKey(ForecastRun, on_delete=models.CASCADE, related_name="days", null=True, blank=True)
    date = models.DateField()
    p10 = models.PositiveIntegerField(null=True, blank=True)
    p50 = models.PositiveIntegerField(null=True, blank=True)
    p90 = models.PositiveIntegerField(null=True, blank=True)
    actual = models.PositiveIntegerField(null=True, blank=True)
    level = models.CharField(max_length=8, choices=AttendanceLevel.choices)
    as_of = models.DateField(null=True, blank=True, help_text="Last day the model saw.")
    horizon_days = models.PositiveSmallIntegerField(null=True, blank=True)
    threshold_high = models.FloatField(null=True, blank=True, help_text="P75 of data up to as_of.")
    threshold_peak = models.FloatField(null=True, blank=True, help_text="P90 of data up to as_of.")
    model_version = models.CharField(max_length=64)
    trained_on = models.CharField(max_length=128)
    data_origin = models.CharField(max_length=16, choices=DataOrigin.choices)
    imported_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["date"]
        constraints = [
            models.UniqueConstraint(fields=["run", "date"], name="forecast_unique_run_date"),
            models.CheckConstraint(
                condition=Q(p10__lte=F("p50")) & Q(p50__lte=F("p90")), name="forecast_quantiles_ordered"
            ),
            models.CheckConstraint(
                condition=Q(as_of__isnull=True) | Q(as_of__lt=F("date")), name="forecast_as_of_before_date"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.date} p50={self.p50} ({self.level})"


class DemandRule(models.Model):
    """Extra staff a site type needs at a given attendance level."""

    site_type = models.CharField(max_length=32)
    level = models.CharField(max_length=8, choices=AttendanceLevel.choices)
    extra_staff = models.PositiveSmallIntegerField(default=0)
    source_note = models.CharField(max_length=200, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["site_type", "level"], name="demand_rule_unique_type_level"),
        ]

    def __str__(self) -> str:
        return f"{self.site_type}@{self.level}: +{self.extra_staff}"

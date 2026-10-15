"""Who the people are, what they can do, and when they cannot work.

This app owns reference data that both apps use (Zone, Skill), so that the
dependency runs one way only: coverage -> workforce, never the reverse.
"""

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q

from workforce.codes import DataOrigin


class Zone(models.Model):
    code = models.CharField(max_length=8, unique=True)
    name = models.CharField(max_length=64)

    class Meta:
        ordering = ["code"]

    def __str__(self) -> str:
        return self.code


class ZoneDistance(models.Model):
    """Walking minutes between two zones (5x5 matrix in the demo pack)."""

    from_zone = models.ForeignKey(Zone, on_delete=models.CASCADE, related_name="distances_from")
    to_zone = models.ForeignKey(Zone, on_delete=models.CASCADE, related_name="distances_to")
    minutes = models.PositiveSmallIntegerField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["from_zone", "to_zone"], name="zone_distance_unique_pair"),
        ]

    def __str__(self) -> str:
        return f"{self.from_zone_id}->{self.to_zone_id}: {self.minutes} min"


class Skill(models.Model):
    class Kind(models.TextChoices):
        CERTIFICATION = "certification", "شهادة"
        ROLE = "role", "دور"
        LANGUAGE = "language", "لغة"

    code = models.CharField(max_length=32, unique=True)
    name_ar = models.CharField(max_length=64)
    name_en = models.CharField(max_length=64)
    kind = models.CharField(max_length=16, choices=Kind.choices)
    # Holding THIS skill also satisfies a requirement for `parent`
    # (Advanced First Aid -> Basic First Aid). Never the other way round.
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="children"
    )

    class Meta:
        ordering = ["code"]
        constraints = [
            models.CheckConstraint(
                condition=Q(parent__isnull=True) | ~Q(parent=F("id")),
                name="skill_parent_not_self",
            ),
        ]

    def __str__(self) -> str:
        return self.code

    def clean(self) -> None:
        # A longer cycle (A -> B -> A) cannot be expressed as a CHECK constraint.
        seen = {self.pk}
        node = self.parent
        while node is not None:
            if node.pk in seen:
                raise ValidationError({"parent": "Skill hierarchy would contain a cycle."})
            seen.add(node.pk)
            node = node.parent


class SourceEntity(models.Model):
    """The ministry, company or programme a person comes from."""

    name = models.CharField(max_length=128, unique=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "source entities"

    def __str__(self) -> str:
        return self.name


class CategoryRule(models.Model):
    """Rules that differ by workforce category (loan / contractor / volunteer)."""

    class Category(models.TextChoices):
        LOAN = "loan", "معار"
        CONTRACTOR = "contractor", "متعاقد"
        VOLUNTEER = "volunteer", "متطوع"

    category = models.CharField(max_length=16, choices=Category.choices, primary_key=True)
    max_weekly_hours = models.PositiveSmallIntegerField()
    min_rest_hours = models.PositiveSmallIntegerField()
    enforce_scope = models.BooleanField(
        help_text="If true, people in this category may only work in their scope zones."
    )
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(max_weekly_hours__gt=0), name="category_rule_hours_positive"),
        ]

    def __str__(self) -> str:
        return self.category


class Employee(models.Model):
    code = models.CharField(max_length=16, unique=True)
    name = models.CharField(max_length=128)
    category = models.ForeignKey(CategoryRule, on_delete=models.PROTECT, related_name="employees")
    source_entity = models.ForeignKey(SourceEntity, on_delete=models.PROTECT, related_name="employees")
    department = models.CharField(max_length=64, blank=True)
    home_zone = models.ForeignKey(Zone, on_delete=models.PROTECT, related_name="residents")
    # Hours this week that do NOT come from assignments stored in CoverCast.
    # Total hours are always computed as this + assignments, so they never go stale.
    hours_before_window = models.PositiveSmallIntegerField(default=0)
    # Secondment scope (loan category). ManyToMany: Django creates the join table.
    # Rule "loan => at least one zone, others => none" is enforced at import
    # time and by workforce.validators, because M2M rows need a saved instance
    # and so cannot be checked in clean().
    scope_zones = models.ManyToManyField(Zone, blank=True, related_name="scoped_employees")
    is_active = models.BooleanField(default=True)
    data_origin = models.CharField(max_length=16, choices=DataOrigin.choices, default=DataOrigin.SIMULATED)

    class Meta:
        ordering = ["code"]

    def __str__(self) -> str:
        return f"{self.code} {self.name}"


class EmployeeSkill(models.Model):
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="skills")
    skill = models.ForeignKey(Skill, on_delete=models.PROTECT, related_name="holders")
    # Empty = never expires (languages and roles). Checked against the SHIFT
    # date, never against today.
    expires_on = models.DateField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["employee", "skill"], name="employee_skill_unique"),
        ]

    def __str__(self) -> str:
        return f"{self.employee_id}:{self.skill_id}"

    def is_valid_on(self, day) -> bool:
        return self.expires_on is None or self.expires_on >= day


class Absence(models.Model):
    """Leave, sickness or a no-show, independent of any assignment."""

    class Kind(models.TextChoices):
        LEAVE = "leave", "إجازة"
        SICK = "sick", "مرضية"
        NO_SHOW = "no_show", "عدم حضور"

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="absences")
    kind = models.CharField(max_length=16, choices=Kind.choices)
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    reported_at = models.DateTimeField()

    class Meta:
        ordering = ["starts_at"]
        constraints = [
            models.CheckConstraint(condition=Q(ends_at__gt=F("starts_at")), name="absence_ends_after_start"),
        ]

    def __str__(self) -> str:
        return f"{self.employee_id} {self.kind} {self.starts_at:%Y-%m-%d %H:%M}"

from django.contrib import admin

from coverage.models import (
    Assignment,
    AttendanceForecast,
    CalendarEvent,
    CandidateEvaluation,
    CoverageRequirement,
    DemandRule,
    EventImpact,
    Shift,
    Site,
    SiteRequirement,
)


class SiteRequirementInline(admin.TabularInline):
    model = SiteRequirement
    extra = 0


@admin.register(Site)
class SiteAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "zone", "site_type", "base_staff", "importance")
    inlines = [SiteRequirementInline]


@admin.register(Assignment)
class AssignmentAdmin(admin.ModelAdmin):
    list_display = ("employee", "site", "shift", "status", "origin", "approved_by", "approved_at")
    list_filter = ("status", "origin", "shift", "site")
    search_fields = ("employee__code", "employee__name")


@admin.register(CoverageRequirement)
class CoverageRequirementAdmin(admin.ModelAdmin):
    list_display = ("site", "shift", "base", "event_uplift", "volume_uplift", "required_total", "computed_at")


@admin.register(CandidateEvaluation)
class CandidateEvaluationAdmin(admin.ModelAdmin):
    list_display = ("requirement", "employee", "eligible", "failed_rule", "score", "rank", "tag", "evaluated_at")
    list_filter = ("eligible", "failed_rule")


admin.site.register([CalendarEvent, EventImpact, Shift, AttendanceForecast, DemandRule])

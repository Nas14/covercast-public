from django.contrib import admin

from workforce.models import Absence, CategoryRule, Employee, EmployeeSkill, Skill, SourceEntity, Zone, ZoneDistance


class EmployeeSkillInline(admin.TabularInline):
    model = EmployeeSkill
    extra = 0


@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "category", "source_entity", "home_zone", "hours_before_window", "data_origin")
    list_filter = ("category", "home_zone", "data_origin")
    search_fields = ("code", "name")
    filter_horizontal = ("scope_zones",)
    inlines = [EmployeeSkillInline]


@admin.register(Skill)
class SkillAdmin(admin.ModelAdmin):
    list_display = ("code", "name_ar", "kind", "parent")
    list_filter = ("kind",)


@admin.register(Absence)
class AbsenceAdmin(admin.ModelAdmin):
    list_display = ("employee", "kind", "starts_at", "ends_at", "reported_at")
    list_filter = ("kind",)


admin.site.register([Zone, ZoneDistance, SourceEntity, CategoryRule])

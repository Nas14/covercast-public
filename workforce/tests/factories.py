"""Tiny helpers that build valid objects for tests.

Each helper gives sensible defaults and lets the test override only the
field it is about, so a test reads as "a valid X, except for Y".
"""

from datetime import datetime
from zoneinfo import ZoneInfo

from workforce.models import CategoryRule, Employee, EmployeeSkill, Skill, SourceEntity, Zone

RIYADH = ZoneInfo("Asia/Riyadh")


def at(y, m, d, h=0, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=RIYADH)


def zone(code="A"):
    return Zone.objects.get_or_create(code=code, defaults={"name": f"Zone {code}"})[0]


def category(name="contractor", **kw):
    defaults = {"max_weekly_hours": 48, "min_rest_hours": 11, "enforce_scope": name == "loan"}
    defaults.update(kw)
    return CategoryRule.objects.get_or_create(category=name, defaults=defaults)[0]


def entity(name="Test Entity"):
    return SourceEntity.objects.get_or_create(name=name)[0]


def skill(code="paramedic", kind=Skill.Kind.ROLE, parent=None):
    return Skill.objects.get_or_create(
        code=code, defaults={"name_ar": code, "name_en": code, "kind": kind, "parent": parent}
    )[0]


def employee(code="E900", cat="contractor", home="A", **kw):
    defaults = {
        "name": f"Test {code}",
        "category": category(cat),
        "source_entity": entity(),
        "home_zone": zone(home),
    }
    defaults.update(kw)
    return Employee.objects.create(code=code, **defaults)


def give_skill(emp, skill_obj, expires_on=None):
    return EmployeeSkill.objects.create(employee=emp, skill=skill_obj, expires_on=expires_on)

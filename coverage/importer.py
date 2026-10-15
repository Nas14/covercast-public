"""Load the demo data pack (CSV files) into the database.

Three steps, kept separate so each can be tested on its own:

1. read_pack(folder)   -> tables (lists of dict rows), checks files and columns
2. validate_pack(tables) -> list of problems, each with file and line number
3. load_pack(tables)   -> writes everything in ONE transaction, or nothing

The loader replaces the demo data wholesale, so running it twice gives the
same database (idempotent). It never invents values: every field comes from
the pack or from a documented default listed in DEFAULTS below.

Scenario overlays (P0-5): `load_folder(pack, scenario="riyadh_peak")` loads the
pack and then the SIMULATED overlay in data/demo/scenario_<name>/ (extra shifts,
their roster, the DemandRule table and a riyadh_scenario level file), all in
ONE transaction. The overlay is validated against the pack before anything is
written, and never changes a row of the pack itself.
"""

from __future__ import annotations

import csv
import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from django.conf import settings
from django.db import transaction

from coverage import forecast_import, policy
from coverage.codes import AssignmentOrigin, AssignmentStatus, DataOrigin, EventKind, ExclusionCode
from coverage.models import (
    Assignment,
    AttendanceForecast,
    CalendarEvent,
    CandidateEvaluation,
    CoverageRequirement,
    DemandRule,
    EventImpact,
    ForecastRun,
    Shift,
    Site,
    SiteRequirement,
)
from workforce.models import (
    Absence,
    CategoryRule,
    Employee,
    EmployeeSkill,
    Skill,
    SourceEntity,
    Zone,
    ZoneDistance,
)

# file -> columns that must be present (extra columns are allowed and ignored)
EXPECTED_COLUMNS: dict[str, list[str]] = {
    "zone.csv": ["code", "name"],
    "zone_travel_time.csv": ["from_zone", "to_zone", "minutes"],
    "skill.csv": ["code", "name_ar", "name_en", "kind", "parent_code"],
    "category_rule.csv": ["category", "max_weekly_hours", "min_rest_hours", "enforce_zone_scope", "note"],
    "employee.csv": ["employee_code", "name", "category", "source_entity", "department", "current_zone",
                     "hours_before_window", "loan_scope_zones", "is_active"],
    "employee_skill.csv": ["employee_code", "skill_code", "expires_on"],
    "critical_site.csv": ["site_code", "name", "zone", "min_staff_required", "site_type", "importance_1_5"],
    "calendar_event.csv": ["event_id", "kind", "title", "country", "starts_at", "ends_at", "source_ref"],
    "site_requirement.csv": ["site_code", "skill_code", "min_count", "event_id", "bundle", "note"],
    "shift.csv": ["date", "label", "starts_at", "ends_at"],
    "assignment.csv": ["employee_code", "site_code", "shift_date", "shift_label", "status"],
    "absence.csv": ["employee_code", "kind", "starts_at", "ends_at", "reported_at"],
    "reason_templates.csv": ["code", "kind", "template_ar", "template_en"],
    "gap_definitions.csv": ["gap_id", "site_code", "shift_date", "shift_label", "required_skills"],
}

# Models the loader owns, in an order where deleting top-down never hits PROTECT.
OWNED_MODELS = [
    CandidateEvaluation, CoverageRequirement, Assignment, EventImpact, SiteRequirement,
    AttendanceForecast, ForecastRun, DemandRule, Shift, CalendarEvent, Site, Absence, EmployeeSkill,
    Employee, SourceEntity, CategoryRule, ZoneDistance, Skill, Zone,
]


@dataclass(frozen=True)
class Problem:
    file: str
    line: int          # 1 = header; first data row is line 2
    message: str

    def __str__(self) -> str:
        return f"{self.file}:{self.line}: {self.message}"


class PackError(Exception):
    def __init__(self, problems: list[Problem]):
        self.problems = problems
        super().__init__("\n".join(str(p) for p in problems))


Tables = dict[str, list[dict[str, str]]]


# ------------------------------------------------------------------ 1. reading
def read_pack(folder: Path) -> Tables:
    problems: list[Problem] = []
    tables: Tables = {}
    for name, required in EXPECTED_COLUMNS.items():
        path = Path(folder) / name
        if not path.exists():
            problems.append(Problem(name, 0, "file is missing"))
            continue
        with open(path, encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            header = reader.fieldnames or []
            missing = [c for c in required if c not in header]
            if missing:
                problems.append(Problem(name, 1, f"missing column(s): {', '.join(missing)}"))
                continue
            tables[name] = [{k: (v or "").strip() for k, v in row.items()} for row in reader]
    if problems:
        raise PackError(problems)
    return tables


# --------------------------------------------------------------- 2. validating
def _rows(tables: Tables, name: str):
    for i, row in enumerate(tables[name], start=2):
        yield i, row


def validate_pack(tables: Tables) -> list[Problem]:
    """Every cross-file reference and every rule the database would reject,
    reported with its line instead of as a bare IntegrityError."""
    p: list[Problem] = []

    def need(cond: bool, file: str, line: int, msg: str) -> None:
        if not cond:
            p.append(Problem(file, line, msg))

    def parse(fn, value, file, line, field):
        try:
            return fn(value)
        except (TypeError, ValueError):
            p.append(Problem(file, line, f"{field}: cannot read {value!r}"))
            return None

    zones = {r["code"] for r in tables["zone.csv"]}
    skills = {r["code"] for r in tables["skill.csv"]}
    categories = {r["category"] for r in tables["category_rule.csv"]}
    employees = {r["employee_code"]: r for r in tables["employee.csv"]}
    sites = {r["site_code"] for r in tables["critical_site.csv"]}
    events = {r["event_id"] for r in tables["calendar_event.csv"]}
    shifts = {(r["date"], r["label"]) for r in tables["shift.csv"]}

    for name, key in [("zone.csv", "code"), ("skill.csv", "code"), ("employee.csv", "employee_code"),
                      ("critical_site.csv", "site_code"), ("calendar_event.csv", "event_id")]:
        seen: set[str] = set()
        for line, row in _rows(tables, name):
            need(row[key] not in seen, name, line, f"duplicate {key} {row[key]!r}")
            seen.add(row[key])

    for line, row in _rows(tables, "zone_travel_time.csv"):
        need(row["from_zone"] in zones and row["to_zone"] in zones, "zone_travel_time.csv", line, "unknown zone")
        parse(int, row["minutes"], "zone_travel_time.csv", line, "minutes")

    for line, row in _rows(tables, "skill.csv"):
        need(row["kind"] in Skill.Kind.values, "skill.csv", line, f"unknown kind {row['kind']!r}")
        need(not row["parent_code"] or row["parent_code"] in skills, "skill.csv", line,
             f"unknown parent {row['parent_code']!r}")
        need(row["parent_code"] != row["code"], "skill.csv", line, "skill cannot be its own parent")

    for line, row in _rows(tables, "category_rule.csv"):
        need(row["category"] in CategoryRule.Category.values, "category_rule.csv", line,
             f"unknown category {row['category']!r}")

    for line, row in _rows(tables, "employee.csv"):
        f = "employee.csv"
        need(row["category"] in categories, f, line, f"unknown category {row['category']!r}")
        need(row["current_zone"] in zones, f, line, f"unknown zone {row['current_zone']!r}")
        parse(int, row["hours_before_window"], f, line, "hours_before_window")
        scope = [z for z in row["loan_scope_zones"].split(";") if z]
        need(all(z in zones for z in scope), f, line, "unknown zone in loan_scope_zones")
        if row["category"] == "loan":
            need(bool(scope), f, line, "loan employee has no scope zones")
        else:
            need(not scope, f, line, "only loan employees may have scope zones")

    seen_es: set[tuple[str, str]] = set()
    for line, row in _rows(tables, "employee_skill.csv"):
        f = "employee_skill.csv"
        need(row["employee_code"] in employees, f, line, f"unknown employee {row['employee_code']!r}")
        need(row["skill_code"] in skills, f, line, f"unknown skill {row['skill_code']!r}")
        key = (row["employee_code"], row["skill_code"])
        need(key not in seen_es, f, line, f"duplicate skill row {key}")
        seen_es.add(key)
        if row["expires_on"]:
            parse(date.fromisoformat, row["expires_on"], f, line, "expires_on")

    for line, row in _rows(tables, "critical_site.csv"):
        f = "critical_site.csv"
        need(row["zone"] in zones, f, line, f"unknown zone {row['zone']!r}")
        imp = parse(int, row["importance_1_5"], f, line, "importance_1_5")
        need(imp is None or 1 <= imp <= 5, f, line, "importance must be 1-5")
        base = parse(int, row["min_staff_required"], f, line, "min_staff_required")
        need(base is None or base >= 1, f, line, "min_staff_required must be >= 1")

    for line, row in _rows(tables, "calendar_event.csv"):
        f = "calendar_event.csv"
        need(row["kind"] in EventKind.values, f, line, f"unknown kind {row['kind']!r}")
        s = parse(datetime.fromisoformat, row["starts_at"], f, line, "starts_at")
        e = parse(datetime.fromisoformat, row["ends_at"], f, line, "ends_at")
        need(s is None or e is None or e > s, f, line, "ends_at must be after starts_at")

    bundle_counts: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    seen_req: set[tuple[str, str, str]] = set()
    for line, row in _rows(tables, "site_requirement.csv"):
        f = "site_requirement.csv"
        need(row["site_code"] in sites, f, line, f"unknown site {row['site_code']!r}")
        need(row["skill_code"] in skills, f, line, f"unknown skill {row['skill_code']!r}")
        need(not row["event_id"] or row["event_id"] in events, f, line, f"unknown event {row['event_id']!r}")
        key = (row["site_code"], row["skill_code"], row["event_id"])
        need(key not in seen_req, f, line, f"duplicate requirement {key}")
        seen_req.add(key)
        if row["bundle"]:
            bundle_counts[(row["site_code"], row["event_id"], row["bundle"])].add(row["min_count"])
    for (site, ev, bundle), counts in bundle_counts.items():
        need(len(counts) == 1, "site_requirement.csv", 0,
             f"bundle {bundle} at {site} (event {ev}) has different min_count values {sorted(counts)}")

    for line, row in _rows(tables, "shift.csv"):
        need(row["label"] in ("morning", "evening"), "shift.csv", line, f"unknown label {row['label']!r}")

    active_seen: dict[tuple[str, str, str], int] = {}
    for line, row in _rows(tables, "assignment.csv"):
        f = "assignment.csv"
        need(row["employee_code"] in employees, f, line, f"unknown employee {row['employee_code']!r}")
        need(row["site_code"] in sites, f, line, f"unknown site {row['site_code']!r}")
        need((row["shift_date"], row["shift_label"]) in shifts, f, line, "unknown shift")
        need(row["status"] in AssignmentStatus.values, f, line, f"unknown status {row['status']!r}")
        if row["status"] in ("planned", "confirmed"):
            key = (row["employee_code"], row["shift_date"], row["shift_label"])
            if key in active_seen:
                p.append(Problem(f, line, f"{key[0]} already active in this shift (line {active_seen[key]})"))
            active_seen.setdefault(key, line)

    for line, row in _rows(tables, "absence.csv"):
        f = "absence.csv"
        need(row["employee_code"] in employees, f, line, f"unknown employee {row['employee_code']!r}")
        need(row["kind"] in Absence.Kind.values, f, line, f"unknown kind {row['kind']!r}")

    codes_in_file = {r["code"] for r in tables["reason_templates.csv"]}
    for code in ExclusionCode.values:
        need(code in codes_in_file, "reason_templates.csv", 0, f"no template for exclusion code {code}")

    for line, row in _rows(tables, "gap_definitions.csv"):
        f = "gap_definitions.csv"
        need(row["site_code"] in sites, f, line, f"unknown site {row['site_code']!r}")
        need((row["shift_date"], row["shift_label"]) in shifts, f, line, "unknown shift")
        need(all(s in skills for s in row["required_skills"].split(";") if s), f, line, "unknown skill")

    return p


# ------------------------------------------------------------------ 3. loading
def _bool(value: str) -> bool:
    return value.strip().lower() in ("true", "1", "yes")


def _origin(source_ref: str) -> str:
    return DataOrigin.OFFICIAL if source_ref.strip().lower() == "official" else DataOrigin.SIMULATED


@transaction.atomic
def load_pack(tables: Tables) -> dict[str, int]:
    """Replace all demo data with the pack. Atomic: on any error nothing changes."""
    for model in OWNED_MODELS:
        if model is Skill:
            # Skill.parent is PROTECT and points into the same table, so
            # Django refuses to delete a parent while its child still exists,
            # even when both are being deleted. Unlink first (errors log E-01).
            Skill.objects.update(parent=None)
        if model is Assignment:
            # Same pattern for Assignment.moved_from (Phase 6, ADR 0008): a moved
            # replacement points at the assignment it replaced (errors log E-12).
            Assignment.objects.update(moved_from=None, moved_from_status="")
        model.objects.all().delete()

    zones = {r["code"]: Zone.objects.create(code=r["code"], name=r["name"]) for r in tables["zone.csv"]}
    ZoneDistance.objects.bulk_create(
        ZoneDistance(from_zone=zones[r["from_zone"]], to_zone=zones[r["to_zone"]], minutes=int(r["minutes"]))
        for r in tables["zone_travel_time.csv"]
    )

    skills = {
        r["code"]: Skill.objects.create(code=r["code"], name_ar=r["name_ar"], name_en=r["name_en"], kind=r["kind"])
        for r in tables["skill.csv"]
    }
    for r in tables["skill.csv"]:  # second pass: parents exist now
        if r["parent_code"]:
            skills[r["code"]].parent = skills[r["parent_code"]]
            skills[r["code"]].save(update_fields=["parent"])

    categories = {
        r["category"]: CategoryRule.objects.create(
            category=r["category"], max_weekly_hours=int(r["max_weekly_hours"]),
            min_rest_hours=int(r["min_rest_hours"]), enforce_scope=_bool(r["enforce_zone_scope"]), note=r["note"],
        )
        for r in tables["category_rule.csv"]
    }

    entities: dict[str, SourceEntity] = {}
    employees: dict[str, Employee] = {}
    for r in tables["employee.csv"]:
        ent = entities.get(r["source_entity"]) or SourceEntity.objects.create(name=r["source_entity"])
        entities[r["source_entity"]] = ent
        emp = Employee.objects.create(
            code=r["employee_code"], name=r["name"], category=categories[r["category"]], source_entity=ent,
            department=r["department"], home_zone=zones[r["current_zone"]],
            hours_before_window=int(r["hours_before_window"]), is_active=_bool(r["is_active"]),
            data_origin=DataOrigin.SIMULATED,
        )
        scope = [zones[z] for z in r["loan_scope_zones"].split(";") if z]
        if scope:
            emp.scope_zones.set(scope)
        employees[r["employee_code"]] = emp

    EmployeeSkill.objects.bulk_create(
        EmployeeSkill(
            employee=employees[r["employee_code"]], skill=skills[r["skill_code"]],
            expires_on=date.fromisoformat(r["expires_on"]) if r["expires_on"] else None,
        )
        for r in tables["employee_skill.csv"]
    )

    sites = {
        r["site_code"]: Site.objects.create(
            code=r["site_code"], name=r["name"], zone=zones[r["zone"]], site_type=r["site_type"],
            base_staff=int(r["min_staff_required"]), importance=int(r["importance_1_5"]),
        )
        for r in tables["critical_site.csv"]
    }

    events = {
        r["event_id"]: CalendarEvent.objects.create(
            code=r["event_id"], kind=r["kind"], title=r["title"], country=r["country"],
            starts_at=datetime.fromisoformat(r["starts_at"]), ends_at=datetime.fromisoformat(r["ends_at"]),
            data_origin=_origin(r["source_ref"]),
        )
        for r in tables["calendar_event.csv"]
    }

    SiteRequirement.objects.bulk_create(
        SiteRequirement(
            site=sites[r["site_code"]], skill=skills[r["skill_code"]], min_count=int(r["min_count"]),
            event=events[r["event_id"]] if r["event_id"] else None, bundle=r["bundle"], note=r["note"],
        )
        for r in tables["site_requirement.csv"]
    )

    shifts = {
        (r["date"], r["label"]): Shift.objects.create(
            date=date.fromisoformat(r["date"]), slot=r["label"],
            starts_at=datetime.fromisoformat(r["starts_at"]), ends_at=datetime.fromisoformat(r["ends_at"]),
        )
        for r in tables["shift.csv"]
    }

    Assignment.objects.bulk_create(
        Assignment(
            employee=employees[r["employee_code"]], site=sites[r["site_code"]],
            shift=shifts[(r["shift_date"], r["shift_label"])], status=r["status"],
            origin=AssignmentOrigin.SCHEDULE,
        )
        for r in tables["assignment.csv"]
    )

    Absence.objects.bulk_create(
        Absence(
            employee=employees[r["employee_code"]], kind=r["kind"],
            starts_at=datetime.fromisoformat(r["starts_at"]), ends_at=datetime.fromisoformat(r["ends_at"]),
            reported_at=datetime.fromisoformat(r["reported_at"]),
        )
        for r in tables["absence.csv"]
    )

    return {model.__name__: model.objects.count() for model in reversed(OWNED_MODELS)}


def load_folder(folder: Path, scenario: str | None = None) -> dict[str, int]:
    tables = read_pack(folder)
    problems = validate_pack(tables)
    if problems:
        raise PackError(problems)
    if scenario is None:
        return load_pack(tables)
    overlay = read_scenario(scenario)
    problems = validate_scenario(overlay, tables)
    if problems:
        raise PackError(problems)
    with transaction.atomic():
        load_pack(tables)
        load_scenario(overlay)
        return {model.__name__: model.objects.count() for model in reversed(OWNED_MODELS)}


# ------------------------------------------------------- scenario overlays (P0-5)
SCENARIO_ROOT = Path(settings.BASE_DIR) / "data" / "demo"
SCENARIOS = ("riyadh_peak",)
SCENARIO_COLUMNS: dict[str, list[str]] = {
    "shift.csv": EXPECTED_COLUMNS["shift.csv"],
    "assignment.csv": EXPECTED_COLUMNS["assignment.csv"],
    "demand_rule.csv": ["site_type", "category", "level", "extra_staff", "source_note"],
}


@dataclass(frozen=True)
class Scenario:
    name: str
    tables: Tables
    forecast_raw: bytes


def read_scenario(name: str, root: Path | None = None) -> Scenario:
    if name not in SCENARIOS:
        raise PackError([Problem("scenario", 0, f"unknown scenario {name!r}; known: {', '.join(SCENARIOS)}")])
    folder = (root or SCENARIO_ROOT) / f"scenario_{name}"
    problems: list[Problem] = []
    tables: Tables = {}
    for file, required in SCENARIO_COLUMNS.items():
        path = folder / file
        if not path.exists():
            problems.append(Problem(file, 0, "file is missing"))
            continue
        with open(path, encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            missing = [c for c in required if c not in (reader.fieldnames or [])]
            if missing:
                problems.append(Problem(file, 1, f"missing column(s): {', '.join(missing)}"))
                continue
            tables[file] = [{k: (v or "").strip() for k, v in row.items()} for row in reader]
    forecast = folder / "forecast.json"
    if not forecast.exists():
        problems.append(Problem("forecast.json", 0, "file is missing"))
    if problems:
        raise PackError(problems)
    return Scenario(name, tables, forecast.read_bytes())


def validate_scenario(sc: Scenario, pack: Tables) -> list[Problem]:
    """The overlay against the pack: new shifts only, known people and sites,
    one active assignment per person and shift, and a DemandRule table that maps
    EVERY site type of the pack, at every level, to its approved category."""
    p: list[Problem] = []

    def need(cond: bool, file: str, line: int, msg: str) -> None:
        if not cond:
            p.append(Problem(file, line, msg))

    employees = {r["employee_code"] for r in pack["employee.csv"]}
    sites = {r["site_code"] for r in pack["critical_site.csv"]}
    site_types = {r["site_type"] for r in pack["critical_site.csv"]}
    pack_shifts = {(r["date"], r["label"]) for r in pack["shift.csv"]}
    shifts = set()
    for line, row in _rows(sc.tables, "shift.csv"):
        key = (row["date"], row["label"])
        need(row["label"] in ("morning", "evening"), "shift.csv", line, f"unknown label {row['label']!r}")
        need(key not in pack_shifts, "shift.csv", line, f"shift {key} already exists in the pack")
        need(key not in shifts, "shift.csv", line, f"duplicate shift {key}")
        shifts.add(key)
        try:
            s, e = datetime.fromisoformat(row["starts_at"]), datetime.fromisoformat(row["ends_at"])
            need(e > s, "shift.csv", line, "ends_at must be after starts_at")
        except ValueError:
            p.append(Problem("shift.csv", line, "starts_at/ends_at: cannot read"))

    active: dict[tuple[str, str, str], int] = {}
    for line, row in _rows(sc.tables, "assignment.csv"):
        f = "assignment.csv"
        need(row["employee_code"] in employees, f, line, f"unknown employee {row['employee_code']!r}")
        need(row["site_code"] in sites, f, line, f"unknown site {row['site_code']!r}")
        need((row["shift_date"], row["shift_label"]) in shifts, f, line, "assignment outside the scenario's shifts")
        need(row["status"] in ("planned", "confirmed"), f, line, "status must be planned or confirmed")
        key = (row["employee_code"], row["shift_date"], row["shift_label"])
        if key in active:
            p.append(Problem(f, line, f"{key[0]} already active in this shift (line {active[key]})"))
        active.setdefault(key, line)

    f = "demand_rule.csv"
    seen: set[tuple[str, str]] = set()
    for line, row in _rows(sc.tables, f):
        t, lvl = row["site_type"], row["level"]
        need(t in policy.VOLUME_CATEGORY_BY_SITE_TYPE, f, line, f"site type {t!r} has no approved category")
        need(t in site_types, f, line, f"site type {t!r} does not exist in the pack")
        need(row["category"] == policy.VOLUME_CATEGORY_BY_SITE_TYPE.get(t), f, line,
             f"category {row['category']!r} differs from the approved mapping for {t!r}")
        need(lvl in ("normal", "high", "peak"), f, line, f"unknown level {lvl!r}")
        need(row["extra_staff"].isdigit(), f, line, "extra_staff must be a whole number >= 0")
        need(bool(row["source_note"]), f, line, "source_note is required (say it is a simulated assumption)")
        need((t, lvl) not in seen, f, line, f"duplicate rule {(t, lvl)}")
        seen.add((t, lvl))
    for t in sorted(site_types):
        for lvl in ("normal", "high", "peak"):
            need((t, lvl) in seen, f, 0, f"no rule for site type {t!r} at level {lvl!r}")

    try:
        doc = forecast_import.parse(sc.forecast_raw)
        problems = forecast_import.validate(doc, None)
        if isinstance(doc, dict) and isinstance(doc.get("run"), dict) and doc["run"].get("mode") != "riyadh_scenario":
            problems.append("a scenario pack may only carry a riyadh_scenario level file")
        dates = {d for d, _ in shifts}
        for d in doc.get("days", []) if isinstance(doc, dict) else []:
            if isinstance(d, dict) and d.get("date") not in dates:
                problems.append(f"level for {d.get('date')} but the scenario has no shift that day")
    except forecast_import.ForecastImportError as exc:
        problems = exc.problems
    p += [Problem("forecast.json", 0, msg) for msg in problems]
    return p


def load_scenario(sc: Scenario) -> None:
    """Write the overlay (call inside the same transaction as load_pack)."""
    shifts = {
        (r["date"], r["label"]): Shift.objects.create(
            date=date.fromisoformat(r["date"]), slot=r["label"],
            starts_at=datetime.fromisoformat(r["starts_at"]), ends_at=datetime.fromisoformat(r["ends_at"]),
        )
        for r in sc.tables["shift.csv"]
    }
    employees = {e.code: e for e in Employee.objects.all()}
    sites = {s.code: s for s in Site.objects.all()}
    Assignment.objects.bulk_create(
        Assignment(employee=employees[r["employee_code"]], site=sites[r["site_code"]],
                   shift=shifts[(r["shift_date"], r["shift_label"])], status=r["status"],
                   origin=AssignmentOrigin.SCHEDULE)
        for r in sc.tables["assignment.csv"]
    )
    DemandRule.objects.bulk_create(
        DemandRule(site_type=r["site_type"], level=r["level"], extra_staff=int(r["extra_staff"]),
                   source_note=f"{r['category']}: {r['source_note']}"[:200])
        for r in sc.tables["demand_rule.csv"]
    )
    forecast_import.import_forecast(sc.forecast_raw, frozen=None)


def database_is_empty() -> bool:
    return not any(model.objects.exists() for model in OWNED_MODELS)


def fingerprint() -> str:
    """Hash of the loaded content, independent of auto-generated ids.
    Two loads of the same pack must give the same fingerprint."""
    parts = {
        "employees": sorted(
            (e.code, e.name, e.category_id, e.source_entity.name, e.home_zone.code, e.hours_before_window,
             tuple(sorted(z.code for z in e.scope_zones.all())))
            for e in Employee.objects.select_related("source_entity", "home_zone").prefetch_related("scope_zones")
        ),
        "skills": sorted(
            (s.employee.code, s.skill.code, str(s.expires_on))
            for s in EmployeeSkill.objects.select_related("employee", "skill")
        ),
        "sites": sorted((s.code, s.zone.code, s.base_staff, s.importance, s.site_type)
                        for s in Site.objects.select_related("zone")),
        "requirements": sorted(
            (r.site.code, r.skill.code, r.min_count, r.event.code if r.event else "", r.bundle)
            for r in SiteRequirement.objects.select_related("site", "skill", "event")
        ),
        "assignments": sorted(
            (a.employee.code, a.site.code, str(a.shift.date), a.shift.slot, a.status, a.origin)
            for a in Assignment.objects.select_related("employee", "site", "shift")
        ),
        "absences": sorted((a.employee.code, a.kind, a.starts_at.isoformat()) for a in Absence.objects.select_related("employee")),
        "events": sorted((e.code, e.kind, e.starts_at.isoformat(), e.ends_at.isoformat()) for e in CalendarEvent.objects.all()),
        "shifts": sorted((str(s.date), s.slot) for s in Shift.objects.all()),
        "demand_rules": sorted((r.site_type, r.level, r.extra_staff) for r in DemandRule.objects.all()),
        "forecast_runs": sorted((r.code, r.source_sha256, r.is_active) for r in ForecastRun.objects.all()),
    }
    return hashlib.sha256(json.dumps(parts, default=str, ensure_ascii=False).encode()).hexdigest()

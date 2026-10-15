#!/usr/bin/env python3
"""Build the CoverCast demo data pack v0.2 from Raghad's v0.1 pack.

v0.1 is kept byte-for-byte as delivered (data/demo/v0.1). This script applies
the fixes agreed in the 13 October review, one function per fix, and writes
data/demo/v0.2. It is deterministic: running it twice gives identical files.

    python data/build_v02.py            # writes data/demo/v0.2
    python data/build_v02.py --check    # fails if v0.2 on disk is out of date

Standard library only, so it runs without the Django environment.
"""

from __future__ import annotations

import argparse
import csv
import io
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE / "demo" / "v0.1"
DST = HERE / "demo" / "v0.2"

# R7: move the window from 11-13 Oct 2026 (Sun-Tue) into Expo 2030 Riyadh
# (1 Oct 2030 - 31 Mar 2031). 1463 days = 209 weeks exactly, so every date
# keeps its weekday: 11 Oct 2026 (Sun) -> 13 Oct 2030 (Sun).
DATE_SHIFT = timedelta(days=1463)
SHIFT_HOURS = 8

# R6: site criticality and type. Not in v0.1; values are documented
# assumptions (docs/12-assumptions.md, A-06 and A-07).
SITE_ATTRS = {
    "S01": ("medical", 5), "S02": ("medical", 5), "S03": ("medical", 5), "S12": ("medical", 5),
    "S04": ("security_gate", 4), "S05": ("security_gate", 4), "S06": ("security_gate", 4),
    "S07": ("operations", 3), "S08": ("visitor_service", 3), "S09": ("visitor_service", 3),
    "S10": ("crowd", 3), "S11": ("safety", 3),
}

# Skill hierarchy: holding the child skill satisfies the parent requirement.
SKILL_PARENT = {"adv_first_aid": "basic_first_aid"}

# R1 + R4: the Japan national day line at S03 becomes a bundle: one person who
# is a paramedic, holds advanced first aid AND speaks Japanese.
S03_BUNDLE = ("B1", 1, ["paramedic", "adv_first_aid", "ja"])

MISSING_SKILL_TEMPLATE = [
    "MISSING_SKILL", "exclusion",
    "لا يحمل المهارة المطلوبة: {skill}",
    "Does not hold the required skill: {skill}",
]


# --------------------------------------------------------------------------- io
def read(name: str) -> tuple[list[str], list[dict[str, str]]]:
    with open(SRC / name, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        return list(reader.fieldnames or []), list(reader)


def render(header: list[str], rows: list[dict[str, str]]) -> bytes:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=header, lineterminator="\r\n")
    w.writeheader()
    w.writerows(rows)
    return ("﻿" + buf.getvalue()).encode("utf-8")


# ----------------------------------------------------------------- date helpers
def shift_date(value: str) -> str:
    return (date.fromisoformat(value) + DATE_SHIFT).isoformat() if value else value


def shift_dt(value: str) -> str:
    return (datetime.fromisoformat(value) + DATE_SHIFT).isoformat(timespec="minutes") if value else value


# ----------------------------------------------------------------------- fixes
def fix_dates(tables: dict) -> None:
    """R7: every date and datetime column moves by DATE_SHIFT."""
    columns = {
        "shift.csv": {"date": shift_date, "starts_at": shift_dt, "ends_at": shift_dt},
        "calendar_event.csv": {"starts_at": shift_dt, "ends_at": shift_dt},
        "assignment.csv": {"shift_date": shift_date},
        "absence.csv": {"starts_at": shift_dt, "ends_at": shift_dt, "reported_at": shift_dt},
        "employee_skill.csv": {"expires_on": shift_date},
        "gap_definitions.csv": {"shift_date": shift_date, "detected_at": shift_dt, "deadline_at": shift_dt},
    }
    for name, cols in columns.items():
        for row in tables[name][1]:
            for col, fn in cols.items():
                row[col] = fn(row[col])


def fix_skill_parent(tables: dict) -> None:
    header, rows = tables["skill.csv"]
    header.append("parent_code")
    for row in rows:
        row["parent_code"] = SKILL_PARENT.get(row["code"], "")


def fix_site_attrs(tables: dict) -> None:
    """R6: importance (1-5) and site_type for every critical site."""
    header, rows = tables["critical_site.csv"]
    header += ["site_type", "importance_1_5"]
    for row in rows:
        site_type, importance = SITE_ATTRS[row["site_code"]]
        row["site_type"], row["importance_1_5"] = site_type, str(importance)


def fix_bundle(tables: dict) -> None:
    """R1 + R4: replace the single 'ja' event line at S03 with a same-person bundle."""
    header, rows = tables["site_requirement.csv"]
    header.insert(header.index("event_id") + 1, "bundle")
    out = []
    for row in rows:
        row["bundle"] = ""
        if row["site_code"] == "S03" and row["event_id"] == "1":
            bundle_id, count, skills = S03_BUNDLE
            for skill in skills:
                out.append({
                    "site_code": "S03", "skill_code": skill, "min_count": str(count),
                    "event_id": "1", "bundle": bundle_id,
                    "note": "مضاف بحدث: اليوم الوطني لليابان - مسعف يتحدث اليابانية (نفس الشخص)",
                })
            continue
        out.append(row)
    tables["site_requirement.csv"] = (header, out)


# Arabic counts agree with the number (دقيقة واحدة، دقيقتان، 3-10 دقائق، 11+ دقيقة),
# so the Arabic texts take a ready-made phrase ({minutes_ar}, {rest_ar}) built by
# coverage/services/reasons.py. English keeps the plain number. (Approved by Nouf, E-13.)
ARABIC_COUNT_FIXES = {
    "NEAR": "الأقرب: على بعد {minutes_ar} من الموقع",
    "REST_GAP": "فاصل الراحة أقل من {rest_ar}",
}
# Neutral distance line for everyone who is not the nearest (E-13): before,
# "the nearest" was shown to every candidate, e.g. 60 minutes away.
TRAVEL_TEMPLATE = ("TRAVEL", "neutral", "على بعد {minutes_ar} من الموقع", "{minutes} min away")


def fix_reason_templates(tables: dict) -> None:
    header, rows = tables["reason_templates.csv"]
    for row in rows:
        if row["code"] in ARABIC_COUNT_FIXES:
            row["template_ar"] = ARABIC_COUNT_FIXES[row["code"]]
    rows.append(dict(zip(header, MISSING_SKILL_TEMPLATE)))
    rows.append(dict(zip(header, TRAVEL_TEMPLATE)))


def fix_gap_definitions(tables: dict) -> None:
    """R2: Arabic is spoken by all 80 people, so it is not a requirement of G1."""
    for row in tables["gap_definitions.csv"][1]:
        skills = [s for s in row["required_skills"].split(";") if s != "ar"]
        row["required_skills"] = ";".join(skills)


def add_hours_before_window(tables: dict) -> None:
    """hours_worked_this_week in v0.1 already includes the shifts inside the
    window. The engine recomputes hours from live assignments, so it needs the
    part that does NOT come from those assignments."""
    counts: dict[str, int] = {}
    for row in tables["assignment.csv"][1]:
        counts[row["employee_code"]] = counts.get(row["employee_code"], 0) + 1
    header, rows = tables["employee.csv"]
    header.insert(header.index("hours_worked_this_week") + 1, "hours_before_window")
    for row in rows:
        before = int(row["hours_worked_this_week"]) - SHIFT_HOURS * counts.get(row["employee_code"], 0)
        if before < 0:
            raise ValueError(f"{row['employee_code']}: negative hours before window")
        row["hours_before_window"] = str(before)


FIXES = [fix_dates, fix_skill_parent, fix_site_attrs, fix_bundle,
         fix_reason_templates, fix_gap_definitions, add_hours_before_window]

# Files carried into v0.2. Left out on purpose:
#   rule_config.csv  -> weights and margins live in coverage/policy.py
#   demo_cases.csv   -> v0.1 expectations used the old ranking; Phase 5 writes
#                       new hand-derived expectations (expected_g1.csv)
CARRIED = [
    "zone.csv", "zone_travel_time.csv", "skill.csv", "employee.csv", "employee_skill.csv",
    "critical_site.csv", "site_requirement.csv", "calendar_event.csv", "shift.csv",
    "assignment.csv", "absence.csv", "category_rule.csv", "reason_templates.csv",
    "gap_definitions.csv",
]


def build() -> dict[str, bytes]:
    tables = {name: read(name) for name in CARRIED}
    for fix in FIXES:
        fix(tables)
    return {name: render(*tables[name]) for name in CARRIED}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="verify v0.2 on disk is current")
    args = parser.parse_args()
    files = build()
    if args.check:
        stale = [n for n, b in files.items() if not (DST / n).exists() or (DST / n).read_bytes() != b]
        if stale:
            print("v0.2 is out of date:", ", ".join(stale))
            return 1
        print(f"v0.2 is current ({len(files)} files)")
        return 0
    DST.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (DST / name).write_bytes(content)
    print(f"wrote {len(files)} files to {DST.relative_to(HERE.parent)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

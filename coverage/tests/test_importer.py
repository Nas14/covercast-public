"""load_demo: correct content, idempotent, all-or-nothing, and clear errors."""

import csv
import io
import shutil
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from coverage import importer
from coverage.models import Assignment, Site, SiteRequirement
from workforce.models import Employee, EmployeeSkill, Skill

PACK = Path(settings.COVERCAST_DEMO_DATA_DIR)
REPO = Path(settings.BASE_DIR)


def csv_rows(name: str) -> int:
    with open(PACK / name, encoding="utf-8-sig", newline="") as fh:
        return sum(1 for _ in csv.DictReader(fh))


class PackCopy:
    """A temporary copy of the pack that a test can damage."""

    def __enter__(self) -> Path:
        self._dir = tempfile.TemporaryDirectory()
        path = Path(self._dir.name) / "pack"
        shutil.copytree(PACK, path)
        return path

    def __exit__(self, *exc):
        self._dir.cleanup()


def edit_csv(path: Path, name: str, change) -> None:
    file = path / name
    with open(file, encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        header, rows = list(reader.fieldnames), list(reader)
    header, rows = change(header, rows)
    with open(file, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=header)
        w.writeheader()
        w.writerows(rows)


class LoadContentTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        importer.load_folder(PACK)

    def test_row_counts_match_the_files(self):
        self.assertEqual(Employee.objects.count(), csv_rows("employee.csv"))
        self.assertEqual(EmployeeSkill.objects.count(), csv_rows("employee_skill.csv"))
        self.assertEqual(Assignment.objects.count(), csv_rows("assignment.csv"))
        self.assertEqual(SiteRequirement.objects.count(), csv_rows("site_requirement.csv"))
        self.assertEqual(Site.objects.count(), 12)

    def test_reem_certificate_expires_the_day_before_the_event(self):
        reem = EmployeeSkill.objects.get(employee__code="E060", skill__code="adv_first_aid")
        self.assertEqual(reem.expires_on, date(2030, 10, 13))

    def test_noura_scope_and_hours(self):
        noura = Employee.objects.get(code="E061")
        self.assertEqual(sorted(z.code for z in noura.scope_zones.all()), ["C", "D", "E"])
        self.assertEqual(noura.hours_before_window, 8)
        self.assertEqual(noura.category_id, "loan")

    def test_only_loan_employees_have_scope(self):
        self.assertFalse(Employee.objects.exclude(category_id="loan").filter(scope_zones__isnull=False).exists())
        self.assertFalse(Employee.objects.filter(category_id="loan", scope_zones__isnull=True).exists())

    def test_s03_event_bundle_is_three_skills_one_person(self):
        rows = SiteRequirement.objects.filter(site__code="S03", event__code="1")
        self.assertEqual(sorted(r.skill.code for r in rows), ["adv_first_aid", "ja", "paramedic"])
        self.assertEqual({r.bundle for r in rows}, {"B1"})
        self.assertEqual({r.min_count for r in rows}, {1})

    def test_skill_hierarchy_loaded(self):
        self.assertEqual(Skill.objects.get(code="adv_first_aid").parent.code, "basic_first_aid")

    def test_everything_is_marked_simulated(self):
        self.assertFalse(Employee.objects.exclude(data_origin="simulated").exists())

    def test_demo_window_is_in_expo_2030(self):
        dates = sorted(set(Assignment.objects.values_list("shift__date", flat=True)))
        self.assertEqual(dates, [date(2030, 10, 13), date(2030, 10, 14), date(2030, 10, 15)])


class IdempotencyTests(TestCase):
    def test_loading_twice_gives_the_same_database(self):
        first_counts = importer.load_folder(PACK)
        first = importer.fingerprint()
        second_counts = importer.load_folder(PACK)
        self.assertEqual(first_counts, second_counts)
        self.assertEqual(first, importer.fingerprint())

    def test_reload_with_skill_hierarchy_present(self):
        # Regression for E-01: a reset failed on Skill.parent (PROTECT, self-reference).
        importer.load_folder(PACK)
        importer.load_folder(PACK)
        self.assertEqual(Skill.objects.filter(parent__isnull=False).count(), 1)

    def test_reload_after_a_moving_approval(self):
        # Regression for E-12: Assignment.moved_from is PROTECT and points into the
        # same table (Phase 6), so a reset after approving Noura's move failed.
        from datetime import date

        from django.contrib.auth import get_user_model

        from coverage.models import Assignment, Shift, Site
        from coverage.services.approval import approve
        importer.load_folder(PACK)
        approve(Site.objects.get(code="S03"), Shift.objects.get(date=date(2030, 10, 14), slot="morning"), "E061",
                idempotency_key="e12", supervisor=get_user_model().objects.create_user("e12"))
        self.assertTrue(Assignment.objects.filter(moved_from__isnull=False).exists())
        first = importer.fingerprint()
        importer.load_folder(PACK)
        self.assertFalse(Assignment.objects.filter(origin="replacement").exists())
        self.assertNotEqual(first, importer.fingerprint())   # really replaced, not left as it was

    def test_command_refuses_to_overwrite_without_reset(self):
        # Output goes to an in-memory buffer, not "/dev/null": that path does
        # not exist on Windows (errors log E-05).
        out = io.StringIO()
        call_command("load_demo", stdout=out)
        self.assertIn("Loaded simulated demo data", out.getvalue())
        with self.assertRaises(CommandError) as ctx:
            call_command("load_demo", stdout=io.StringIO())
        self.assertIn("--reset", str(ctx.exception))
        call_command("load_demo", "--reset", stdout=io.StringIO())
        self.assertEqual(Employee.objects.count(), 80)


class RejectionTests(TestCase):
    """A broken pack is rejected with file:line, and the database keeps its previous content."""

    def setUp(self):
        importer.load_folder(PACK)
        self.before = importer.fingerprint()

    def assert_rejected(self, pack: Path, expected_fragment: str):
        with self.assertRaises(importer.PackError) as ctx:
            importer.load_folder(pack)
        messages = [str(p) for p in ctx.exception.problems]
        self.assertTrue(any(expected_fragment in m for m in messages), messages)
        self.assertEqual(self.before, importer.fingerprint(), "database changed after a rejected load")

    def test_missing_column(self):
        with PackCopy() as pack:
            edit_csv(pack, "employee.csv", lambda h, r: ([c for c in h if c != "hours_before_window"],
                                                         [{k: v for k, v in x.items() if k != "hours_before_window"} for x in r]))
            self.assert_rejected(pack, "employee.csv:1: missing column(s): hours_before_window")

    def test_missing_file(self):
        with PackCopy() as pack:
            (pack / "shift.csv").unlink()
            self.assert_rejected(pack, "shift.csv:0: file is missing")

    def test_assignment_to_unknown_employee(self):
        def change(h, rows):
            rows[0]["employee_code"] = "E999"
            return h, rows
        with PackCopy() as pack:
            edit_csv(pack, "assignment.csv", change)
            self.assert_rejected(pack, "assignment.csv:2: unknown employee 'E999'")

    def test_double_booking_in_the_file(self):
        def change(h, rows):
            dup = dict(rows[0])
            dup["site_code"] = "S11" if rows[0]["site_code"] != "S11" else "S10"
            return h, rows + [dup]
        with PackCopy() as pack:
            edit_csv(pack, "assignment.csv", change)
            self.assert_rejected(pack, "already active in this shift")

    def test_contractor_with_scope(self):
        def change(h, rows):
            for r in rows:
                if r["category"] == "contractor":
                    r["loan_scope_zones"] = "A"
                    break
            return h, rows
        with PackCopy() as pack:
            edit_csv(pack, "employee.csv", change)
            self.assert_rejected(pack, "only loan employees may have scope zones")

    def test_bundle_with_different_counts(self):
        def change(h, rows):
            for r in rows:
                if r["bundle"] == "B1" and r["skill_code"] == "ja":
                    r["min_count"] = "2"
            return h, rows
        with PackCopy() as pack:
            edit_csv(pack, "site_requirement.csv", change)
            self.assert_rejected(pack, "bundle B1 at S03")

    def test_unreadable_date(self):
        def change(h, rows):
            for r in rows:
                if r["expires_on"]:
                    r["expires_on"] = "13/10/2030"
                    break
            return h, rows
        with PackCopy() as pack:
            edit_csv(pack, "employee_skill.csv", change)
            self.assert_rejected(pack, "expires_on: cannot read '13/10/2030'")

    def test_error_during_writing_rolls_everything_back(self):
        # Skip validation on purpose to force a failure half-way through writing.
        tables = importer.read_pack(PACK)
        tables["assignment.csv"][-1]["shift_label"] = "night"
        with self.assertRaises(KeyError):
            importer.load_pack(tables)
        self.assertEqual(self.before, importer.fingerprint())


class ProvenanceTests(TestCase):
    """The chain v0.1 (Raghad's generator) -> v0.2 (our transform) is reproducible."""

    def test_v02_matches_its_transform(self):
        result = subprocess.run([sys.executable, str(REPO / "data" / "build_v02.py"), "--check"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_v01_matches_raghads_generator(self):
        v01 = REPO / "data" / "demo" / "v0.1"
        with tempfile.TemporaryDirectory() as tmp:
            shutil.copy(v01 / "generate_covercast_data.py", tmp)
            subprocess.run([sys.executable, "-I", "generate_covercast_data.py"], cwd=tmp,
                           check=True, capture_output=True)
            out = Path(tmp) / "output"
            for produced in sorted(out.glob("*.csv")):
                self.assertEqual(produced.read_bytes(), (v01 / produced.name).read_bytes(), produced.name)

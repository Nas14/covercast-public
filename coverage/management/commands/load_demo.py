"""python manage.py load_demo [--reset] [--path FOLDER] [--scenario riyadh_peak]"""

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from coverage import importer


class Command(BaseCommand):
    help = "Load the simulated demo data pack (replaces all CoverCast data)."

    def add_arguments(self, parser):
        parser.add_argument("--path", type=Path, default=settings.COVERCAST_DEMO_DATA_DIR,
                            help="Folder with the CSV pack (default: data/demo/v0.2).")
        parser.add_argument("--reset", action="store_true",
                            help="Required when the database already holds data: it will be replaced.")
        parser.add_argument("--scenario", choices=importer.SCENARIOS,
                            help="Also load a SIMULATED scenario overlay (data/demo/scenario_<name>/), same transaction.")

    def handle(self, *args, path: Path, reset: bool, scenario: str | None = None, **options):
        if not importer.database_is_empty() and not reset:
            raise CommandError(
                "The database already has CoverCast data (it may include approvals made during a demo). "
                "Run again with --reset to replace it."
            )
        try:
            counts = importer.load_folder(path, scenario=scenario)
        except importer.PackError as exc:
            lines = [str(p) for p in exc.problems]
            shown = "\n".join(lines[:30])
            more = f"\n... and {len(lines) - 30} more" if len(lines) > 30 else ""
            raise CommandError(f"Data pack rejected, nothing was changed:\n{shown}{more}") from exc

        for name, count in counts.items():
            self.stdout.write(f"  {name:<22} {count}")
        self.stdout.write(f"  fingerprint            {importer.fingerprint()[:16]}")
        self.stdout.write(self.style.SUCCESS(f"Loaded simulated demo data from {path}"))
        if scenario:
            self.stdout.write(self.style.SUCCESS(f"Loaded SIMULATED scenario overlay: {scenario}"))

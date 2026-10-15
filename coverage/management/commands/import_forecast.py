"""python manage.py import_forecast FILE [--replace]

FILE is a covercast-forecast-v1 JSON file:
  forecast/out/export/osaka_replay.json              (official, Osaka 2025 replay)
  data/demo/scenario_riyadh_peak/forecast.json       (simulated Riyadh scenario;
                                                       load_demo --scenario imports it)
"""

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from coverage.forecast_import import ForecastImportError, import_path


class Command(BaseCommand):
    help = "Import a forecast file as the active run of its mode (validated, atomic, idempotent)."

    def add_arguments(self, parser):
        parser.add_argument("file", type=Path)
        parser.add_argument("--replace", action="store_true",
                            help="Replace an existing run with the same code but different content.")

    def handle(self, *args, file: Path, replace: bool, **options):
        if not file.exists():
            raise CommandError(f"{file} not found")
        try:
            result = import_path(file, replace=replace)
        except ForecastImportError as exc:
            shown = "\n".join(f"  - {p}" for p in exc.problems[:30])
            more = f"\n  ... and {len(exc.problems) - 30} more" if len(exc.problems) > 30 else ""
            raise CommandError(f"Forecast file rejected, nothing was changed:\n{shown}{more}") from exc
        run = result.run
        if not result.created:
            self.stdout.write(f"Already imported: {run.code} ({result.days} days); nothing changed.")
            return
        for code in result.deactivated:
            self.stdout.write(f"  deactivated previous run {code}")
        self.stdout.write(self.style.SUCCESS(
            f"Imported {run.code}: {result.days} days, mode {run.mode}, {run.data_origin}, active"))

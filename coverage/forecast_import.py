"""Import one forecast file (covercast-forecast-v1) as a ForecastRun. (P0-3)

Two modes, never mixed (docs/18 section 3.2):

  osaka_replay     official Expo 2025 data, the frozen protocol-selected model,
                   produced by forecast/export_forecast.py. Every day carries
                   p10/p50/p90, the official actual, as_of, horizon_days and the
                   level thresholds of its origin. Dates: Expo 2025 only.
  riyadh_scenario  simulated. A day carries a date and a level ONLY: no visitor
                   numbers, no actuals, no accuracy figures. Dates: Expo 2030 only.

Checks (all of them, reported together; nothing is written if any fails):
  - exact keys (an unknown key such as "mae" or "wape" is refused)
  - mode <-> data origin <-> date window
  - no look-ahead: as_of < date and date = as_of + horizon_days, 1..7
  - p10 <= p50 <= p90; threshold_high <= threshold_peak; the level equals the
    level of p50 against those thresholds
  - osaka_replay: the model and freeze hash equal forecast/out/selection_frozen.json
  - each date once

Atomic (one transaction) and idempotent: the same file again changes nothing.
A different file under an existing run code is refused unless replace=True.
The new run becomes the active run of its mode; the previous one is kept,
inactive. Only active runs feed the engine (services/snapshot.forecast_for).

Django never imports the forecast/ package (ADR 0009); it only reads files.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from django.conf import settings
from django.db import transaction

from coverage import policy
from coverage.codes import AttendanceLevel, DataOrigin, ForecastMode
from coverage.models import AttendanceForecast, ForecastRun

FORMAT = "covercast-forecast-v1"
FROZEN_SELECTION = Path(settings.BASE_DIR) / "forecast" / "out" / "selection_frozen.json"
SCENARIO_MODEL = "scenario_assumption"
MAX_DAYS = 400
CODE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{2,79}$")

RUN_KEYS = {
    ForecastMode.OSAKA_REPLAY: ({"code", "mode", "model", "data_origin", "level_method", "freeze_sha256", "sources"},
                                {"note"}),
    ForecastMode.RIYADH_SCENARIO: ({"code", "mode", "model", "data_origin", "level_method"}, {"note"}),
}
DAY_KEYS = {
    ForecastMode.OSAKA_REPLAY: {"date", "as_of", "horizon_days", "p10", "p50", "p90", "actual", "level",
                                "threshold_high", "threshold_peak"},
    ForecastMode.RIYADH_SCENARIO: {"date", "level"},
}
ORIGIN = {ForecastMode.OSAKA_REPLAY: DataOrigin.OFFICIAL, ForecastMode.RIYADH_SCENARIO: DataOrigin.SIMULATED}
WINDOW = {ForecastMode.OSAKA_REPLAY: policy.OSAKA_REPLAY_DATES, ForecastMode.RIYADH_SCENARIO: policy.RIYADH_SCENARIO_DATES}


class ForecastImportError(Exception):
    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("\n".join(problems))


@dataclass
class ImportResult:
    run: ForecastRun
    created: bool
    days: int
    deactivated: list[str] = field(default_factory=list)


def sha256_lf(data: bytes) -> str:
    """Same hash as forecast/freeze.py: content with CRLF normalised to LF."""
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def level_of(value: float, high: float, peak: float) -> str:
    """Same cut rule as forecast/metrics.level: >= peak -> peak, >= high -> high."""
    if value >= peak:
        return AttendanceLevel.PEAK
    if value >= high:
        return AttendanceLevel.HIGH
    return AttendanceLevel.NORMAL


def _is_int(x) -> bool:
    return isinstance(x, int) and not isinstance(x, bool) and x >= 0


def _is_num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) and x >= 0


def _date(x):
    try:
        return date.fromisoformat(x) if isinstance(x, str) else None
    except ValueError:
        return None


def frozen_selection(path: Path = FROZEN_SELECTION) -> tuple[str, str] | None:
    """(selected_model, sha256) of the committed freeze, or None if it is not there."""
    if not path.exists():
        return None
    raw = path.read_bytes()
    return json.loads(raw)["selected_model"], sha256_lf(raw)


def validate(doc, frozen: tuple[str, str] | None) -> list[str]:
    p: list[str] = []
    if not isinstance(doc, dict) or set(doc) != {"format", "run", "days"}:
        return ["top level must have exactly the keys: format, run, days"]
    if doc["format"] != FORMAT:
        p.append(f"format must be {FORMAT!r}")
    run, days = doc["run"], doc["days"]
    if not isinstance(run, dict) or run.get("mode") not in ForecastMode.values:
        return p + [f"run.mode must be one of {ForecastMode.values}"]
    mode = ForecastMode(run["mode"])
    required, optional = RUN_KEYS[mode]
    if missing := sorted(required - set(run)):
        p.append(f"run: missing {missing}")
    if extra := sorted(set(run) - required - optional):
        p.append(f"run: keys not allowed for {mode.value}: {extra}")
    if not isinstance(run.get("code"), str) or not CODE_RE.match(run["code"]):
        p.append("run.code must be 3-80 characters: a-z, 0-9, '-', '_'")
    if run.get("data_origin") != ORIGIN[mode]:
        p.append(f"run.data_origin must be {ORIGIN[mode].value!r} for {mode.value}")
    for k in ("model", "level_method"):
        if k in run and (not isinstance(run[k], str) or not run[k].strip()):
            p.append(f"run.{k} must be a non-empty text")
    if mode == ForecastMode.RIYADH_SCENARIO and run.get("model") != SCENARIO_MODEL:
        p.append(f"run.model must be {SCENARIO_MODEL!r}: a scenario level is an assumption, not a model output")
    if mode == ForecastMode.OSAKA_REPLAY:
        if frozen is None:
            p.append("forecast/out/selection_frozen.json not found: an Osaka replay must match the frozen selection")
        elif (run.get("model"), run.get("freeze_sha256")) != frozen:
            p.append(f"run.model/freeze_sha256 must equal the frozen selection {frozen[0]!r} / {frozen[1][:8]}...")

    if not isinstance(days, list) or not days or len(days) > MAX_DAYS:
        return p + [f"days must be a list of 1..{MAX_DAYS} entries"]
    lo, hi = (date.fromisoformat(x) for x in WINDOW[mode])
    seen: set[date] = set()
    for i, d in enumerate(days):
        where = f"days[{i}]"
        if not isinstance(d, dict) or set(d) != DAY_KEYS[mode]:
            got = sorted(d) if isinstance(d, dict) else type(d).__name__
            p.append(f"{where}: keys must be exactly {sorted(DAY_KEYS[mode])} for {mode.value}, got {got}")
            continue
        day = _date(d["date"])
        if day is None:
            p.append(f"{where}: date {d['date']!r} is not YYYY-MM-DD")
            continue
        where = f"{where} ({day})"
        if not lo <= day <= hi:
            p.append(f"{where}: outside {mode.value} dates {lo}..{hi}")
        if day in seen:
            p.append(f"{where}: date appears twice")
        seen.add(day)
        if d["level"] not in AttendanceLevel.values:
            p.append(f"{where}: level must be one of {AttendanceLevel.values}")
        if mode != ForecastMode.OSAKA_REPLAY:
            continue
        as_of, h = _date(d["as_of"]), d["horizon_days"]
        if as_of is None or not _is_int(h) or not 1 <= h <= policy.FORECAST_MAX_HORIZON_DAYS:
            p.append(f"{where}: needs as_of (YYYY-MM-DD) and horizon_days 1..{policy.FORECAST_MAX_HORIZON_DAYS}")
        elif (day - as_of).days != h:
            p.append(f"{where}: date must equal as_of + horizon_days (no look-ahead)")
        nums = [d[k] for k in ("p10", "p50", "p90", "actual")]
        if not all(_is_int(x) for x in nums):
            p.append(f"{where}: p10, p50, p90 and actual must be whole numbers >= 0")
        elif not nums[0] <= nums[1] <= nums[2]:
            p.append(f"{where}: quantiles must satisfy p10 <= p50 <= p90")
        th, tp = d["threshold_high"], d["threshold_peak"]
        if not (_is_num(th) and _is_num(tp) and th <= tp):
            p.append(f"{where}: thresholds must be numbers with threshold_high <= threshold_peak")
        elif _is_int(d["p50"]) and d["level"] in AttendanceLevel.values and level_of(d["p50"], th, tp) != d["level"]:
            p.append(f"{where}: level {d['level']!r} does not match p50 against the thresholds")
    return p


def parse(raw: bytes) -> dict:
    try:
        return json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ForecastImportError([f"not valid UTF-8 JSON: {exc}"]) from exc


def import_forecast(raw: bytes, *, replace: bool = False, frozen=FROZEN_SELECTION) -> ImportResult:
    doc = parse(raw)
    problems = validate(doc, frozen_selection(frozen) if isinstance(frozen, Path) else frozen)
    if problems:
        raise ForecastImportError(problems)
    return _write(doc, sha256_lf(raw), replace)


@transaction.atomic
def _write(doc: dict, sha: str, replace: bool) -> ImportResult:
    r = doc["run"]
    existing = ForecastRun.objects.select_for_update().filter(code=r["code"]).first()
    if existing is not None:
        if existing.source_sha256 == sha:
            return ImportResult(existing, created=False, days=existing.days.count())
        if not replace:
            raise ForecastImportError([f"run {r['code']!r} already exists with different content; "
                                       "use --replace to import the new file under the same code"])
        existing.delete()
    mode = r["mode"]
    deactivated = list(ForecastRun.objects.filter(mode=mode, is_active=True).values_list("code", flat=True))
    ForecastRun.objects.filter(mode=mode, is_active=True).update(is_active=False)
    run = ForecastRun.objects.create(
        code=r["code"], mode=mode, model_name=r["model"], data_origin=r["data_origin"],
        level_method=r["level_method"][:300], freeze_sha256=r.get("freeze_sha256", ""),
        source_sha256=sha, note=r.get("note", "")[:300], is_active=True,
    )
    replay = mode == ForecastMode.OSAKA_REPLAY
    version = f"{r['model']}@{r['freeze_sha256'][:8]}" if replay else SCENARIO_MODEL
    AttendanceForecast.objects.bulk_create(
        AttendanceForecast(
            run=run, date=date.fromisoformat(d["date"]), level=d["level"], data_origin=r["data_origin"],
            model_version=version,
            trained_on=f"official visitors_general 2025-04-13..{d['as_of']}" if replay else "n/a: scenario assumption",
            p10=d.get("p10"), p50=d.get("p50"), p90=d.get("p90"), actual=d.get("actual"),
            as_of=date.fromisoformat(d["as_of"]) if replay else None, horizon_days=d.get("horizon_days"),
            threshold_high=d.get("threshold_high"), threshold_peak=d.get("threshold_peak"),
        )
        for d in doc["days"]
    )
    return ImportResult(run, created=True, days=len(doc["days"]), deactivated=deactivated)


def import_path(path: Path, *, replace: bool = False) -> ImportResult:
    return import_forecast(Path(path).read_bytes(), replace=replace)

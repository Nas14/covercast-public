"""F5: export the Osaka 2025 historical replay for Django (covercast-forecast-v1).

What it exports: the forecasts of the FROZEN, protocol-selected model
(forecast/out/selection_frozen.json, today `seasonal_naive`) exactly as the
evaluation already produced them:

  - the 14 selection folds  (forecast/out/f2/predictions.csv), days 10 Jun .. 15 Sep 2025
  - the 4 final folds       (forecast/out/final/predictions.csv), days 16 Sep .. 13 Oct 2025

Nothing is re-trained, re-run or re-selected: p10/p50/p90 are copied from the
committed artifacts. Chronos-2 is not exported (it is not the selected model;
its holdout result is reported separately in docs/18).

Pressure level of each day (normal / high / peak), no look-ahead:
  thresholds = P75 and P90 of the OFFICIAL visitors_general series from
  13 Apr 2025 up to and including that forecast's as_of (EVALUATION_PROTOCOL.md
  section 4, metrics.thresholds). Only data a planner had at the origin.
  The quantile pair (0.75, 0.90) was fixed in the protocol before any model ran;
  it was not tuned on the final test (the final threshold_sensitivity.csv is
  reported, never used here).
The recomputed thresholds and levels are checked against the artifact columns
(t75, t90, level_pred), so the export cannot drift from what was evaluated.

This is Osaka 2025, official data. It is NOT a forecast for Riyadh 2030.

    python forecast/export_forecast.py            # write forecast/out/export/osaka_replay.json
    python forecast/export_forecast.py --check    # the committed file reproduces exactly
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import freeze as FZ  # noqa: E402
import metrics as M  # noqa: E402
import series as S  # noqa: E402

FORMAT = "covercast-forecast-v1"
SOURCES = (("selection", HERE / "out" / "f2" / "predictions.csv"),
           ("final", HERE / "out" / "final" / "predictions.csv"))
SELECTION_ORIGINS = tuple(range(57, 149, 7))   # same folds as evaluate.py (not imported: no side effects)
FINAL_ORIGINS = FZ.FINAL_ORIGINS
HORIZON = 7
OUT = HERE / "out" / "export" / "osaka_replay.json"
LEVEL_METHOD = (
    "P75/P90 of official visitors_general from 2025-04-13 up to and including as_of "
    "(EVALUATION_PROTOCOL.md section 4); quantile pair fixed in the protocol before any model; "
    "not tuned on the final test"
)


class ExportError(RuntimeError):
    pass


def _rel(p: Path) -> str:
    return p.resolve().relative_to(FZ.REPO).as_posix()


def day_entry(series: S.Series, row: dict) -> dict:
    """One exported day. Thresholds use series.upto(as_of) only (no look-ahead)."""
    o, h = int(row["as_of_index"]), int(row["horizon_days"])
    day = o + h
    if not 1 <= h <= HORIZON or int(row["day_index"]) != day:
        raise ExportError(f"{row['date']}: day_index must be as_of + horizon (1..{HORIZON})")
    if row["date"] != series.dates[day].isoformat() or row["as_of"] != series.dates[o].isoformat():
        raise ExportError(f"{row['date']}: dates do not match the official series")
    cuts = M.thresholds(series.upto(o))
    p10, p50, p90 = (int(row[k]) for k in ("p10", "p50", "p90"))
    level = M.level(p50, cuts)
    # agreement with what the evaluation wrote (the CSV keeps 6 decimals of a value rounded to 0.1)
    for got, col in zip(cuts, ("t75", "t90")):
        if abs(got - float(row[col])) > 0.051:
            raise ExportError(f"{row['date']}: {col} {row[col]} differs from recomputed {got}")
    if level != row["level_pred"]:
        raise ExportError(f"{row['date']}: level {level} differs from the artifact's {row['level_pred']}")
    actual = int(series.y[day])
    if actual != int(row["actual"]):
        raise ExportError(f"{row['date']}: actual differs from the official series")
    return {"date": row["date"], "as_of": row["as_of"], "horizon_days": h,
            "p10": p10, "p50": p50, "p90": p90, "actual": actual, "level": level,
            "threshold_high": round(cuts[0], 2), "threshold_peak": round(cuts[1], 2)}


def build(series: S.Series, frozen: dict, sources=SOURCES) -> dict:
    model = frozen["selected_model"]
    days, seen = [], set()
    for stage, path in sources:
        with open(path, encoding="utf-8") as f:
            rows = [r for r in csv.DictReader(f) if r["model"] == model]
        origins = SELECTION_ORIGINS if stage == "selection" else FINAL_ORIGINS
        expected = {(o, h) for o in origins for h in range(1, HORIZON + 1)}
        got = {(int(r["as_of_index"]), int(r["horizon_days"])) for r in rows}
        if got != expected or len(rows) != len(expected):
            raise ExportError(f"{_rel(path)}: rows of {model} do not match the {stage} folds")
        for r in rows:
            e = day_entry(series, r)
            if e["date"] in seen:
                raise ExportError(f"{e['date']} appears twice")
            seen.add(e["date"])
            days.append(e)
    days.sort(key=lambda d: d["date"])
    sha = FZ.freeze_sha256()
    return {
        "format": FORMAT,
        "run": {
            "code": f"osaka-replay-{model}-{sha[:8]}",
            "mode": "osaka_replay",
            "model": model,
            "data_origin": "official",
            "freeze_sha256": sha,
            "level_method": LEVEL_METHOD,
            "sources": {_rel(p): FZ.sha256_lf(p) for _, p in sources},
            "note": ("Osaka Expo 2025 historical replay from official daily attendance. "
                     "Forecasts of the frozen protocol-selected model. Not a forecast for Riyadh 2030."),
        },
        "days": days,
    }


def render(doc: dict) -> str:
    return json.dumps(doc, indent=1, ensure_ascii=False, sort_keys=True) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)
    try:
        frozen = FZ.verify_freeze(FZ.FROZEN)
    except FZ.FreezeError as e:
        print(f"REFUSED: freeze does not hold: {e}", file=sys.stderr)
        return 2
    try:
        text = render(build(S.load(), frozen))
    except ExportError as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 3
    if args.check:
        if not OUT.exists() or OUT.read_text(encoding="utf-8").replace("\r\n", "\n") != text:
            print(f"FAILED: {_rel(OUT)} is out of date", file=sys.stderr)
            return 1
        print("OK: export reproduces exactly")
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {_rel(OUT)} ({len(json.loads(text)['days'])} days)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

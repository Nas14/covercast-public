"""F2: rolling-origin evaluation, exactly as forecast/EVALUATION_PROTOCOL.md declares.

  python forecast/evaluate.py            selection folds only (F2); writes forecast/out/f2/
  python forecast/evaluate.py --check    recompute and fail if the files on disk differ
  python forecast/evaluate.py --final    the final test, for REPORTING ONLY (F4c). Refuses unless
                                         forecast/out/selection_frozen.json is a valid freeze
                                         (freeze.verify_freeze), checked BEFORE any data is read.
                                         Never calls select(): the frozen selection stands.
  python forecast/evaluate.py --stage f3 [--check]
                                         the same folds with Chronos-2 added from the files
                                         run_chronos_local.py wrote; writes forecast/out/f3/
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import hashlib  # noqa: E402

import freeze as FZ  # noqa: E402
import metrics as M  # noqa: E402
import series as S  # noqa: E402
from models import Forecast  # noqa: E402
from models import calendar_regression, seasonal_naive  # noqa: E402

HORIZON = 7
SELECTION_ORIGINS = tuple(range(57, 149, 7))   # 9 Jun .. 8 Sep 2025 (Mondays), 14 folds
FINAL_ORIGINS = (155, 162, 169, 176)            # 15 Sep .. 6 Oct 2025, sealed
RISING_PHASE_ORIGINS = SELECTION_ORIGINS[-4:]   # protocol §6(b): 19 Aug .. 15 Sep
BASELINE = seasonal_naive.NAME
MODELS = (seasonal_naive, calendar_regression)
MIN_IMPROVEMENT = 0.01                          # one percentage point of WAPE
SENSITIVITY = ((0.70, 0.85), (0.75, 0.90), (0.80, 0.95))
SENSITIVITY_REFS = (("all_history", None), ("last_28_days", 28))

OUT_DIR = HERE / "out" / "f2"
F3_DIR = HERE / "out" / "f3"
CHRONOS_DIR = F3_DIR / "chronos2"
FROZEN = FZ.FROZEN
FINAL_DIR = HERE / "out" / "final"
FINAL_CHRONOS_DIR = FINAL_DIR / "chronos2"
CHRONOS_SPEC = "chronos2-spec-v1"


class SealedError(RuntimeError):
    """The final test was requested before the selection was frozen."""


def origins_for(stage: str) -> tuple[int, ...]:
    if stage == "selection":
        return SELECTION_ORIGINS
    if stage == "final":
        try:
            FZ.verify_freeze(FROZEN)
        except FZ.FreezeError as e:
            raise SealedError(f"final test is sealed: {e}") from e
        return FINAL_ORIGINS
    raise ValueError(stage)


def run_fold(series: S.Series, model, as_of: int) -> list[dict]:
    """One model, one origin. The model only ever receives days 0..as_of."""
    history = series.upto(as_of)
    fc = model.forecast(history, as_of, HORIZON, series)
    cuts = M.thresholds(history)                 # levels from data up to as_of only
    rows = []
    for h in range(1, HORIZON + 1):
        day = as_of + h
        actual = int(series.y[day])
        p50 = float(fc.p50[h - 1])
        rows.append({
            "model": model.NAME,
            "as_of": series.dates[as_of].isoformat(),
            "as_of_index": as_of,
            "date": series.dates[day].isoformat(),
            "day_index": day,
            "horizon_days": h,
            "p10": int(fc.p10[h - 1]), "p50": int(p50), "p90": int(fc.p90[h - 1]),
            "actual": actual,
            "t75": round(cuts[0], 1), "t90": round(cuts[1], 1),
            "level_pred": M.level(p50, cuts),
            "level_actual": M.level(actual, cuts),
            "clipped_in_fold": fc.clipped,
            "notes": "; ".join(fc.notes),
        })
    return rows


def run(series: S.Series, origins, models=MODELS) -> list[dict]:
    return [row for m in models for o in origins for row in run_fold(series, m, o)]


def _summ(rows) -> dict:
    a = lambda k: np.array([r[k] for r in rows], dtype=float)  # noqa: E731
    out = M.summary(a("actual"), a("p10"), a("p50"), a("p90"))
    out["level_accuracy"] = M.level_accuracy([r["level_actual"] for r in rows], [r["level_pred"] for r in rows])
    return out


def metric_rows(rows: list[dict]) -> list[dict]:
    """Pooled, per fold, per horizon, and the report-only peak slices (protocol §6)."""
    out = []
    models = sorted({r["model"] for r in rows})

    def add(model, slice_, key, subset):
        if subset:
            out.append({"model": model, "slice": slice_, "key": key, **_summ(subset)})

    for m in models:
        mr = [r for r in rows if r["model"] == m]
        add(m, "pooled", "all", mr)
        for o in sorted({r["as_of"] for r in mr}):
            add(m, "fold", o, [r for r in mr if r["as_of"] == o])
        for h in range(1, HORIZON + 1):
            add(m, "horizon", str(h), [r for r in mr if r["horizon_days"] == h])
        add(m, "peak_report_only", "actual_level_high_or_peak",
            [r for r in mr if r["level_actual"] in ("high", "peak")])
        add(m, "peak_report_only", "rising_phase_last_4_folds",
            [r for r in mr if r["as_of_index"] in RISING_PHASE_ORIGINS])
    return out


def select(metric_table: list[dict]) -> dict:
    """Protocol §5. Uses pooled WAPE of the selection folds and nothing else."""
    pooled = {r["model"]: r["wape"] for r in metric_table if r["slice"] == "pooled"}
    base = pooled[BASELINE]
    challengers = {m: w for m, w in pooled.items() if m != BASELINE}
    best = min(challengers, key=challengers.get) if challengers else None
    if best is not None and challengers[best] <= base - MIN_IMPROVEMENT:
        return {"selected": best, "status": "model", "pooled_wape": pooled,
                "reason": f"{best} WAPE {challengers[best]:.4f} beats {BASELINE} {base:.4f} by "
                          f"{base - challengers[best]:.4f} (>= {MIN_IMPROVEMENT})"}
    reason = (f"no challenger beats {BASELINE} ({base:.4f}) by >= {MIN_IMPROVEMENT}"
              + (f"; best was {best} {challengers[best]:.4f}" if best else ""))
    return {"selected": BASELINE, "status": "fallback_model", "pooled_wape": pooled, "reason": reason}


def threshold_sensitivity(series: S.Series, rows: list[dict]) -> list[dict]:
    """F-D3, report only: level accuracy under other cut-points and reference windows."""
    out = []
    for m in sorted({r["model"] for r in rows}):
        mr = [r for r in rows if r["model"] == m]
        for lo_q, hi_q in SENSITIVITY:
            for ref_name, last_n in SENSITIVITY_REFS:
                actual_l, pred_l = [], []
                for r in mr:
                    hist = series.upto(r["as_of_index"])
                    ref = hist if last_n is None else hist[-last_n:]
                    cuts = M.thresholds(ref, lo_q, hi_q)
                    actual_l.append(M.level(r["actual"], cuts))
                    pred_l.append(M.level(r["p50"], cuts))
                out.append({
                    "model": m, "low_q": lo_q, "high_q": hi_q, "reference": ref_name,
                    "level_accuracy": M.level_accuracy(actual_l, pred_l),
                    **{f"actual_{lv}_share": float(np.mean([x == lv for x in actual_l])) for lv in M.LEVELS},
                })
    return out


def _csv(rows: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(rows[0]), lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow({k: (f"{v:.6f}" if isinstance(v, float) else v) for k, v in r.items()})
    return buf.getvalue()


def report_md(rows, mt, sens, sel, title="F2 evaluation report") -> str:
    def pct(x):
        return f"{100 * x:.1f}%"

    def num(x):
        return f"{x:,.0f}"

    pooled = {r["model"]: r for r in mt if r["slice"] == "pooled"}
    lines = [
        f"# {title} (generated by forecast/evaluate.py; do not edit by hand)",
        "",
        f"Selection folds: {len(SELECTION_ORIGINS)} (as_of {rows[0]['as_of']} .. "
        f"{max(r['as_of'] for r in rows)}), horizon {HORIZON} days, "
        f"{pooled[BASELINE]['n_days']} evaluated days per model. Final test: sealed, not run.",
        "",
        "## Pooled (selection folds)",
        "",
        "| model | MAE | WAPE | Bias | PI80 coverage | level accuracy |",
        "|---|---|---|---|---|---|",
    ]
    for m, r in sorted(pooled.items()):
        lines.append(f"| {m} | {num(r['mae'])} | {pct(r['wape'])} | {pct(r['bias'])} | "
                     f"{pct(r['pi80_coverage'])} | {pct(r['level_accuracy'])} |")
    lines += ["", f"**Provisional selection:** `{sel['selected']}` ({sel['status']}). {sel['reason']}.", ""]

    lines += ["## WAPE per fold", "", "| as_of | " + " | ".join(sorted(pooled)) + " |",
              "|---|" + "---|" * len(pooled)]
    for o in sorted({r["key"] for r in mt if r["slice"] == "fold"}):
        cells = [pct(next(r["wape"] for r in mt if r["slice"] == "fold" and r["key"] == o and r["model"] == m))
                 for m in sorted(pooled)]
        lines.append(f"| {o} | " + " | ".join(cells) + " |")

    lines += ["", "## WAPE per horizon", "", "| horizon | " + " | ".join(sorted(pooled)) + " |",
              "|---|" + "---|" * len(pooled)]
    for h in range(1, HORIZON + 1):
        cells = [pct(next(r["wape"] for r in mt if r["slice"] == "horizon" and r["key"] == str(h) and r["model"] == m))
                 for m in sorted(pooled)]
        lines.append(f"| {h} | " + " | ".join(cells) + " |")

    lines += ["", "## Peak analysis (report only; not used for selection)", "",
              "| slice | model | days | MAE | WAPE | Bias | PI80 coverage |", "|---|---|---|---|---|---|---|"]
    for r in mt:
        if r["slice"] == "peak_report_only":
            lines.append(f"| {r['key']} | {r['model']} | {r['n_days']} | {num(r['mae'])} | {pct(r['wape'])} | "
                         f"{pct(r['bias'])} | {pct(r['pi80_coverage'])} |")

    lines += ["", "## Level thresholds sensitivity (report only)", "",
              "| model | cut-points | reference | level accuracy | normal | high | peak |", "|---|---|---|---|---|---|---|"]
    for r in sens:
        lines.append(f"| {r['model']} | P{int(r['low_q']*100)}/P{int(r['high_q']*100)} | {r['reference']} | "
                     f"{pct(r['level_accuracy'])} | {pct(r['actual_normal_share'])} | {pct(r['actual_high_share'])} | "
                     f"{pct(r['actual_peak_share'])} |")

    clipped = {m: sum(r["clipped_in_fold"] for r in rows if r["model"] == m and r["horizon_days"] == 1)
               for m in sorted(pooled)}
    dropped = sorted({r["notes"] for r in rows if r["notes"]})
    lines += ["", "## Mechanics", "",
              f"- Quantile corrections to keep p10 <= p50 <= p90 (count over all folds): {clipped}",
              f"- Regression columns dropped as constant in a window: {dropped or 'none'}", ""]
    return "\n".join(lines)


# ---------------------------------------------------------------- F3: Chronos-2 from files

class IngestError(ValueError):
    """The Chronos-2 files do not match what the spec and the protocol require."""


class PrecomputedModel:
    """Replays forecasts made elsewhere, so they go through the same run_fold and metrics."""

    def __init__(self, name: str, table: dict[int, list[dict]]):
        self.NAME = name
        self.table = table

    def forecast(self, history, as_of, horizon, series=None) -> Forecast:
        rows = self.table[as_of]
        arr = lambda k: np.array([float(r[k]) for r in rows])  # noqa: E731
        notes = sorted({r["notes"] for r in rows if r["notes"]})
        return Forecast(arr("p10"), arr("p50"), arr("p90"), int(rows[0]["clipped_in_fold"]), notes)


def _sha256_lf(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def load_chronos(series: S.Series, folder: Path = CHRONOS_DIR, stage: str = "selection"):
    """Returns (PrecomputedModel | None, status dict). Refuses anything off-spec.

    stage "selection": the 14 selection folds (F3 files). stage "final": the 4 final folds,
    and the manifest must also carry stage "final", the sha256 of the local freeze file,
    a git commit, and a passed check of the pinned files.
    """
    unavailable = folder / "chronos2_unavailable.json"
    preds, manifest = folder / "chronos2_predictions.csv", folder / "run_manifest.json"
    if not preds.exists():
        if unavailable.exists():
            info = json.loads(unavailable.read_text(encoding="utf-8"))
            return None, {"chronos2": "unavailable", "reason": info.get("reason", ""), "comparison": "incomplete"}
        return None, {"chronos2": "not run yet", "comparison": "incomplete"}
    if not manifest.exists():
        raise IngestError("run_manifest.json is missing next to chronos2_predictions.csv")
    man = json.loads(manifest.read_text(encoding="utf-8"))
    if man.get("spec_version") != CHRONOS_SPEC:
        raise IngestError(f"spec_version {man.get('spec_version')!r}, expected {CHRONOS_SPEC!r}")
    if man.get("input_sha256_lf") != _sha256_lf(S.SERIES_CSV):
        raise IngestError("the series used for Chronos-2 differs from forecast/data/osaka_daily.csv")
    if man.get("determinism_check") != "passed":
        raise IngestError("the determinism check did not pass")
    if stage == "final":
        if man.get("stage") != "final":
            raise IngestError(f"manifest stage {man.get('stage')!r}, expected 'final'")
        if man.get("freeze_sha256") != FZ.freeze_sha256(FROZEN):
            raise IngestError("the run used a different freeze than forecast/out/selection_frozen.json")
        if man.get("pinned_check") != "passed":
            raise IngestError("the pinned-file check did not pass on the machine that ran Chronos-2")
        if not man.get("git", {}).get("commit"):
            raise IngestError("the manifest does not record the git commit of the code that ran")

    with open(preds, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    origins = FINAL_ORIGINS if stage == "final" else SELECTION_ORIGINS
    expected = {(o, h) for o in origins for h in range(1, HORIZON + 1)}
    got = [(int(r["as_of_index"]), int(r["horizon_days"])) for r in rows]
    if len(got) != len(set(got)):
        raise IngestError("duplicate (as_of, horizon) rows")
    if set(got) != expected:
        extra, missing = sorted(set(got) - expected), sorted(expected - set(got))
        what = "4 final folds" if stage == "final" else "14 selection folds"
        raise IngestError(f"rows do not match the {what}: extra {extra[:3]}, missing {missing[:3]}")
    table: dict[int, list[dict]] = {}
    for r in rows:
        o, h, day = int(r["as_of_index"]), int(r["horizon_days"]), int(r["day_index"])
        if r["model"] != "chronos2":
            raise IngestError(f"model {r['model']!r} in the Chronos-2 file")
        if day != o + h or r["date"] != series.dates[day].isoformat() or r["as_of"] != series.dates[o].isoformat():
            raise IngestError(f"dates do not match as_of + horizon at as_of {r['as_of']} h {h}")
        p10, p50, p90 = (int(r[k]) for k in ("p10", "p50", "p90"))
        if not p10 <= p50 <= p90:
            raise IngestError(f"quantiles out of order on {r['date']}: {p10} {p50} {p90}")
        table.setdefault(o, []).append(r)
    for o in table:
        table[o].sort(key=lambda r: int(r["horizon_days"]))
    status = {"chronos2": "included", "comparison": "complete",
              "revision_used": man.get("revision_used"), "weights_sha256": man.get("weights_sha256"),
              "environment": {k: man.get("environment", {}).get(k) for k in ("python", "torch", "chronos", "platform")}}
    if stage == "final":
        status["git_commit"] = man["git"]["commit"]
        status["freeze_sha256"] = man["freeze_sha256"]
    return PrecomputedModel("chronos2", table), status


def build(series: S.Series, stage: str = "selection", models=MODELS, extra_status: dict | None = None) -> dict[str, str]:
    rows = run(series, origins_for(stage), models)
    mt = metric_rows(rows)
    sens = threshold_sensitivity(series, rows)
    sel = select(mt)
    if extra_status is not None:
        sel = {**sel, "candidates": status_line(extra_status)}
    files = {
        "predictions.csv": _csv(rows),
        "metrics.csv": _csv(mt),
        "threshold_sensitivity.csv": _csv(sens),
        "selection.json": json.dumps(sel, indent=2, sort_keys=True) + "\n",
        "evaluation_report.md": report_md(rows, mt, sens, sel,
                                          "F2 evaluation report" if extra_status is None else
                                          "F3 evaluation report (three models, selection folds)"),
    }
    if extra_status is not None:
        files["chronos2_status.json"] = json.dumps(extra_status, indent=2, sort_keys=True) + "\n"
        files["evaluation_report.md"] += f"\n## Chronos-2\n\n{status_line(extra_status)}\n"
    return files


# ---------------------------------------------------------------- F4c: final test, reporting only

def build_final(series: S.Series, frozen: dict, models, chronos_status: dict) -> dict[str, str]:
    """All models on the 4 final folds. Reporting only: select() is never called here."""
    rows = run(series, origins_for("final"), models)   # re-checks the freeze
    mt = metric_rows(rows)
    sens = threshold_sensitivity(series, rows)
    header = {
        "final_reporting_only": True,
        "frozen_selection": frozen["selected_model"],
        "frozen_status": frozen["status"],
        "freeze_sha256": FZ.freeze_sha256(FROZEN),
        "chronos2": chronos_status,
    }
    return {
        "predictions.csv": _csv(rows),
        "metrics.csv": _csv(mt),
        "threshold_sensitivity.csv": _csv(sens),
        "final_status.json": json.dumps(header, indent=2, sort_keys=True) + "\n",
        "final_report.md": final_report_md(rows, mt, header),
    }


def final_report_md(rows, mt, header) -> str:
    pct = lambda x: f"{100 * x:.1f}%"  # noqa: E731
    pooled = {r["model"]: r for r in mt if r["slice"] == "pooled"}
    lines = [
        "# F4 final-test report (generated by forecast/evaluate.py --final; do not edit by hand)",
        "",
        "**Reporting only.** The selection was frozen before this run and is not changed by it: "
        f"`{header['frozen_selection']}` ({header['frozen_status']}), freeze sha256 `{header['freeze_sha256']}`.",
        "",
        f"Final folds: {len(FINAL_ORIGINS)} (as_of {rows[0]['as_of']} .. {max(r['as_of'] for r in rows)}), "
        f"horizon {HORIZON} days, {next(iter(pooled.values()))['n_days']} days per model.",
        "",
        "| model | MAE | WAPE | Bias | PI80 coverage | level accuracy |",
        "|---|---|---|---|---|---|",
    ]
    for m, r in sorted(pooled.items()):
        lines.append(f"| {m} | {r['mae']:,.0f} | {pct(r['wape'])} | {pct(r['bias'])} | "
                     f"{pct(r['pi80_coverage'])} | {pct(r['level_accuracy'])} |")
    lines += ["", "## Chronos-2", "", status_line(header["chronos2"]), ""]
    return "\n".join(lines)


def status_line(st: dict) -> str:
    if st["chronos2"] == "included":
        return "Chronos-2 included (chronos2-spec-v1); comparison complete."
    why = f" Reason: {st['reason']}." if st.get("reason") else ""
    return f"Chronos-2 {st['chronos2']}; the comparison is INCOMPLETE and is reported as such.{why}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--final", action="store_true")
    ap.add_argument("--stage", choices=("f2", "f3"), default="f2")
    args = ap.parse_args(argv)
    if args.final:
        # The freeze is checked BEFORE the series (which holds the final-test targets) is read.
        try:
            frozen = FZ.verify_freeze(FROZEN)
        except FZ.FreezeError as e:
            print(f"REFUSED: final test is sealed: {e}", file=sys.stderr)
            return 2
        series = S.load()
        try:
            chronos, status = load_chronos(series, FINAL_CHRONOS_DIR, stage="final")
        except IngestError as e:
            print(f"REFUSED Chronos-2 final files: {e}", file=sys.stderr)
            return 3
        if status["chronos2"] == "not run yet":
            print("REFUSED: no Chronos-2 final files and no chronos2_unavailable.json; the final test is "
                  "run once, with all three models or a recorded reason", file=sys.stderr)
            return 3
        files = build_final(series, frozen, MODELS + ((chronos,) if chronos else ()), status)
        return _write_or_check(files, FINAL_DIR, args.check)
    series = S.load()
    try:
        if args.stage == "f3":
            chronos, status = load_chronos(series, CHRONOS_DIR)
            models = MODELS + ((chronos,) if chronos else ())
            files = build(series, "selection", models, status)
            out = F3_DIR
        else:
            files = build(series, "selection")
            out = OUT_DIR
    except IngestError as e:
        print(f"REFUSED Chronos-2 files: {e}", file=sys.stderr)
        return 3
    rc = _write_or_check(files, out, args.check)
    if rc == 0 and not args.check:
        print(json.loads(files["selection.json"])["reason"])
    return rc


def _write_or_check(files: dict[str, str], out: Path, check: bool) -> int:
    if check:
        stale = [n for n, text in files.items()
                 if not (out / n).exists() or (out / n).read_text(encoding="utf-8").replace("\r\n", "\n") != text]
        if stale:
            print(f"FAILED: out of date: {stale}", file=sys.stderr)
            return 1
        print("OK: outputs reproduce exactly")
        return 0
    out.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (out / name).write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())

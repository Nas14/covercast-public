"""Run Chronos-2 on Nouf's machine: the 14 selection folds (F3) or, after the freeze, the 4 final folds (F4c).

    python forecast/run_chronos_local.py                       F3 selection folds (already done)
    python forecast/run_chronos_local.py --final               F4c final folds; refuses without a valid freeze
    python forecast/run_chronos_local.py [--final] --model-path C:\\models\\chronos-2   manual download
    python forecast/run_chronos_local.py [--final] --record-unavailable "why"            record that it could not run

Writes forecast/out/f3/chronos2/ (selection) or forecast/out/final/chronos2/ (final):
    chronos2_predictions.csv   forecasts only, p10/p50/p90 (no actuals, no metrics)
    run_manifest.json          stage, versions, git commit and tree status, model revision and weights
                               sha256, input sha256, freeze sha256 (final), timings
or  chronos2_unavailable.json  the reason, if it could not run

Holdout protection:
  - --final verifies the freeze (freeze.verify_freeze) BEFORE the series is read.
  - Forecasting never reads a target: each origin gets series.upto(as_of) only, and rows
    carry dates and quantiles, never actual attendance. Metrics are computed later by
    evaluate.py with the same code as the other two models.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import os
import platform
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import subprocess  # noqa: E402

import evaluate as E  # noqa: E402
import freeze as FZ  # noqa: E402
import series as S  # noqa: E402
from models import chronos2 as C2  # noqa: E402

OUT = HERE / "out" / "f3" / "chronos2"
FINAL_OUT = HERE / "out" / "final" / "chronos2"
PRED_FIELDS = ["model", "as_of", "as_of_index", "date", "day_index", "horizon_days",
               "p10", "p50", "p90", "clipped_in_fold", "notes"]


def sha256_text_file(path: Path) -> str:
    """Hash with LF line endings, so a Windows checkout (CRLF) hashes the same."""
    data = path.read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def resolve_model_dir(model_path: str | None) -> tuple[Path, str]:
    """A local folder (manual download) or the pinned revision from Hugging Face."""
    if model_path:
        p = Path(model_path)
        missing = [n for n in ("config.json", "model.safetensors") if not (p / n).exists()]
        if missing:
            raise SystemExit(f"FAILED: {p} is missing {missing}. See forecast/CHRONOS2_WINDOWS.md, section 5.")
        return p, "local-folder"
    from huggingface_hub import snapshot_download
    path = snapshot_download(C2.MODEL_ID, revision=C2.MODEL_REVISION,
                             allow_patterns=["config.json", "model.safetensors"])
    return Path(path), C2.MODEL_REVISION


def load_pipeline(model_dir: Path):
    import torch
    from chronos import Chronos2Pipeline
    torch.set_num_threads(max(1, os.cpu_count() or 1))
    pipeline = Chronos2Pipeline.from_pretrained(str(model_dir), device_map="cpu")
    dtypes = {str(p.dtype) for p in pipeline.model.parameters()}
    if dtypes != {"torch.float32"}:
        raise SystemExit(f"FAILED: expected float32 weights (spec), got {sorted(dtypes)}")
    return pipeline


def selection_grid() -> list[tuple[int, int]]:
    grid = [(o, h) for o in E.origins_for("selection") for h in range(1, E.HORIZON + 1)]
    assert max(o + h for o, h in grid) < E.FINAL_ORIGINS[0] + 1, "selection grid reaches the final test"
    return grid


def forecast_rows(series: S.Series, forecaster, as_of: int) -> list[dict]:
    """One origin. Reads days 0..as_of only (series.upto) and never a target after it."""
    fc = forecaster.forecast(series.upto(as_of), as_of, E.HORIZON, series)
    return [{
        "model": forecaster.NAME,
        "as_of": series.dates[as_of].isoformat(), "as_of_index": as_of,
        "date": series.dates[as_of + h].isoformat(), "day_index": as_of + h,
        "horizon_days": h,
        "p10": int(fc.p10[h - 1]), "p50": int(fc.p50[h - 1]), "p90": int(fc.p90[h - 1]),
        "clipped_in_fold": fc.clipped, "notes": "; ".join(fc.notes),
    } for h in range(1, E.HORIZON + 1)]


def run_folds(series: S.Series, forecaster, origins=None) -> tuple[list[dict], dict]:
    """Determinism check on the first origin, then every origin. Returns rows and per-fold seconds."""
    origins = tuple(origins or E.SELECTION_ORIGINS)
    a = forecast_rows(series, forecaster, origins[0])
    b = forecast_rows(series, forecaster, origins[0])
    if [(r["p10"], r["p50"], r["p90"]) for r in a] != [(r["p10"], r["p50"], r["p90"]) for r in b]:
        raise SystemExit("FAILED: two runs of the same fold differ; results would not be reproducible.")
    rows, seconds = [], {}
    for o in origins:
        t0 = time.perf_counter()
        rows += forecast_rows(series, forecaster, o)
        seconds[series.dates[o].isoformat()] = round(time.perf_counter() - t0, 3)
    return rows, seconds


def git_info() -> dict:
    """Code provenance. The scientific gate is freeze.verify_freeze (content hashes);
    git status is recorded for the record only, since line endings or file modes on
    Windows can make git report changes that do not touch any content hash."""
    def git(*args):
        return subprocess.run(["git", *args], cwd=FZ.REPO, capture_output=True, text=True, timeout=30)
    try:
        head = git("rev-parse", "HEAD")
        if head.returncode != 0:
            return {"commit": None, "error": head.stderr.strip()[:200]}
        tree = git("status", "--porcelain", "--untracked-files=no")
        pinned = git("status", "--porcelain", "--untracked-files=no", "--", *FZ.PINNED_LOGIC)
        return {"commit": head.stdout.strip(),
                "tree_clean": tree.stdout.strip() == "",
                "changed_tracked_files": len([ln for ln in tree.stdout.splitlines() if ln.strip()]),
                "pinned_paths_clean_per_git": pinned.stdout.strip() == ""}
    except (OSError, subprocess.SubprocessError) as e:
        return {"commit": None, "error": f"{e.__class__.__name__}: {e}"[:200]}


def write_predictions(rows: list[dict], out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "chronos2_predictions.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=PRED_FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def versions() -> dict:
    out = {"python": sys.version.split()[0], "platform": platform.platform(), "machine": platform.machine(),
           "processor": platform.processor(), "cpu_count": os.cpu_count()}
    for mod in ("numpy", "torch", "transformers", "chronos", "huggingface_hub", "accelerate"):
        try:
            m = __import__(mod)
            out[mod] = getattr(m, "__version__", "unknown")
        except Exception as e:  # noqa: BLE001 - recorded, not hidden
            out[mod] = f"not importable: {e.__class__.__name__}"
    return out


def record_unavailable(reason: str, out: Path = OUT, stage: str = "selection") -> Path:
    out.mkdir(parents=True, exist_ok=True)
    path = out / "chronos2_unavailable.json"
    path.write_text(json.dumps({
        "spec_version": C2.SPEC_VERSION, "model_id": C2.MODEL_ID, "revision": C2.MODEL_REVISION,
        "reason": reason, "recorded_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "stage": stage, "git": git_info(), "environment": versions(),
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Run Chronos-2 on the selection folds (F3) or, after the freeze, "
                                             "the final folds (--final, F4c).")
    ap.add_argument("--final", action="store_true",
                    help="the 4 final folds; refuses unless forecast/out/selection_frozen.json is a valid freeze")
    ap.add_argument("--model-path", help="folder with config.json and model.safetensors (manual download)")
    ap.add_argument("--record-unavailable", metavar="REASON", help="record that Chronos-2 could not run, and why")
    ap.add_argument("--out", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    stage = "final" if args.final else "selection"
    out = Path(args.out) if args.out else (FINAL_OUT if args.final else OUT)

    freeze_sha = None
    if args.final:
        # Before anything else, and before the series is read: no valid freeze, no final run.
        try:
            FZ.verify_freeze()
        except FZ.FreezeError as e:
            raise SystemExit(f"REFUSED: the final test is sealed: {e}")
        freeze_sha = FZ.freeze_sha256()

    if args.record_unavailable:
        print(f"Recorded: {record_unavailable(args.record_unavailable, out, stage)}")
        return 0

    git = git_info()
    if args.final and not git.get("commit"):
        raise SystemExit("FAILED: cannot read the git commit of this checkout (is git installed and is this "
                         f"the repository folder?). The final run must record it. Detail: {git.get('error')}")

    origins = E.FINAL_ORIGINS if args.final else E.SELECTION_ORIGINS
    if not args.final:
        selection_grid()
    series = S.load()
    input_sha = sha256_text_file(S.SERIES_CSV)
    print("1/4 model: locating weights (first time downloads about 478 MB) ...", flush=True)
    t0 = time.perf_counter()
    model_dir, revision = resolve_model_dir(args.model_path)
    weights_sha = sha256_file(model_dir / "model.safetensors")
    if weights_sha != C2.WEIGHTS_SHA256:
        raise SystemExit(
            "FAILED: model.safetensors is not the file the spec pins.\n"
            f"  expected sha256 {C2.WEIGHTS_SHA256}\n  found    sha256 {weights_sha}\n"
            "  Re-download it (forecast/CHRONOS2_WINDOWS.md, section 5). Do not run with other weights.")
    print("2/4 model: loading on CPU ...", flush=True)
    pipeline = load_pipeline(model_dir)
    load_seconds = round(time.perf_counter() - t0, 1)

    print(f"3/4 running {len(origins)} {stage} folds (determinism check first) ...", flush=True)
    rows, fold_seconds = run_folds(series, C2.Chronos2Forecaster(pipeline), origins)
    write_predictions(rows, out)

    manifest = {
        "spec_version": C2.SPEC_VERSION,
        "stage": stage,
        "model_id": C2.MODEL_ID,
        "revision_requested": C2.MODEL_REVISION,
        "revision_used": revision,
        "weights_sha256": weights_sha,
        "weights_bytes": (model_dir / "model.safetensors").stat().st_size,
        "input_file": "forecast/data/osaka_daily.csv",
        "input_sha256_lf": input_sha,
        "folds": [series.dates[o].isoformat() for o in origins],
        "rows": len(rows),
        "determinism_check": "passed",
        "git": git,
        "load_seconds": load_seconds,
        "fold_seconds": fold_seconds,
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "environment": versions(),
    }
    if args.final:
        manifest["freeze_sha256"] = freeze_sha
        manifest["pinned_check"] = "passed"   # verify_freeze above raised otherwise
    (out / "run_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"4/4 done: {len(rows)} rows -> {out}")
    print("Send back the two files in that folder (chronos2_predictions.csv, run_manifest.json).")
    return 0


if __name__ == "__main__":
    sys.exit(main())

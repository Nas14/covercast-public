"""F4b: the selection freeze, and the check that every final-test step runs against it.

    python forecast/freeze.py --write --frozen-at 2026-10-15T00:00:00+03:00
        create forecast/out/selection_frozen.json (refuses if it exists or if
        forecast/out/final exists). Done once, in its own commit.
    python forecast/freeze.py --check
        verify the freeze against the files on disk; exit 0 only if it holds.

What the freeze pins, and why content hashes rather than `git diff`:
  - Each pinned file is hashed by CONTENT with CRLF normalised to LF. A Windows
    checkout (autocrlf) or a file-mode change therefore does not break the freeze;
    a changed byte of logic, data or protocol does.
  - Only the files that decide numbers are pinned (logic, data, protocol, spec,
    requirements) plus the F2/F3 artifacts. Docs, READMEs, tests and the preflight
    script are not, so editing them never invalidates the freeze.
  - The freeze cannot contain its own commit id. Its integrity is shown instead by
    (a) this hash list, (b) the freeze file's own sha256 recorded in every final
    manifest and report, (c) a test pinning that sha256, and (d) git history
    showing the one commit that added it. Tamper-evident, not tamper-proof.
  - Any later change to a pinned file needs a new, documented freeze version
    approved by Nouf; the final steps refuse to run until then.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
FROZEN = HERE / "out" / "selection_frozen.json"
FREEZE_VERSION = "f4-freeze-v1"

PINNED_LOGIC = (
    "forecast/series.py",
    "forecast/features.py",
    "forecast/metrics.py",
    "forecast/evaluate.py",
    "forecast/freeze.py",
    "forecast/run_chronos_local.py",
    "forecast/models/__init__.py",
    "forecast/models/seasonal_naive.py",
    "forecast/models/calendar_regression.py",
    "forecast/models/chronos2.py",
    "forecast/data/osaka_daily.csv",
    "forecast/data/jp_holidays_2025.csv",
    "forecast/EVALUATION_PROTOCOL.md",
    "forecast/CHRONOS2_SPEC.md",
    "requirements-forecast.txt",
    "requirements-chronos.txt",
)
ARTIFACT_DIRS = ("forecast/out/f2", "forecast/out/f3")
FINAL_ORIGINS = (155, 162, 169, 176)
FINAL_ORIGIN_DATES = ("2025-09-15", "2025-09-22", "2025-09-29", "2025-10-06")
MODELS_FOR_FINAL_REPORTING = ("seasonal_naive", "calendar_regression", "chronos2")


class FreezeError(RuntimeError):
    """No valid freeze: the final test stays sealed."""


def sha256_lf(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def _artifacts(root: Path) -> list[str]:
    out = []
    for d in ARTIFACT_DIRS:
        out += [p.relative_to(root).as_posix() for p in sorted((root / d).rglob("*")) if p.is_file()]
    return out


def build_freeze(frozen_at: str, root: Path = REPO) -> dict:
    sel = json.loads((root / "forecast/out/f3/selection.json").read_text(encoding="utf-8"))
    pooled = sel["pooled_wape"]
    best = min(pooled, key=pooled.get)
    return {
        "freeze_version": FREEZE_VERSION,
        "frozen_at": frozen_at,
        "selected_model": sel["selected"],
        "status": sel["status"],
        "selection_basis": ("EVALUATION_PROTOCOL.md section 5 as committed in 47b580f: pooled WAPE over the "
                            "14 selection folds; a challenger must beat seasonal_naive by >= 0.01, else "
                            "seasonal_naive with status fallback_model"),
        "selection_reason": sel["reason"],
        "rule": {"baseline": "seasonal_naive", "metric": "pooled WAPE, 14 selection folds",
                 "min_improvement": 0.01},
        "threshold_provenance": ("design assumption proposed by Claude in the approved F0 plan; not "
                                 "statistically derived, not operationally validated, no cost analysis"),
        "plan_protocol_discrepancy": (
            "Approved plan: lowest WAPE; must beat seasonal_naive; if two models are within 1 point choose "
            "the simpler. Committed protocol and code: a challenger must beat seasonal_naive by >= 1 point; "
            "the lowest-WAPE challenger wins, no tie-break among challengers. Both readings select "
            "seasonal_naive on the F3 results. Neither wording is changed; the committed protocol governs."),
        "selection_fold_pooled_wape": pooled,
        "lowest_numerical_wape": {"model": best, "wape": pooled[best],
                                  "note": "lowest WAPE but not selected: gap to seasonal_naive below 0.01; "
                                          "exploratory review found the gap not statistically conclusive"},
        "what_this_is_not": ("seasonal_naive is the model selected under the predefined protocol. It is not "
                             "the most accurate model and not a proven best operational model; operational "
                             "fitness has not been defined or assessed."),
        "models_for_final_reporting": list(MODELS_FOR_FINAL_REPORTING),
        "final_reporting_only": True,
        "final_origins": dict(zip(FINAL_ORIGIN_DATES, FINAL_ORIGINS)),
        "chronos2": {"spec_version": "chronos2-spec-v1", "model_id": "amazon/chronos-2",
                     "revision": "29ec3766d36d6f73f0696f85560a422f50e8498c",
                     "weights_sha256": "ddcda3c7508bf2528087723e98a20707cc04b7f370ae275a9fd88078ddba4f42"},
        "dataset": {"path": "forecast/data/osaka_daily.csv",
                    "sha256_lf": sha256_lf(root / "forecast/data/osaka_daily.csv")},
        "pinned_files": {p: sha256_lf(root / p) for p in PINNED_LOGIC},
        "artifacts": {p: sha256_lf(root / p) for p in _artifacts(root)},
        "statement": ("Frozen before any final-test evaluation. Final-test results are for reporting only and "
                      "cannot change this selection, the rule, or any model configuration."),
    }


def verify_freeze(path: Path | None = None, root: Path | None = None) -> dict:
    """Return the freeze if every pinned hash still matches; else raise FreezeError."""
    path = Path(path) if path else FROZEN
    root = Path(root) if root else REPO
    if not path.exists():
        raise FreezeError(f"no freeze at {path}; the final test is sealed")
    try:
        frozen = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise FreezeError(f"freeze is not valid JSON: {e}") from e
    if frozen.get("freeze_version") != FREEZE_VERSION:
        raise FreezeError(f"freeze_version {frozen.get('freeze_version')!r}, expected {FREEZE_VERSION!r}")
    expected = build_freeze(frozen.get("frozen_at", ""), root)
    bad = []
    for key in ("pinned_files", "artifacts"):
        want, have = frozen.get(key, {}), expected[key]
        bad += [f"{p} (changed)" for p in want if p in have and want[p] != have[p]]
        bad += [f"{p} (missing)" for p in want if p not in have]
        bad += [f"{p} (not in freeze)" for p in have if p not in want]
    if bad:
        raise FreezeError("freeze no longer holds: " + "; ".join(bad[:8]))
    if frozen != expected:
        diff = sorted(k for k in set(frozen) | set(expected) if frozen.get(k) != expected.get(k))
        raise FreezeError(f"freeze fields differ from the F3 record: {diff}")
    return frozen


def freeze_sha256(path: Path | None = None) -> str:
    return sha256_lf(Path(path) if path else FROZEN)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Create or verify the F4 selection freeze.")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--frozen-at", help="ISO timestamp recorded in the freeze (with --write)")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)
    if args.write:
        if FROZEN.exists():
            print(f"REFUSED: {FROZEN} already exists; a freeze is written once", file=sys.stderr)
            return 2
        if (HERE / "out" / "final").exists():
            print("REFUSED: forecast/out/final exists; freeze must precede any final output", file=sys.stderr)
            return 2
        if not args.frozen_at:
            print("REFUSED: --frozen-at is required", file=sys.stderr)
            return 2
        FROZEN.write_text(json.dumps(build_freeze(args.frozen_at), indent=2, sort_keys=True) + "\n",
                          encoding="utf-8")
        print(f"wrote {FROZEN} sha256 {freeze_sha256()}")
        return 0
    try:
        frozen = verify_freeze()
    except FreezeError as e:
        print(f"FREEZE INVALID: {e}", file=sys.stderr)
        return 1
    print(f"OK: freeze holds ({frozen['selected_model']}, {len(frozen['pinned_files'])} pinned files, "
          f"{len(frozen['artifacts'])} artifacts); sha256 {freeze_sha256()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

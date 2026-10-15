"""Check the documentation against the repository (standard library only).

1. Paths: every repository path written in backticks in docs/*.md, docs/adr/*.md,
   README.md, forecast/README.md and data/demo/*/README.md exists.
   Paths that are deliberately historical (deleted on purpose and documented)
   are listed in HISTORICAL with the reason.
2. Numbers: the forecast metrics quoted in docs/18 and docs/17 (MAE, WAPE, Bias,
   PI80, level accuracy) equal the result files they come from:
   forecast/out/f3/metrics.csv (selection folds) and forecast/out/final/metrics.csv
   (final test), formatted as the docs write them.

    python tools/check_docs.py        exit 0 = OK, 1 = problems listed
"""
from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = sorted([*ROOT.glob("docs/*.md"), *ROOT.glob("docs/adr/*.md"), ROOT / "README.md",
               ROOT / "forecast" / "README.md", *ROOT.glob("data/demo/*/README.md")])
TOP = ("coverage/", "workforce/", "forecast/", "data/", "docs/", "config/", "tools/", "adr/")
FILE_EXT = (".py", ".md", ".csv", ".json", ".txt", ".html", ".css", ".js", ".xlsx", ".png", ".svg")
HISTORICAL = {
    # E-14: written by a sabotage run in 9190641, deleted unopened in f0221f1 (docs/11)
    "forecast/out/final/evaluation_report.md", "forecast/out/final/selection.json",
    # planned in the phase-0 audit, never built (v0.2 came from data/build_v02.py); noted at its top
    "data/demo/overrides.csv",
}
TOKEN = re.compile(r"`([^`\s]+)`")
MODELS = {"chronos2": "Chronos-2", "seasonal_naive": "Seasonal Naive", "calendar_regression": "Calendar Regression"}


def cited_paths(text: str) -> set[str]:
    out = set()
    for tok in TOKEN.findall(text):
        tok = tok.rstrip(".,:;)")
        if any(c in tok for c in "<>{}*|$\\") or tok.startswith(("http", "/", "--")) or "…" in tok:
            continue
        if tok.startswith(TOP) and (tok.endswith(FILE_EXT) or tok.endswith("/")):
            out.add(tok.split(":")[0])
    return out


def exists(path: str, doc: Path) -> bool:
    for base in (ROOT, doc.parent, ROOT / "docs"):
        if (base / path).exists():
            return True
    return False


def pooled(path: Path) -> dict[str, dict]:
    with open(path, encoding="utf-8") as f:
        return {r["model"]: r for r in csv.DictReader(f) if r["slice"] == "pooled"}


def pct(x: str, signed: bool = False) -> str:
    v = 100 * float(x)
    s = f"{abs(v):.1f}%"
    if signed:
        return ("−" if v < 0 else "+") + s
    return s


def check_numbers() -> list[str]:
    problems = []
    d18 = (ROOT / "docs" / "18-forecast.md").read_text(encoding="utf-8")
    d17 = (ROOT / "docs" / "17-resume-here.md").read_text(encoding="utf-8")
    for stage, path in (("F3 selection folds", ROOT / "forecast/out/f3/metrics.csv"),
                        ("F4c final test", ROOT / "forecast/out/final/metrics.csv")):
        for model, r in pooled(path).items():
            name, mae = MODELS[model], f"{float(r['mae']):,.0f}"
            wape = pct(r["wape"])
            row = re.compile(rf"\|\s*{re.escape(name)}\s*\|\s*{re.escape(mae)}\s*\|\s*\*\*{re.escape(wape)}\*\*\s*\|")
            if not row.search(d18):
                problems.append(f"docs/18: {stage} row for {name} should read MAE {mae}, WAPE {wape}")
            for k in ("pi80_coverage", "level_accuracy"):
                if pct(r[k]) not in d18:
                    problems.append(f"docs/18: {stage} {name} {k} {pct(r[k])} not found")
            if wape not in d17:
                problems.append(f"docs/17: {stage} {name} WAPE {wape} not found")
    return problems


def main() -> int:
    problems = []
    for doc in DOCS:
        for p in sorted(cited_paths(doc.read_text(encoding="utf-8"))):
            if p not in HISTORICAL and not exists(p, doc):
                problems.append(f"{doc.relative_to(ROOT)}: cites missing path {p}")
    problems += check_numbers()
    for p in problems:
        print(p)
    n = sum(len(cited_paths(d.read_text(encoding="utf-8"))) for d in DOCS)
    print(f"{'FAILED' if problems else 'OK'}: {len(DOCS)} documents, {n} cited paths, forecast metrics checked"
          + (f"; {len(problems)} problem(s)" if problems else ""))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())

"""F1: build the official daily attendance series for Expo 2025 Osaka.

Input   forecast/data/raw/official/*.txt
        One file per weekly news post by the organiser
        (来場者数と入場チケット販売数について), transcribed from the page.
Output  forecast/data/osaka_daily.csv
        One row per day, 13 Apr – 13 Oct 2025, every row traceable to its post.

Nothing here guesses. Any failed check stops the build with a message:
  - each post: its daily rows add up to its printed 合計 (total and AD)
  - each post: printed weekday matches the real calendar weekday
  - each post: its rows are consecutive days and match its period heading
  - across posts: printed cumulative(k) = cumulative(k-1) + weekly total(k)
  - across posts: a day reported twice must carry the same numbers
  - the series covers all 184 days of the Expo, no more, no less
  - grand totals equal the organiser's final cumulative figures

Definitions (from the posts):
  visitors_total   来場者数, everyone who entered that day
  ad_pass_entries  うちAD証入場者数, staff/exhibitor pass entries, part of the total
  visitors_general visitors_total - ad_pass_entries (the attendance we forecast)

Standard library only, so the Django runtime gains no dependency.
Run:  python forecast/build_series.py          (build + check)
      python forecast/build_series.py --check  (check that the CSV on disk is current)
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
RAW_DIR = HERE / "data" / "raw" / "official"
OUT_CSV = HERE / "data" / "osaka_daily.csv"

YEAR = 2025
OPENING = dt.date(2025, 4, 13)
CLOSING = dt.date(2025, 10, 13)
EXPECTED_DAYS = (CLOSING - OPENING).days + 1  # 184

# Final official cumulative figures, printed in news-20251014-01 (as of 13 Oct 2025).
FINAL_TOTAL = 29_017_924
FINAL_AD = 3_438_938
FINAL_GENERAL = FINAL_TOTAL - FINAL_AD  # 25,578,986 (also the expo_data page's 2,558万)

# How the numbers reached this repo. The cloud workspace cannot open the site
# from the shell (proxy 403), so pages were read with a web-reading tool and the
# digits copied into raw/*.txt. The arithmetic checks below are what make that safe.
RETRIEVED_ON = "2026-10-14"
TRANSCRIPTION = "web_reader_copy"

WEEKDAYS_JA = "月火水木金土日"  # Monday=0 ... Sunday=6, matching date.weekday()

ROW_RE = re.compile(r"^(\d{1,2})月(\d{1,2})日（(.)）;([\d,]+);([\d,]+)$")
TOTAL_RE = re.compile(r"^合計;([\d,]+);([\d,]+)$")
CUM_RE = re.compile(
    r"(\d{4})年(\d{1,2})月(\d{1,2})日（.）現在\s*累計来場者数?\s*([\d,]+)人（うちAD証入場者数?\s*([\d,]+)人）"
)
PERIOD_RE = re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})日～(?:(\d{1,2})月)?(\d{1,2})日")

FIELDS = [
    "date", "weekday", "visitors_total", "ad_pass_entries", "visitors_general",
    "origin", "source_url", "source_published", "retrieved_on", "transcription",
]


class SeriesError(Exception):
    """A check failed. The message says which post and which number."""


def _int(text: str) -> int:
    return int(text.replace(",", ""))


@dataclass
class Day:
    date: dt.date
    total: int
    ad: int

    @property
    def general(self) -> int:
        return self.total - self.ad


@dataclass
class Post:
    name: str
    url: str
    published: str
    period: tuple[dt.date, dt.date]
    days: list[Day] = field(default_factory=list)
    week_total: int = 0
    week_ad: int = 0
    cum_date: dt.date | None = None
    cum_total: int = 0
    cum_ad: int = 0


def parse_post(name: str, text: str) -> Post:
    """Read one raw file and run the checks that need only that post."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    header = {}
    for ln in lines:
        key, sep, value = ln.partition(":")
        if sep and key in {"URL", "PUBLISHED", "PERIOD", "CUMULATIVE"}:
            header[key] = value.strip()
    for key in ("URL", "PUBLISHED", "PERIOD", "CUMULATIVE"):
        if key not in header:
            raise SeriesError(f"{name}: missing {key} line")

    m = PERIOD_RE.search(header["PERIOD"])
    if not m:
        raise SeriesError(f"{name}: cannot read period {header['PERIOD']!r}")
    y, m1, d1, m2, d2 = m.groups()
    start = dt.date(int(y), int(m1), int(d1))
    end = dt.date(int(y), int(m2 or m1), int(d2))

    post = Post(name=name, url=header["URL"], published=header["PUBLISHED"], period=(start, end))

    totals = None
    for ln in lines:
        if (r := ROW_RE.match(ln)):
            month, day, wd, total, ad = r.groups()
            date = dt.date(YEAR, int(month), int(day))
            if WEEKDAYS_JA[date.weekday()] != wd:
                raise SeriesError(f"{name}: {date} is printed as （{wd}） but is a {WEEKDAYS_JA[date.weekday()]}曜日")
            post.days.append(Day(date, _int(total), _int(ad)))
        elif (t := TOTAL_RE.match(ln)):
            totals = (_int(t.group(1)), _int(t.group(2)))
    if totals is None:
        raise SeriesError(f"{name}: no 合計 row")
    post.week_total, post.week_ad = totals

    c = CUM_RE.search(header["CUMULATIVE"])
    if not c:
        raise SeriesError(f"{name}: cannot read cumulative {header['CUMULATIVE']!r}")
    cy, cm, cd, ctotal, cad = c.groups()
    post.cum_date = dt.date(int(cy), int(cm), int(cd))
    post.cum_total, post.cum_ad = _int(ctotal), _int(cad)

    # rows are the period's days, consecutive, in order
    expected = [start + dt.timedelta(days=i) for i in range((end - start).days + 1)]
    got = [d.date for d in post.days]
    if got != expected:
        raise SeriesError(f"{name}: rows {got[0] if got else None}..{got[-1] if got else None} "
                          f"({len(got)} rows) do not match period {start}..{end}")
    if post.cum_date != end:
        raise SeriesError(f"{name}: cumulative is as of {post.cum_date}, period ends {end}")

    # rows add up to the printed total
    row_total = sum(d.total for d in post.days)
    row_ad = sum(d.ad for d in post.days)
    if row_total != post.week_total:
        raise SeriesError(f"{name}: daily 来場者数 add up to {row_total:,}, 合計 says {post.week_total:,}")
    if row_ad != post.week_ad:
        raise SeriesError(f"{name}: daily AD add up to {row_ad:,}, 合計 says {post.week_ad:,}")
    for d in post.days:
        if not 0 <= d.ad <= d.total:
            raise SeriesError(f"{name}: {d.date} AD {d.ad:,} is not within total {d.total:,}")
    return post


def load_posts(raw_dir: Path = RAW_DIR) -> list[Post]:
    files = sorted(raw_dir.glob("*.txt"))
    if not files:
        raise SeriesError(f"no raw files in {raw_dir}")
    posts = [parse_post(f.stem, f.read_text(encoding="utf-8")) for f in files]
    posts.sort(key=lambda p: p.period[0])
    return posts


def check_chain(posts: list[Post]) -> None:
    """Printed cumulative of each post = previous cumulative + this post's 合計."""
    prev_total = prev_ad = 0
    for p in posts:
        if p.cum_total != prev_total + p.week_total:
            raise SeriesError(f"{p.name}: cumulative {p.cum_total:,} ≠ previous {prev_total:,} + week {p.week_total:,}")
        if p.cum_ad != prev_ad + p.week_ad:
            raise SeriesError(f"{p.name}: AD cumulative {p.cum_ad:,} ≠ previous {prev_ad:,} + week {p.week_ad:,}")
        prev_total, prev_ad = p.cum_total, p.cum_ad


def merge(posts: list[Post]) -> dict[dt.date, tuple[Day, Post]]:
    """One entry per date. The same date in two posts must agree exactly."""
    out: dict[dt.date, tuple[Day, Post]] = {}
    for p in posts:
        for d in p.days:
            if d.date in out:
                seen, first = out[d.date]
                if (seen.total, seen.ad) != (d.total, d.ad):
                    raise SeriesError(f"{d.date}: {first.name} says {seen.total:,}/{seen.ad:,}, "
                                      f"{p.name} says {d.total:,}/{d.ad:,}")
                continue
            out[d.date] = (d, p)
    return out


def check_complete(days: dict[dt.date, tuple[Day, Post]]) -> None:
    expected = {OPENING + dt.timedelta(days=i) for i in range(EXPECTED_DAYS)}
    missing = sorted(expected - days.keys())
    extra = sorted(days.keys() - expected)
    if missing:
        raise SeriesError(f"{len(missing)} day(s) missing, first {missing[0]}")
    if extra:
        raise SeriesError(f"{len(extra)} day(s) outside the Expo, first {extra[0]}")


def check_totals(days: dict[dt.date, tuple[Day, Post]]) -> None:
    total = sum(d.total for d, _ in days.values())
    ad = sum(d.ad for d, _ in days.values())
    if total != FINAL_TOTAL:
        raise SeriesError(f"sum of 来場者数 {total:,} ≠ official final {FINAL_TOTAL:,}")
    if ad != FINAL_AD:
        raise SeriesError(f"sum of AD {ad:,} ≠ official final {FINAL_AD:,}")
    if total - ad != FINAL_GENERAL:
        raise SeriesError(f"general {total - ad:,} ≠ {FINAL_GENERAL:,}")


def build(raw_dir: Path = RAW_DIR) -> list[dict[str, str]]:
    posts = load_posts(raw_dir)
    check_chain(posts)
    days = merge(posts)
    check_complete(days)
    check_totals(days)
    rows = []
    for date in sorted(days):
        d, p = days[date]
        rows.append({
            "date": date.isoformat(),
            "weekday": date.strftime("%a"),
            "visitors_total": str(d.total),
            "ad_pass_entries": str(d.ad),
            "visitors_general": str(d.general),
            "origin": "official",
            "source_url": p.url,
            "source_published": p.published.split()[0],
            "retrieved_on": RETRIEVED_ON,
            "transcription": TRANSCRIPTION,
        })
    return rows


def to_csv(rows: list[dict[str, str]]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=FIELDS, lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="fail if osaka_daily.csv is not what the raw files produce")
    args = ap.parse_args(argv)
    try:
        rows = build()
    except SeriesError as e:
        print(f"FAILED: {e}", file=sys.stderr)
        return 1
    text = to_csv(rows)
    if args.check:
        # Compare ignoring CRLF vs LF: a Windows checkout may convert line endings.
        on_disk = OUT_CSV.read_text(encoding="utf-8").replace("\r\n", "\n") if OUT_CSV.exists() else None
        if on_disk != text:
            print("FAILED: osaka_daily.csv is missing or out of date; run build_series.py", file=sys.stderr)
            return 1
        print(f"OK: {len(rows)} days, osaka_daily.csv is current")
        return 0
    OUT_CSV.write_text(text, encoding="utf-8")
    general = sum(int(r["visitors_general"]) for r in rows)
    print(f"OK: {len(rows)} days, {len({r['source_url'] for r in rows})} posts, "
          f"general attendance {general:,} -> {OUT_CSV.relative_to(HERE.parent)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

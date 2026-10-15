"""Load the official series (F1 output) and the holiday calendar.

Everything the models know comes through here:
  y          visitors_general per day, day 0 = 13 Apr 2025 (numpy int array)
  dates      the calendar date of each day index
  holidays   set of dates that are Japanese national or substitute holidays
"""
from __future__ import annotations

import csv
import datetime as dt
from dataclasses import dataclass
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
SERIES_CSV = HERE / "data" / "osaka_daily.csv"
HOLIDAYS_CSV = HERE / "data" / "jp_holidays_2025.csv"


@dataclass(frozen=True)
class Series:
    dates: tuple[dt.date, ...]
    y: np.ndarray            # visitors_general, int64
    holidays: frozenset[dt.date]

    def __len__(self) -> int:
        return len(self.dates)

    def upto(self, as_of: int) -> np.ndarray:
        """Days 0..as_of inclusive: all a model may see at that origin."""
        return self.y[: as_of + 1].copy()


def load(series_csv: Path = SERIES_CSV, holidays_csv: Path = HOLIDAYS_CSV) -> Series:
    with open(series_csv, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        if r["origin"] != "official":
            raise ValueError(f"{r['date']}: origin {r['origin']!r}; only official data may train or evaluate")
    dates = tuple(dt.date.fromisoformat(r["date"]) for r in rows)
    for a, b in zip(dates, dates[1:]):
        if (b - a).days != 1:
            raise ValueError(f"series is not consecutive between {a} and {b}")
    y = np.array([int(r["visitors_general"]) for r in rows], dtype=np.int64)
    with open(holidays_csv, encoding="utf-8") as f:
        holidays = frozenset(dt.date.fromisoformat(r["date"]) for r in csv.DictReader(f))
    return Series(dates=dates, y=y, holidays=holidays)

"""Forecast models. Each exposes NAME and forecast(history, as_of, horizon, series) -> Forecast.

`history` is the attendance for days 0..as_of only (the harness cuts it), so a
model cannot see the future even by mistake. `series` gives the calendar
(dates, holidays), which is known in advance; models must not read series.y.
"""
from dataclasses import dataclass, field

import numpy as np

WINDOW_DAYS = 56  # protocol §3: both models look at the last 8 weeks


@dataclass
class Forecast:
    p10: np.ndarray
    p50: np.ndarray
    p90: np.ndarray
    clipped: int = 0                      # how many quantiles were moved to keep p10<=p50<=p90
    notes: list[str] = field(default_factory=list)


def ordered(p10, p50, p90, notes=None) -> Forecast:
    """Protocol §3.3: keep p10 <= p50 <= p90, count the corrections, round at the end."""
    lo = np.minimum(p10, p50)
    hi = np.maximum(p90, p50)
    clipped = int(np.sum(lo != p10) + np.sum(hi != p90))
    return Forecast(np.rint(lo), np.rint(p50), np.rint(hi), clipped, list(notes or []))

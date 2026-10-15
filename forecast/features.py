"""Calendar features for the regression (protocol §3.2).

Only things known for any future date go in here: weekday, the national holiday
calendar (fixed by law years ahead), and the day number. Nothing derived from
attendance, weather or ticket sales.
"""
from __future__ import annotations

import datetime as dt

import numpy as np

# Monday is the baseline (no column); one column per other weekday.
DOW_COLUMNS = ("dow_tue", "dow_wed", "dow_thu", "dow_fri", "dow_sat", "dow_sun")
COLUMNS = DOW_COLUMNS + ("holiday", "t")


def calendar_matrix(day_indices, dates, holidays) -> np.ndarray:
    """One row per day index: [6 weekday dummies, holiday, t]."""
    rows = []
    for i in day_indices:
        d: dt.date = dates[i]
        dow = [1.0 if d.weekday() == k else 0.0 for k in range(1, 7)]  # Tue..Sun
        rows.append(dow + [1.0 if d in holidays else 0.0, float(i)])
    return np.array(rows, dtype=float)

"""Seasonal Naive (protocol §3.1): same weekday last week.

p50(d)  = y(d - 7)
p10/p90 = p50 x the 10th/90th percentile of week-on-week ratios y_t / y_(t-7)
          over the last 56 days up to as_of.
"""
import numpy as np

from . import WINDOW_DAYS, Forecast, ordered

NAME = "seasonal_naive"


def forecast(history: np.ndarray, as_of: int, horizon: int, series=None) -> Forecast:
    if len(history) != as_of + 1:
        raise ValueError("history must be days 0..as_of")
    if horizon > 7:
        raise ValueError("seasonal naive with one week of lag supports horizon <= 7")
    days = np.arange(as_of + 1, as_of + 1 + horizon)
    p50 = history[days - 7].astype(float)

    t = np.arange(max(7, as_of + 1 - WINDOW_DAYS), as_of + 1)
    ratios = history[t] / history[t - 7]
    q10, q90 = np.quantile(ratios, [0.10, 0.90])
    return ordered(p50 * q10, p50, p50 * q90)

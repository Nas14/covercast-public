"""Metrics and pressure levels (protocol §4). Plain numpy, one definition each."""
from __future__ import annotations

import numpy as np

LEVELS = ("normal", "high", "peak")


def mae(y, p50) -> float:
    return float(np.mean(np.abs(y - p50)))


def wape(y, p50) -> float:
    """Sum of absolute errors / sum of actuals. Pooled, never an average of fold WAPEs."""
    return float(np.sum(np.abs(y - p50)) / np.sum(y))


def bias(y, p50) -> float:
    """Signed: negative means the forecast was too low."""
    return float(np.sum(p50 - y) / np.sum(y))


def coverage(y, p10, p90) -> float:
    return float(np.mean((p10 <= y) & (y <= p90)))


def thresholds(reference, low_q=0.75, high_q=0.90) -> tuple[float, float]:
    """Level cut-points from attendance up to as_of only (F-D3: experimental)."""
    lo, hi = np.quantile(np.asarray(reference, dtype=float), [low_q, high_q])
    return float(lo), float(hi)


def level(value, cuts) -> str:
    lo, hi = cuts
    if value >= hi:
        return "peak"
    if value >= lo:
        return "high"
    return "normal"


def level_accuracy(actual_levels, predicted_levels) -> float:
    a, p = list(actual_levels), list(predicted_levels)
    return float(np.mean([x == y for x, y in zip(a, p)]))


def summary(y, p10, p50, p90) -> dict:
    y, p10, p50, p90 = (np.asarray(v, dtype=float) for v in (y, p10, p50, p90))
    return {
        "n_days": int(len(y)),
        "mae": mae(y, p50),
        "wape": wape(y, p50),
        "bias": bias(y, p50),
        "pi80_coverage": coverage(y, p10, p90),
    }

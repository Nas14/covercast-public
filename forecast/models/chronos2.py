"""Chronos-2 adapter (forecast/CHRONOS2_SPEC.md, chronos2-spec-v1).

The pipeline is passed in, so this module imports neither torch nor chronos:
tests drive it with a fake pipeline, and the real one is loaded only by
run_chronos_local.py on a machine that has the model.
"""
from __future__ import annotations

import numpy as np

from . import Forecast, ordered

NAME = "chronos2"
SPEC_VERSION = "chronos2-spec-v1"
MODEL_ID = "amazon/chronos-2"
MODEL_REVISION = "29ec3766d36d6f73f0696f85560a422f50e8498c"
# SHA256 of model.safetensors at that revision, as shown on its Hugging Face file page (read twice, 14 Oct 2026)
WEIGHTS_SHA256 = "ddcda3c7508bf2528087723e98a20707cc04b7f370ae275a9fd88078ddba4f42"
QUANTILES = (0.1, 0.5, 0.9)


def _to_numpy(x) -> np.ndarray:
    if hasattr(x, "detach"):          # torch.Tensor
        x = x.detach().cpu().numpy()
    return np.asarray(x, dtype=float)


class Chronos2Forecaster:
    """Same interface as the other models: NAME and forecast(history, as_of, horizon, series)."""

    NAME = NAME

    def __init__(self, pipeline):
        self.pipeline = pipeline

    def forecast(self, history: np.ndarray, as_of: int, horizon: int, series=None) -> Forecast:
        if len(history) != as_of + 1:
            raise ValueError("history must be days 0..as_of")
        context = np.asarray(history, dtype=np.float32)          # univariate, whole history (spec)
        quantiles, _mean = self.pipeline.predict_quantiles(
            [context], prediction_length=horizon, quantile_levels=list(QUANTILES))
        arr = _to_numpy(quantiles[0])
        if arr.ndim == 3:                                          # (n_variates=1, horizon, 3)
            arr = arr[0]
        if arr.shape != (horizon, len(QUANTILES)):
            raise ValueError(f"unexpected Chronos-2 output shape {arr.shape}, wanted ({horizon}, 3)")
        if not np.all(np.isfinite(arr)):
            raise ValueError("Chronos-2 returned non-finite values")
        return ordered(arr[:, 0], arr[:, 1], arr[:, 2])

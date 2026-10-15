"""Calendar Regression (protocol §3.2).

ln(y) ~ weekday dummies + holiday + t, OLS on the last 56 days up to as_of.
p50     = exp(prediction)            (the median under a log model)
p10/p90 = exp(prediction + 10th/90th percentile of the window's residuals)
A column that is constant inside the window (e.g. no holiday in it) is dropped,
so its effect is zero; the note says which.
"""
import numpy as np
from sklearn.linear_model import LinearRegression

from features import COLUMNS, calendar_matrix

from . import WINDOW_DAYS, Forecast, ordered

NAME = "calendar_regression"


def forecast(history: np.ndarray, as_of: int, horizon: int, series) -> Forecast:
    if len(history) != as_of + 1:
        raise ValueError("history must be days 0..as_of")
    train = np.arange(max(0, as_of + 1 - WINDOW_DAYS), as_of + 1)
    future = np.arange(as_of + 1, as_of + 1 + horizon)

    X_train = calendar_matrix(train, series.dates, series.holidays)
    X_future = calendar_matrix(future, series.dates, series.holidays)
    keep = X_train.std(axis=0) > 0
    notes = [f"dropped {c} (constant in window)" for c, k in zip(COLUMNS, keep) if not k]

    target = np.log(history[train].astype(float))
    model = LinearRegression().fit(X_train[:, keep], target)
    residuals = target - model.predict(X_train[:, keep])
    q10, q90 = np.quantile(residuals, [0.10, 0.90])

    pred = model.predict(X_future[:, keep])
    return ordered(np.exp(pred + q10), np.exp(pred), np.exp(pred + q90), notes)

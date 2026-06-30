"""Pure time-series momentum signal."""

from __future__ import annotations

import pandas as pd


def momentum_signal(features_df: pd.DataFrame, *, lookback: int = 20) -> pd.Series:
    close = features_df["close"]
    trailing_return = close / close.shift(lookback) - 1.0
    signal = trailing_return.map(lambda value: 1 if value > 0.0 else (-1 if value < 0.0 else 0))
    signal = signal.fillna(0).astype("int64")
    signal.name = "momentum_signal"
    return signal

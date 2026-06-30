"""Pure open-interest extreme mean-reversion signal.

Committed rule (do not change after seeing results):
  On 4h bars, compute sum_open_interest_value z-score over a trailing
  OI_LOOKBACK_BARS window.  When |z| >= threshold_z (an honest extreme,
  not a near-50% coin flip), signal AGAINST the prior price direction:
    short (-1) if z extreme AND 6-bar return > 0
    long  (+1) if z extreme AND 6-bar return < 0
    flat  ( 0) otherwise
  Threshold must be >= 1.5; it never gets lowered to manufacture trades.
"""

from __future__ import annotations

import pandas as pd


def oi_extreme_signal(
    features_df: pd.DataFrame,
    *,
    oi_column: str = "sum_open_interest_value",
    price_column: str = "close",
    lookback: int = 35,
    threshold_z: float = 1.5,
    price_lookback: int = 6,
) -> pd.Series:
    if threshold_z < 1.5:
        raise ValueError(f"threshold_z={threshold_z} < 1.5; this is not an extreme and is forbidden by the signal spec.")

    oi = features_df[oi_column]
    close = features_df[price_column]
    mean = oi.rolling(lookback, min_periods=lookback).mean()
    std = oi.rolling(lookback, min_periods=lookback).std(ddof=0)
    z_score = (oi - mean) / std.replace(0.0, pd.NA)
    price_direction = close / close.shift(price_lookback) - 1.0

    extreme = z_score.abs() >= threshold_z
    has_data = z_score.notna() & price_direction.notna()

    signal = pd.Series(0, index=features_df.index, dtype="int64", name="oi_extreme_signal")
    signal.loc[has_data & extreme & (price_direction > 0.0)] = -1
    signal.loc[has_data & extreme & (price_direction < 0.0)] = 1
    return signal

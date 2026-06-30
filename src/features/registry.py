"""
Feature registry for WS2.

Each registered feature is:
- a pure function
- explicitly versioned
- easy to look up by name
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np
import pandas as pd


FeatureFunction = Callable[[pd.DataFrame], pd.DataFrame]


@dataclass(frozen=True)
class RegisteredFeature:
    """Metadata and callable for one feature."""

    name: str
    version: str
    function: FeatureFunction


def add_log_return_v1(df: pd.DataFrame) -> pd.DataFrame:
    """Pure feature: one-bar log return from close to close."""

    result = pd.DataFrame(index=df.index)
    result["log_return_1"] = np.log(df["close"] / df["close"].shift(1))
    return result


def add_quote_volume_ma_20_v1(df: pd.DataFrame) -> pd.DataFrame:
    """Pure feature: trailing 20-bar mean of quote volume."""

    result = pd.DataFrame(index=df.index)
    result["quote_volume_ma_20"] = df["quote_volume"].rolling(window=20, min_periods=20).mean()
    return result


FEATURE_REGISTRY: dict[str, RegisteredFeature] = {
    "log_return_1": RegisteredFeature(
        name="log_return_1",
        version="1.0.0",
        function=add_log_return_v1,
    ),
    "quote_volume_ma_20": RegisteredFeature(
        name="quote_volume_ma_20",
        version="1.0.0",
        function=add_quote_volume_ma_20_v1,
    ),
}

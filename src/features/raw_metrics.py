"""
Helpers for reading raw Binance metrics files with point-in-time discipline.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from config.settings import RAW_DATA_DIR


METRIC_COLUMNS = [
    "create_time",
    "symbol",
    "sum_open_interest",
    "sum_open_interest_value",
    "count_toptrader_long_short_ratio",
    "sum_toptrader_long_short_ratio",
    "count_long_short_ratio",
    "sum_taker_long_short_vol_ratio",
]


def load_metrics(symbol: str, start: str | pd.Timestamp, end: str | pd.Timestamp) -> pd.DataFrame:
    """
    Load raw 5-minute Binance metrics for one symbol and time range.

    Returned rows are indexed by the publish timestamp, which is exactly the
    information boundary we must respect in later as-of joins.
    """

    root = RAW_DATA_DIR / "metrics"
    files = sorted(root.rglob(f"{symbol}-metrics-*.csv"))
    if not files:
        raise FileNotFoundError(f"No metrics files found for {symbol}")

    frames: list[pd.DataFrame] = []
    for path in files:
        frame = pd.read_csv(path)
        if list(frame.columns) != METRIC_COLUMNS:
            frame.columns = METRIC_COLUMNS
        frames.append(frame)

    df = pd.concat(frames, ignore_index=True)
    df["create_time"] = pd.to_datetime(df["create_time"], utc=True, errors="coerce")
    numeric_columns = [column for column in METRIC_COLUMNS if column not in {"create_time", "symbol"}]
    for column in numeric_columns:
        df[column] = pd.to_numeric(df[column], errors="coerce")

    df = df.dropna(subset=["create_time"]).sort_values("create_time")
    df = df.set_index("create_time")

    start_ts = _coerce_timestamp(start)
    end_ts = _coerce_timestamp(end)
    return df.loc[start_ts:end_ts]


def _coerce_timestamp(value: str | pd.Timestamp) -> pd.Timestamp:
    """Normalize timestamps to UTC for consistent slicing."""

    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        return ts.tz_localize("UTC")
    return ts.tz_convert("UTC")

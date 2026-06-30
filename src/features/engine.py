"""
WS2 feature engine.

This layer:
- reads immutable raw/parquet inputs
- computes versioned pure features on demand
- joins metrics with strict point-in-time semantics
- caches feature outputs using a content-derived key
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd

from src.data.featurestore import FeatureStore
from src.features.cache import FeatureCache, build_cache_key
from src.features.raw_metrics import load_metrics
from src.features.registry import FEATURE_REGISTRY


class FeatureEngine:
    """Compute and cache versioned feature frames."""

    def __init__(
        self,
        *,
        store: FeatureStore | None = None,
        cache: FeatureCache | None = None,
    ) -> None:
        self.store = store or FeatureStore()
        self.cache = cache or FeatureCache()

    def compute_feature(
        self,
        *,
        feature_name: str,
        symbol: str,
        start: str,
        end: str,
        timeframe: str,
    ) -> pd.DataFrame:
        """Compute one registered feature and reuse cache when possible."""

        registered = FEATURE_REGISTRY[feature_name]
        data_version = self.compute_data_version(symbol=symbol, start=start, end=end, timeframe=timeframe)
        cache_key = build_cache_key(
            feature_name=registered.name,
            feature_version=registered.version,
            data_version=data_version,
            symbol=symbol,
            timeframe=timeframe,
        )

        if self.cache.has(cache_key):
            cached = self.cache.load(cache_key)
            cached.index = pd.to_datetime(cached.index, utc=True)
            cached.index.name = "open_time"
            return cached

        base = self.load_point_in_time_bars(
            symbol=symbol,
            start=start,
            end=end,
            timeframe=timeframe,
        )
        features = registered.function(base)
        features.index.name = "open_time"
        self.cache.save(cache_key, features)
        return features

    def compute_data_version(
        self,
        *,
        symbol: str,
        start: str,
        end: str,
        timeframe: str,
    ) -> str:
        """
        Fingerprint the data slice used to compute a feature.

        We hash the actual loaded candle frame. That keeps the result tied to the
        real inputs instead of only file timestamps.
        """

        base = self.load_point_in_time_bars(
            symbol=symbol,
            start=start,
            end=end,
            timeframe=timeframe,
        )
        payload = {
            "symbol": symbol,
            "timeframe": timeframe,
            "start": str(base.index.min()) if not base.empty else start,
            "end": str(base.index.max()) if not base.empty else end,
            "rows": len(base),
            "frame_hash": hashlib.sha256(
                pd.util.hash_pandas_object(base, index=True).values.tobytes()
            ).hexdigest(),
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    def attach_metrics_asof(
        self,
        *,
        symbol: str,
        start: str,
        end: str,
        timeframe: str,
        publish_lag_minutes: int = 5,
    ) -> pd.DataFrame:
        """
        Join funding/OI-style metrics with strict point-in-time semantics.

        We shift the metrics publish time forward by one publish interval before
        using an as-of join. That means a bar can only see a metric row after it
        would have been observable in the real world.
        """

        candles = self.load_point_in_time_bars(
            symbol=symbol,
            start=start,
            end=end,
            timeframe=timeframe,
        ).copy()
        candles = candles.reset_index().rename(columns={"open_time": "bar_close_time"})
        candles = candles.sort_values("bar_close_time")

        metrics = load_metrics(symbol=symbol, start=start, end=end).copy()
        metrics = metrics.reset_index().rename(columns={"create_time": "metric_publish_time"})
        metrics["available_at"] = metrics["metric_publish_time"] + pd.Timedelta(minutes=publish_lag_minutes)
        metrics = metrics.sort_values("available_at")

        joined = pd.merge_asof(
            candles,
            metrics,
            left_on="bar_close_time",
            right_on="available_at",
            direction="backward",
            allow_exact_matches=True,
        )
        joined = joined.set_index("bar_close_time")
        joined.index.name = "open_time"
        return joined

    def load_point_in_time_bars(
        self,
        *,
        symbol: str,
        start: str,
        end: str,
        timeframe: str,
    ) -> pd.DataFrame:
        """
        Load bars and relabel them to the right edge (bar close time).

        The Phase 1 store currently labels rows by bar open time. WS2 shifts the
        index to bar close time so a row timestamp matches when the full bar would
        actually be knowable.
        """

        df = self.store.load(symbol=symbol, start=start, end=end, timeframe=timeframe).copy()
        df.index = df.index + _timeframe_delta(timeframe)
        df.index.name = "open_time"
        return df


def _timeframe_delta(timeframe: str) -> pd.Timedelta:
    """Map supported timeframes to one bar of elapsed time."""

    mapping = {
        "1m": pd.Timedelta(minutes=1),
        "5m": pd.Timedelta(minutes=5),
        "15m": pd.Timedelta(minutes=15),
        "1h": pd.Timedelta(hours=1),
        "4h": pd.Timedelta(hours=4),
        "1d": pd.Timedelta(days=1),
    }
    return mapping[timeframe]

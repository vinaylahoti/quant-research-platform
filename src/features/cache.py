"""
Disposable cache for WS2 feature outputs.

The cache is a speed optimization only. Raw data remains the source of truth.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[2]
FEATURE_CACHE_DIR = REPO_ROOT / "data" / "feature_cache"


def build_cache_key(
    *,
    feature_name: str,
    feature_version: str,
    data_version: str,
    symbol: str,
    timeframe: str,
) -> str:
    """Create a deterministic cache key from the feature identity."""

    joined = "|".join(
        [feature_name, feature_version, data_version, symbol, timeframe]
    )
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


class FeatureCache:
    """Tiny parquet-backed cache addressed by a content hash."""

    def __init__(self, cache_dir: Path | None = None) -> None:
        self.cache_dir = Path(cache_dir or FEATURE_CACHE_DIR)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def path_for(self, key: str) -> Path:
        """Return the file path used for one cache entry."""

        return self.cache_dir / f"{key}.parquet"

    def has(self, key: str) -> bool:
        """Check whether a cache entry already exists."""

        return self.path_for(key).exists()

    def load(self, key: str) -> pd.DataFrame:
        """Load a cached feature frame."""

        return pd.read_parquet(self.path_for(key))

    def save(self, key: str, df: pd.DataFrame) -> Path:
        """Persist a feature frame into the disposable cache."""

        path = self.path_for(key)
        df.to_parquet(path, index=True, compression="zstd")
        return path

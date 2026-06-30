"""
WS2 decision-gate test.

This proves two things:
1. the same (feature_version, data_version) returns byte-identical output
2. the as-of metrics join does not let a bar see future information
"""

from __future__ import annotations

from pathlib import Path
import hashlib
import sys

import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.features.engine import FeatureEngine


def _frame_digest(df: pd.DataFrame) -> str:
    """Create a stable hash of a DataFrame's values and index."""

    row_hashes = pd.util.hash_pandas_object(df, index=True).values.tobytes()
    return hashlib.sha256(row_hashes).hexdigest()


def run_ws2_decision_gate_test() -> str:
    """Run the WS2 determinism and lookahead checks on real data."""

    engine = FeatureEngine()

    first = engine.compute_feature(
        feature_name="quote_volume_ma_20",
        symbol="BTCUSDT",
        start="2025-01-01 00:00:00+00:00",
        end="2025-01-05 23:59:00+00:00",
        timeframe="15m",
    )
    second = engine.compute_feature(
        feature_name="quote_volume_ma_20",
        symbol="BTCUSDT",
        start="2025-01-01 00:00:00+00:00",
        end="2025-01-05 23:59:00+00:00",
        timeframe="15m",
    )

    digest_one = _frame_digest(first)
    digest_two = _frame_digest(second)
    assert digest_one == digest_two, "Feature output changed for the same feature/data version."

    joined = engine.attach_metrics_asof(
        symbol="BTCUSDT",
        start="2025-12-31 00:00:00+00:00",
        end="2025-12-31 02:00:00+00:00",
        timeframe="15m",
        publish_lag_minutes=5,
    )
    joined = joined.dropna(subset=["metric_publish_time"])
    assert not joined.empty, "Expected at least one joined metrics row."

    # Every bar must only see data that was actually available by that bar close.
    assert (joined["available_at"] <= joined.index).all(), "Lookahead detected in metrics as-of join."

    latest_gap_minutes = (
        (joined.index.to_series() - joined["metric_publish_time"]).dt.total_seconds() / 60.0
    ).min()
    assert latest_gap_minutes >= 5.0, "Metrics were visible too early."

    return (
        "WS2 decision gate passed | "
        f"deterministic_hash={digest_one[:12]} | "
        f"rows={len(first)} | "
        f"joined_rows={len(joined)} | "
        f"min_publish_gap_minutes={latest_gap_minutes:.1f}"
    )


if __name__ == "__main__":
    print(run_ws2_decision_gate_test())

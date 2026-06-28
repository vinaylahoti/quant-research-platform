"""
Real-data Phase 1 decision-gate test.

This test uses the actual parquet feature store under /data and proves the
steady-state `load()` helper can pull real symbol/timeframe slices quickly.
"""

from __future__ import annotations

from pathlib import Path
from time import perf_counter
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.data.featurestore import FeatureStore


TEST_CASES = [
    ("BTCUSDT", "2025-01-01 00:00:00+00:00", "2025-01-07 23:59:00+00:00", "15m"),
    ("ETHUSDT", "2024-06-01 00:00:00+00:00", "2024-06-30 23:59:00+00:00", "1h"),
    ("1000SHIBUSDT", "2025-03-01 00:00:00+00:00", "2025-03-31 23:59:00+00:00", "4h"),
]


def run_real_data_decision_gate_test() -> str:
    """Run the real-data load-speed checks and return a compact summary."""

    store = FeatureStore()
    results: list[str] = []

    for symbol, start, end, timeframe in TEST_CASES:
        # Warm the parquet engine once for each case before timing the steady-state load.
        warm_df = store.load(symbol=symbol, start=start, end=end, timeframe=timeframe)
        assert not warm_df.empty, f"Warm-up load returned no rows for {symbol} {timeframe}"

        started = perf_counter()
        df = store.load(symbol=symbol, start=start, end=end, timeframe=timeframe)
        elapsed = perf_counter() - started

        assert not df.empty, f"Expected rows for {symbol} {timeframe}"
        assert elapsed < 1.0, f"Decision gate failed for {symbol} {timeframe}: {elapsed:.6f}s"

        results.append(
            f"{symbol} {timeframe} rows={len(df)} elapsed_seconds={elapsed:.6f}"
        )

    return "Real-data decision gate passed | " + " | ".join(results)


if __name__ == "__main__":
    print(run_real_data_decision_gate_test())

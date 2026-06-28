"""
Quick Phase 1 decision-gate test.

This test is intentionally self-contained:
- it creates a tiny local raw kline file,
- builds the parquet feature store from that fixture,
- loads a timeframe slice, and
- checks that loading completes in under one second.

That keeps the test deterministic and fast while still exercising the real
Phase 1 code path.
"""

from __future__ import annotations

from pathlib import Path
from time import perf_counter
import shutil
import sys

import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.data.featurestore import FeatureStore, StorePaths


def _make_raw_fixture(base_dir: Path) -> Path:
    """Create a small but realistic Binance-style 1-minute kline CSV."""

    raw_dir = base_dir / "raw" / "klines" / "futures" / "um" / "monthly" / "klines" / "BTCUSDT" / "1m"
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_file = raw_dir / "BTCUSDT-1m-2025-01.csv"

    timestamps = pd.date_range("2025-01-01 00:00:00", periods=1_440, freq="1min", tz="UTC")
    rows: list[str] = []
    for i, ts in enumerate(timestamps):
        open_price = 100.0 + (i * 0.01)
        high_price = open_price + 0.2
        low_price = open_price - 0.2
        close_price = open_price + 0.05
        volume = 10.0 + (i % 10)
        quote_volume = volume * close_price
        taker_buy_base = volume * 0.55
        taker_buy_quote = taker_buy_base * close_price
        open_ms = int(ts.timestamp() * 1000)
        close_ms = open_ms + 59_999

        rows.append(
            ",".join(
                [
                    str(open_ms),
                    f"{open_price:.4f}",
                    f"{high_price:.4f}",
                    f"{low_price:.4f}",
                    f"{close_price:.4f}",
                    f"{volume:.4f}",
                    str(close_ms),
                    f"{quote_volume:.4f}",
                    "100",
                    f"{taker_buy_base:.4f}",
                    f"{taker_buy_quote:.4f}",
                    "0",
                ]
            )
        )

    raw_file.write_text("\n".join(rows), encoding="utf-8")
    return raw_file


def run_decision_gate_test() -> str:
    """Run the proof test and return a short human-readable summary."""

    temp_root = Path("tests/.tmp_phase1_gate")
    if temp_root.exists():
        shutil.rmtree(temp_root)

    _make_raw_fixture(temp_root)
    store = FeatureStore(
        StorePaths(
            raw_klines_dir=temp_root / "raw" / "klines",
            featurestore_dir=temp_root / "featurestore",
        )
    )

    written = store.build(symbols=["BTCUSDT"])

    # Warm up the parquet reader once so we measure the actual load helper
    # rather than one-time library startup cost inside pyarrow/pandas.
    store.load(
        symbol="BTCUSDT",
        start="2025-01-01 00:00:00+00:00",
        end="2025-01-01 23:59:00+00:00",
        timeframe="15m",
    )

    started = perf_counter()
    df = store.load(
        symbol="BTCUSDT",
        start="2025-01-01 00:00:00+00:00",
        end="2025-01-01 23:59:00+00:00",
        timeframe="15m",
    )
    elapsed = perf_counter() - started

    assert written, "Expected at least one parquet file to be written."
    assert not df.empty, "Expected the loaded DataFrame to contain candles."
    assert elapsed < 1.0, f"Decision gate failed: load took {elapsed:.6f}s"

    return (
        f"Decision gate passed | rows={len(df)} | columns={list(df.columns)} | "
        f"elapsed_seconds={elapsed:.6f}"
    )


if __name__ == "__main__":
    print(run_decision_gate_test())

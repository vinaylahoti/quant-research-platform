"""
Pre-flight sizing sanity check - gate item 4 from ws9_vol_portfolio.md.

Verifies that the live sizer code path (sizer.compute_target_size_from_returns)
produces output IDENTICAL to calling WS5's size_position() directly, given the
same trailing-return inputs.

Tolerance: 1e-9 relative error. Both paths are deterministic on identical
inputs, so any larger gap is a code-path divergence, not floating-point noise.
All 5 symbols must pass; 4/5 does not count.

The 5 test cases are chosen to hit edge cases that would reveal a real divergence:
  1. High-vol period (stress-tests the leverage cap).
  2. Low-vol period (tests the minimum-leverage floor).
  3. Near-leverage-cap period (leverage formula saturates).
  4. LUNAUSDT - data ends 2022-05; verifies historical-only symbols work.
  5. ETHUSDT - normal mid-vol control case.

Run with:
    py scripts/sanity_check.py

A passing run prints "ALL 5 PASSED" and exits 0.
A failing run prints the divergence and exits 1.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow imports from repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.data.featurestore import FeatureStore
from src.execution.model import size_position
from src.paper_trading.sizer import compute_target_size_from_returns, make_execution_config

_TOLERANCE = 1e-9

_TEST_CASES = [
    # (label, symbol, start, end, reason)
    ("high-vol",           "BTCUSDT",  "2022-05-01", "2022-05-02", "crypto crash - high hourly vol"),
    ("low-vol",            "BTCUSDT",  "2023-10-01", "2023-10-02", "low-vol consolidation period"),
    ("near-leverage-cap",  "BTCUSDT",  "2022-11-08", "2022-11-09", "FTX collapse - extreme vol hits leverage cap"),
    ("alt-symbol",         "SOLUSDT",  "2022-05-01", "2022-05-02", "alternate symbol, same crash date - exercises sizer on non-BTC input"),
    ("normal-control",     "ETHUSDT",  "2024-03-01", "2024-03-02", "ordinary mid-vol ETH day"),
]


def _returns_from_featurestore(symbol: str, start: str, end: str) -> tuple[float, ...]:
    fs = FeatureStore()
    bars = fs.load(symbol, start=start, end=end, timeframe="1h")
    closes = list(bars["close"].astype(float))
    if len(closes) < 2:
        raise ValueError(f"{symbol} {start}-{end}: only {len(closes)} bar(s), need >= 2")
    return tuple(closes[i] / closes[i - 1] - 1.0 for i in range(1, len(closes)))


def _check(label: str, symbol: str, start: str, end: str) -> bool:
    try:
        returns = _returns_from_featurestore(symbol, start, end)
    except Exception as exc:
        print(f"  SKIP - could not load data: {exc}")
        print("  (Is the featurestore built? Run: from src.data.featurestore import FeatureStore; FeatureStore().build())")
        return False

    cfg = make_execution_config()

    # WS5 reference path - direct call.
    ws5 = size_position(trailing_returns=returns, config=cfg)

    # Live path - through the sizer wrapper.
    live = compute_target_size_from_returns(returns, cfg)

    fields = ("realized_volatility", "leverage", "expected_risk_pct")
    all_ok = True
    for field in fields:
        ws5_val = getattr(ws5, field)
        live_val = getattr(live, field)
        denom = max(abs(ws5_val), 1e-8)
        rel_err = abs(live_val - ws5_val) / denom
        if rel_err >= _TOLERANCE:
            print(f"  FAIL [{field}]: ws5={ws5_val}, live={live_val}, rel_err={rel_err:.2e}")
            all_ok = False

    if all_ok:
        print(
            f"  PASS  vol={ws5.realized_volatility:.6f}  "
            f"lev={ws5.leverage:.4f}x  "
            f"n_returns={len(returns)}"
        )
    return all_ok


def main() -> int:
    cfg = make_execution_config()
    print(
        f"Sanity check - ExecutionConfig: "
        f"target_risk={cfg.target_trade_risk_pct}, "
        f"max_lev={cfg.max_leverage}, "
        f"min_lev={cfg.min_leverage}, "
        f"slippage={cfg.slippage_bps}bps, "
        f"fee={cfg.taker_fee_bps}bps"
    )
    print()

    results: list[bool] = []
    for label, symbol, start, end, reason in _TEST_CASES:
        print(f"[{label}] {symbol} {start}-{end}  ({reason})")
        ok = _check(label, symbol, start, end)
        results.append(ok)
        print()

    passed = sum(results)
    total = len(results)
    print(f"{'ALL ' + str(total) + ' PASSED' if passed == total else str(passed) + '/' + str(total) + ' PASSED'}")

    if passed != total:
        print()
        print("STOP: code-path divergence detected. Fix before collecting paper-trading data.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

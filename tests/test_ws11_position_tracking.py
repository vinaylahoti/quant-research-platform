"""
WS11 determinism test.

Given an identical sequence of target sizes and prices, replaying them twice
(with DB reconstructed between runs) must produce identical position states
and P&L — same discipline as every other component in this project.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from src.execution.model import ExecutionConfig
from src.paper_trading.logger import TradeLogger, TradeRecord
from src.paper_trading.position_tracker import PositionTracker


_CFG = ExecutionConfig(slippage_bps=2.0, taker_fee_bps=4.0)

# Deterministic tick sequence: (target_notional_usd, current_price)
_TICKS = [
    (1000.0, 50_000.0),   # open: buy 0.02 BTC at 50000 + slippage
    (1500.0, 51_000.0),   # increase
    (1500.0, 52_000.0),   # hold (same target, different price)
    (800.0,  52_000.0),   # decrease
    (0.0,    53_000.0),   # close
    (600.0,  53_000.0),   # open again
]

_SYMBOL = "BTCUSDT"


def _run_sequence(db_path: Path) -> list[dict]:
    """Run the tick sequence and return the fill result dicts."""
    results = []
    tracker = PositionTracker(db_path=db_path)
    logger = TradeLogger(db_path=db_path)

    for i, (target, price) in enumerate(_TICKS):
        fill = tracker.process_fill(
            symbol=_SYMBOL,
            target_size_notional=target,
            current_price=price,
            config=_CFG,
        )
        # Write to DB exactly as the scheduler would.
        ts = f"2026-01-01T{i:02d}:00:00+00:00"
        logger.append(TradeRecord(
            timestamp_decision=ts,
            timestamp_fill=ts,
            symbol=_SYMBOL,
            vol_estimate=0.01,
            target_size_notional=target,
            intended_fill_price=price,
            actual_fill_price=fill.fill_price,
            slippage_bps=2.0,
            fees_bps=4.0,
            latency_ms=100.0,
            error=None,
            universe_snapshot=[_SYMBOL],
            open_units_before=fill.open_units_before,
            open_units_after=fill.open_units_after,
            vwap_entry_price=fill.vwap_entry_price or None,
            realized_pnl_usd=fill.realized_pnl_usd,
            unrealized_pnl_usd=fill.unrealized_pnl_usd,
            fee_cost_usd=fill.fee_cost_usd,
            portfolio_value_usd=None,
            position_event=fill.position_event,
        ))
        logger.set_portfolio_value_for_tick(ts, tracker.portfolio_value({_SYMBOL: price}))
        results.append({
            "open_units_after": fill.open_units_after,
            "vwap_entry_price": fill.vwap_entry_price,
            "realized_pnl_usd": fill.realized_pnl_usd,
            "unrealized_pnl_usd": fill.unrealized_pnl_usd,
            "fee_cost_usd": fill.fee_cost_usd,
            "position_event": fill.position_event,
            "fill_price": fill.fill_price,
        })
    return results


def test_determinism():
    """
    Same tick sequence replayed twice must produce identical results.
    Also verifies that reconstructing state mid-sequence from DB gives
    identical results to the in-memory path (restart-survival property).
    """
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        db = Path(tmp) / "test_ws11.db"

        # First pass: full sequence in one run.
        run1 = _run_sequence(db)

    # Second pass: fresh DB, fresh tracker — fully independent replay.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        db = Path(tmp) / "test_ws11_b.db"
        run2 = _run_sequence(db)

    assert len(run1) == len(run2)
    for i, (r1, r2) in enumerate(zip(run1, run2)):
        for key in r1:
            if isinstance(r1[key], float):
                assert abs(r1[key] - r2[key]) < 1e-9, (
                    f"tick {i} key={key}: {r1[key]} != {r2[key]}"
                )
            else:
                assert r1[key] == r2[key], (
                    f"tick {i} key={key}: {r1[key]!r} != {r2[key]!r}"
                )


def test_restart_survival():
    """
    Reconstruct from DB mid-sequence and confirm identical outcome to uninterrupted run.

    Simulates: run ticks 0-2, restart (reconstruct from DB), run ticks 3-5.
    Compare with uninterrupted run of all 6 ticks.
    """
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        db = Path(tmp) / "restart_test.db"

        # --- Uninterrupted run ---
        full_results = _run_sequence(db)

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        db = Path(tmp) / "restart_test2.db"
        results_pre = []
        results_post = []

        # Run ticks 0-2
        tracker = PositionTracker(db_path=db)
        logger = TradeLogger(db_path=db)
        for i, (target, price) in enumerate(_TICKS[:3]):
            fill = tracker.process_fill(
                symbol=_SYMBOL,
                target_size_notional=target,
                current_price=price,
                config=_CFG,
            )
            ts = f"2026-01-01T{i:02d}:00:00+00:00"
            logger.append(TradeRecord(
                timestamp_decision=ts, timestamp_fill=ts, symbol=_SYMBOL,
                vol_estimate=0.01, target_size_notional=target,
                intended_fill_price=price, actual_fill_price=fill.fill_price,
                slippage_bps=2.0, fees_bps=4.0, latency_ms=100.0,
                error=None, universe_snapshot=[_SYMBOL],
                open_units_before=fill.open_units_before,
                open_units_after=fill.open_units_after,
                vwap_entry_price=fill.vwap_entry_price or None,
                realized_pnl_usd=fill.realized_pnl_usd,
                unrealized_pnl_usd=fill.unrealized_pnl_usd,
                fee_cost_usd=fill.fee_cost_usd,
                portfolio_value_usd=None, position_event=fill.position_event,
            ))
            logger.set_portfolio_value_for_tick(ts, tracker.portfolio_value({_SYMBOL: price}))
            results_pre.append(fill)

        # --- Simulated restart: reconstruct from DB ---
        tracker2 = PositionTracker(db_path=db)

        # Run ticks 3-5 with the reconstructed tracker
        for i, (target, price) in enumerate(_TICKS[3:], start=3):
            fill = tracker2.process_fill(
                symbol=_SYMBOL,
                target_size_notional=target,
                current_price=price,
                config=_CFG,
            )
            ts = f"2026-01-01T{i:02d}:00:00+00:00"
            logger.append(TradeRecord(
                timestamp_decision=ts, timestamp_fill=ts, symbol=_SYMBOL,
                vol_estimate=0.01, target_size_notional=target,
                intended_fill_price=price, actual_fill_price=fill.fill_price,
                slippage_bps=2.0, fees_bps=4.0, latency_ms=100.0,
                error=None, universe_snapshot=[_SYMBOL],
                open_units_before=fill.open_units_before,
                open_units_after=fill.open_units_after,
                vwap_entry_price=fill.vwap_entry_price or None,
                realized_pnl_usd=fill.realized_pnl_usd,
                unrealized_pnl_usd=fill.unrealized_pnl_usd,
                fee_cost_usd=fill.fee_cost_usd,
                portfolio_value_usd=None, position_event=fill.position_event,
            ))
            results_post.append(fill)

        restarted = results_pre + results_post

    # Compare tick-by-tick
    for i, (fr, rr) in enumerate(zip(full_results, restarted)):
        # full_results are dicts; restarted is FillResult objects
        if isinstance(fr, dict):
            assert abs(fr["open_units_after"] - rr.open_units_after) < 1e-9, (
                f"tick {i}: open_units_after mismatch after restart"
            )
            assert abs(fr["realized_pnl_usd"] - rr.realized_pnl_usd) < 1e-9, (
                f"tick {i}: realized_pnl_usd mismatch after restart"
            )
            assert fr["position_event"] == rr.position_event, (
                f"tick {i}: position_event mismatch after restart"
            )


def test_fee_uses_ws5_model():
    """
    Confirm taker fee is computed as notional * taker_fee_bps/10000,
    matching WS5's cost model (not a new shortcut).
    """
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        db = Path(tmp) / "fee_test.db"
        tracker = PositionTracker(db_path=db)

        target_notional = 1000.0
        price = 50_000.0
        fill = tracker.process_fill(
            symbol="BTCUSDT",
            target_size_notional=target_notional,
            current_price=price,
            config=_CFG,
        )

    slippage = _CFG.slippage_bps / 10_000.0
    fill_price = price * (1.0 + slippage)
    units = target_notional / price
    expected_fee = units * fill_price * (_CFG.taker_fee_bps / 10_000.0)
    assert abs(fill.fee_cost_usd - expected_fee) < 1e-9, (
        f"fee_cost_usd={fill.fee_cost_usd} expected={expected_fee}"
    )


def test_vwap_accumulation():
    """VWAP entry price updates correctly on position increase."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        db = Path(tmp) / "vwap_test.db"
        tracker = PositionTracker(db_path=db)

        fill1 = tracker.process_fill("BTCUSDT", 1000.0, 50_000.0, _CFG)
        fill2 = tracker.process_fill("BTCUSDT", 2000.0, 55_000.0, _CFG)

    slippage = _CFG.slippage_bps / 10_000.0
    fill_price1 = 50_000.0 * (1 + slippage)
    fill_price2 = 55_000.0 * (1 + slippage)
    # Target units are computed at mid price (target_notional / current_price)
    units1 = 1000.0 / 50_000.0               # first open: delta = 0 → 0.02
    target_units2 = 2000.0 / 55_000.0        # total target after tick 2
    delta_units2 = target_units2 - units1     # units added on increase
    expected_vwap = (units1 * fill_price1 + delta_units2 * fill_price2) / target_units2
    assert abs(fill2.vwap_entry_price - expected_vwap) < 1e-6, (
        f"vwap={fill2.vwap_entry_price} expected={expected_vwap}"
    )

"""
WS9 paper trading scheduler — main entry point.

Run with:
    py -m src.paper_trading.scheduler

What this does each hour:
  1. Fetches current top-30 USDT-M symbols by 24h volume from Binance API.
  2. Fetches recent 1h klines from Binance public API for each symbol.
  3. Computes vol-targeted paper position size (WS5 model, no directional signal).
  4. Logs every decision to the SQLite trade log.
  5. Writes a heartbeat file for the dead-man's switch.

Error policy (decided in ws9_vol_portfolio.md, not at runtime):
  - Transient failure on one symbol: retry 3x with exponential backoff.
    If all retries fail, log a null-fill row for that symbol and continue.
  - Tick-level unrecoverable error (DB write failure, assertion): send alert,
    do NOT write heartbeat, halt -- manual restart required.
  - A gracefully-skipped symbol still counts as a successful tick for the
    heartbeat (the scheduler is alive; the symbol data was unavailable).
"""

from __future__ import annotations

import datetime
import time

from src.paper_trading.config import (
    REBALANCE_INTERVAL_SECONDS,
    SLIPPAGE_BPS,
    TAKER_FEE_BPS,
)
from src.paper_trading.heartbeat import HeartbeatThread, send_alert, write_heartbeat
from src.paper_trading.logger import TradeLogger, TradeRecord
from src.paper_trading.position_tracker import PositionTracker
from src.paper_trading.sizer import fetch_live_universe, make_execution_config, size_symbol


def _load_universe() -> list[str]:
    """Fetch current top-30 USDT-M symbols by 24h quote volume from Binance."""
    symbols = fetch_live_universe(top_n=30)
    print(
        f"[scheduler] Live universe: {len(symbols)} symbols "
        f"(top-30 by 24h volume)",
        flush=True,
    )
    return symbols


def _now_utc() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _run_tick(
    symbols: list[str],
    logger: TradeLogger,
    tracker: PositionTracker,
) -> None:
    """
    Execute one rebalance tick: size every symbol, track positions, log results.
    Unrecoverable errors propagate to the caller (scheduler halts + alerts).
    """
    cfg = make_execution_config()
    universe_size = len(symbols)
    universe_snapshot = list(symbols)

    # Collect fill prices this tick for portfolio MTM at the end.
    tick_prices: dict[str, float] = {}
    # Store (symbol, fill_result) pairs for portfolio_value annotation.
    fills: list[tuple[str, object]] = []

    for symbol in symbols:
        tick_start = time.monotonic()
        ts_decision = _now_utc()
        record_kwargs: dict = dict(
            timestamp_decision=ts_decision,
            timestamp_fill=None,
            symbol=symbol,
            vol_estimate=None,
            target_size_notional=None,
            intended_fill_price=None,
            actual_fill_price=None,
            slippage_bps=SLIPPAGE_BPS,
            fees_bps=TAKER_FEE_BPS,
            latency_ms=None,
            error=None,
            universe_snapshot=universe_snapshot,
        )

        try:
            decision = size_symbol(symbol, universe_size=universe_size, config=cfg)
            ts_fill = _now_utc()
            latency_ms = (time.monotonic() - tick_start) * 1_000.0

            # WS11: compute position fill using WS5 slippage model via tracker.
            fill = tracker.process_fill(
                symbol=symbol,
                target_size_notional=decision.target_notional,
                current_price=decision.intended_fill_price,
                config=cfg,
            )
            tick_prices[symbol] = decision.intended_fill_price
            fills.append((symbol, fill))

            record_kwargs.update(
                timestamp_fill=ts_fill,
                vol_estimate=decision.sizing.realized_volatility,
                target_size_notional=decision.target_notional,
                intended_fill_price=decision.intended_fill_price,
                actual_fill_price=decision.actual_fill_price,
                latency_ms=latency_ms,
                # WS11 position fields
                open_units_before=fill.open_units_before,
                open_units_after=fill.open_units_after,
                vwap_entry_price=fill.vwap_entry_price if fill.vwap_entry_price else None,
                realized_pnl_usd=fill.realized_pnl_usd,
                unrealized_pnl_usd=fill.unrealized_pnl_usd,
                fee_cost_usd=fill.fee_cost_usd,
                position_event=fill.position_event,
            )
        except Exception as exc:
            # Transient failure: already retried inside size_symbol/fetch_klines.
            # Log a null-fill row and continue to the next symbol.
            latency_ms = (time.monotonic() - tick_start) * 1_000.0
            record_kwargs.update(
                latency_ms=latency_ms,
                error=f"{type(exc).__name__}: {exc}",
            )
            print(f"[scheduler] {symbol} skipped: {exc}", flush=True)

        # DB write failure here is unrecoverable — let it propagate.
        logger.append(TradeRecord(**record_kwargs))

    # Annotate each logged row with the end-of-tick portfolio value.
    # We do this as a single UPDATE after all rows are inserted so that every
    # symbol in the tick gets the same portfolio snapshot.
    if tick_prices:
        portfolio_val = tracker.portfolio_value(tick_prices)
        logger.set_portfolio_value_for_tick(ts_decision, portfolio_val)
        print(
            f"[scheduler] Tick complete — portfolio value: ${portfolio_val:.2f}",
            flush=True,
        )

    # Heartbeat written after ALL symbols are processed (or gracefully skipped).
    write_heartbeat()


def run(*, once: bool = False) -> None:
    """
    Main scheduling loop.

    Args:
        once: if True, run exactly one tick and return (used by tests).
    """
    print("[scheduler] Starting WS9+WS11 paper trading scheduler", flush=True)

    watchdog = HeartbeatThread()
    watchdog.start()

    logger = TradeLogger()
    tracker = PositionTracker()
    symbols = _load_universe()

    if not symbols:
        send_alert(
            subject="Universe is empty - scheduler cannot start",
            body="fetch_live_universe() returned no symbols. Check Binance API connectivity.",
        )
        watchdog.stop()
        raise RuntimeError("Universe is empty")

    while True:
        tick_start_wall = time.time()
        try:
            _run_tick(symbols, logger, tracker)
        except Exception as exc:
            # Unrecoverable tick error: alert and halt.
            # Heartbeat was NOT written, so the watchdog will also fire
            # if this keeps happening.
            import traceback
            send_alert(
                subject="Unrecoverable tick error - HALTED",
                body=f"UTC: {_now_utc()}\n\n{traceback.format_exc()}",
            )
            watchdog.stop()
            raise

        if once:
            break

        # Sleep until the next rebalance boundary.
        elapsed = time.time() - tick_start_wall
        sleep_for = max(0.0, REBALANCE_INTERVAL_SECONDS - elapsed)
        print(
            f"[scheduler] Tick done in {elapsed:.1f}s, sleeping {sleep_for:.0f}s",
            flush=True,
        )
        time.sleep(sleep_for)

    watchdog.stop()


if __name__ == "__main__":
    run()

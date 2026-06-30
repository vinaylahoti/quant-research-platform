"""
WS9 paper trading scheduler — main entry point.

Run with:
    py -m src.paper_trading.scheduler

What this does each hour:
  1. Loads the WS4 point-in-time universe (most recent available date).
  2. Fetches recent 1h klines from Binance public API for each symbol.
  3. Computes vol-targeted paper position size (WS5 model, no directional signal).
  4. Logs every decision to data/paper_trading.db.
  5. Writes a heartbeat file for the dead-man's switch.

Error policy (decided in ws9_vol_portfolio.md, not at runtime):
  - Transient failure on one symbol: retry 3× with exponential backoff.
    If all retries fail, log a null-fill row for that symbol and continue.
  - Tick-level unrecoverable error (DB write failure, assertion): send alert,
    do NOT write heartbeat, halt — manual restart required.
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
from src.paper_trading.sizer import make_execution_config, size_symbol
from src.universe.builder import PointInTimeUniverse


def _load_universe() -> tuple[PointInTimeUniverse, list[str]]:
    """
    Build the WS4 membership table and return the most-recent-available symbols.

    The featurestore covers data through the last downloaded date (currently
    end of 2025). as_of(today) would return nothing for dates beyond that.
    We use the latest date present in the membership table so the system
    always gets a real, point-in-time universe rather than an empty one.
    """
    pit = PointInTimeUniverse()
    table = pit.membership_table()
    latest_date = table["date"].max()
    members = pit.as_of(latest_date)
    symbols = [m.symbol for m in members]
    print(
        f"[scheduler] Universe as of {latest_date.date()}: {len(symbols)} symbols",
        flush=True,
    )
    return pit, symbols


def _now_utc() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _run_tick(
    symbols: list[str],
    logger: TradeLogger,
) -> None:
    """
    Execute one rebalance tick: size every symbol, log results.
    Unrecoverable errors propagate to the caller (scheduler halts + alerts).
    """
    cfg = make_execution_config()
    universe_size = len(symbols)
    universe_snapshot = list(symbols)

    for symbol in symbols:
        tick_start = time.monotonic()
        ts_decision = _now_utc()
        error_msg: str | None = None
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
            record_kwargs.update(
                timestamp_fill=ts_fill,
                vol_estimate=decision.sizing.realized_volatility,
                target_size_notional=decision.target_notional,
                intended_fill_price=decision.intended_fill_price,
                actual_fill_price=decision.actual_fill_price,
                latency_ms=latency_ms,
            )
        except Exception as exc:
            # Transient failure: already retried inside size_symbol/fetch_klines.
            # Log a null-fill row and continue to the next symbol.
            error_msg = f"{type(exc).__name__}: {exc}"
            latency_ms = (time.monotonic() - tick_start) * 1_000.0
            record_kwargs.update(latency_ms=latency_ms, error=error_msg)
            print(f"[scheduler] {symbol} skipped: {error_msg}", flush=True)

        # DB write failure here is unrecoverable — let it propagate.
        logger.append(TradeRecord(**record_kwargs))

    # Heartbeat written after ALL symbols are processed (or gracefully skipped).
    # This is the only point where write_heartbeat() is called per tick,
    # so a DB failure above prevents the heartbeat from being written —
    # causing the dead-man's switch to fire for any true unrecoverable error.
    write_heartbeat()


def run(*, once: bool = False) -> None:
    """
    Main scheduling loop.

    Args:
        once: if True, run exactly one tick and return (used by tests).
    """
    print("[scheduler] Starting WS9 paper trading scheduler", flush=True)

    watchdog = HeartbeatThread()
    watchdog.start()

    logger = TradeLogger()
    _pit, symbols = _load_universe()

    if not symbols:
        send_alert(
            subject="Universe is empty — scheduler cannot start",
            body="PointInTimeUniverse returned no symbols. Check the featurestore.",
        )
        watchdog.stop()
        raise RuntimeError("Universe is empty")

    while True:
        tick_start_wall = time.time()
        try:
            _run_tick(symbols, logger)
        except Exception as exc:
            # Unrecoverable tick error: alert and halt.
            # Heartbeat was NOT written, so the watchdog will also fire
            # if this keeps happening.
            import traceback
            send_alert(
                subject=f"Unrecoverable tick error — HALTED",
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

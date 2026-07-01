"""
WS11 position tracker — state and P&L layer for the WS9 scheduler.

Tracks open position in units (contracts) per symbol with a VWAP entry price.
Uses WS5's slippage functions directly — no new cost model.

State is reconstructed from the DB on startup; the DB is updated each tick so
container restarts never lose position state.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from src.execution.model import ExecutionConfig
from src.paper_trading.config import LOG_DB_PATH, PAPER_PORTFOLIO_USD


@dataclass
class PositionState:
    open_units: float = 0.0         # contracts held (0 = flat)
    vwap_entry_price: float = 0.0   # avg cost basis per unit; 0 if flat
    cumulative_realized_pnl: float = 0.0


@dataclass
class FillResult:
    open_units_before: float
    open_units_after: float
    vwap_entry_price: float      # after this tick (0 if now flat)
    realized_pnl_usd: float
    unrealized_pnl_usd: float    # MTM at fill price
    fee_cost_usd: float
    position_event: str          # 'open' | 'increase' | 'decrease' | 'close' | 'hold'
    fill_price: float            # actual fill price (slippage applied)


class PositionTracker:
    """
    In-memory position state initialized from DB on construction.

    process_fill() is called once per symbol per tick.  After all symbols are
    processed, portfolio_value() aggregates the tick's total account value.
    """

    def __init__(self, db_path: Path = LOG_DB_PATH) -> None:
        self._states: dict[str, PositionState] = {}
        self._total_realized_pnl: float = 0.0
        self._db_path = db_path
        self._reconstruct_from_db()

    def _reconstruct_from_db(self) -> None:
        """
        Load the most recent non-error position state per symbol from the DB.

        Reads open_units_after and vwap_entry_price from the last row per symbol
        that has WS11 tracking columns populated.  Also sums all realized_pnl_usd
        to restore the cumulative total.
        """
        try:
            conn = sqlite3.connect(self._db_path)
            conn.row_factory = sqlite3.Row

            # Latest state per symbol
            rows = conn.execute("""
                SELECT t.symbol, t.open_units_after, t.vwap_entry_price
                FROM trades t
                INNER JOIN (
                    SELECT symbol, MAX(id) AS max_id
                    FROM trades
                    WHERE open_units_after IS NOT NULL
                    GROUP BY symbol
                ) latest ON t.id = latest.max_id
            """).fetchall()

            for row in rows:
                self._states[row["symbol"]] = PositionState(
                    open_units=row["open_units_after"] or 0.0,
                    vwap_entry_price=row["vwap_entry_price"] or 0.0,
                )

            # Cumulative realized P&L
            result = conn.execute(
                "SELECT COALESCE(SUM(realized_pnl_usd), 0.0) FROM trades "
                "WHERE realized_pnl_usd IS NOT NULL"
            ).fetchone()
            conn.close()
            if result:
                self._total_realized_pnl = result[0]

            if self._states:
                open_count = sum(1 for s in self._states.values() if s.open_units > 0)
                print(
                    f"[position_tracker] Reconstructed from DB: "
                    f"{len(self._states)} symbols tracked, "
                    f"{open_count} open positions, "
                    f"cumulative realized P&L = ${self._total_realized_pnl:.2f}",
                    flush=True,
                )
        except Exception as exc:
            # DB may not have WS11 columns yet (first run after migration).
            print(f"[position_tracker] Reconstruct skipped: {exc}", flush=True)

    def process_fill(
        self,
        symbol: str,
        target_size_notional: float,
        current_price: float,
        config: ExecutionConfig,
    ) -> FillResult:
        """
        Compute the fill for one symbol given the new target notional.

        target_size_notional: desired position value in USD at current_price.
        current_price: the intended fill price (close of latest completed bar).

        Slippage and taker fees from config are applied to any executed delta.
        No new cost model — directly uses WS5's per-side formulas.
        """
        state = self._states.get(symbol, PositionState())
        prev_units = state.open_units
        prev_vwap = state.vwap_entry_price

        slippage = config.slippage_bps / 10_000.0
        taker_fee_rate = config.taker_fee_bps / 10_000.0

        # Convert target notional to units at current price
        if current_price <= 0:
            current_price = prev_vwap if prev_vwap > 0 else 1.0
        target_units = target_size_notional / current_price

        delta_units = target_units - prev_units

        if abs(delta_units) < 1e-9:
            # Hold — no trade, just MTM
            unrealized = (
                prev_units * (current_price - prev_vwap)
                if prev_units > 0 and prev_vwap > 0
                else 0.0
            )
            return FillResult(
                open_units_before=prev_units,
                open_units_after=prev_units,
                vwap_entry_price=prev_vwap,
                realized_pnl_usd=0.0,
                unrealized_pnl_usd=unrealized,
                fee_cost_usd=0.0,
                position_event="hold",
                fill_price=current_price,
            )

        if delta_units > 0:
            # Opening or increasing position (long entry).
            # WS5 entry slippage: pay above mid.
            fill_price = current_price * (1.0 + slippage)
            fee_usd = delta_units * fill_price * taker_fee_rate
            new_units = prev_units + delta_units
            if prev_units <= 0 or prev_vwap <= 0:
                new_vwap = fill_price
                event = "open"
            else:
                # Volume-weighted average entry price
                new_vwap = (
                    prev_units * prev_vwap + delta_units * fill_price
                ) / new_units
                event = "increase"
            realized_pnl = 0.0
        else:
            # Reducing or closing position (long exit).
            # WS5 exit slippage: receive below mid.
            fill_price = current_price * (1.0 - slippage)
            closed_units = abs(delta_units)
            fee_usd = closed_units * fill_price * taker_fee_rate
            new_units = max(0.0, prev_units + delta_units)
            # Realized P&L: (exit_price - entry_price) * units_closed
            realized_pnl = (
                closed_units * (fill_price - prev_vwap)
                if prev_vwap > 0
                else 0.0
            )
            new_vwap = prev_vwap if new_units > 0 else 0.0
            event = "close" if new_units <= 0 else "decrease"

        # Unrealized on the remaining position, marked at fill price
        unrealized = (
            new_units * (fill_price - new_vwap)
            if new_units > 0 and new_vwap > 0
            else 0.0
        )

        # Update in-memory state
        self._states[symbol] = PositionState(
            open_units=new_units,
            vwap_entry_price=new_vwap,
            cumulative_realized_pnl=state.cumulative_realized_pnl + realized_pnl,
        )
        self._total_realized_pnl += realized_pnl

        return FillResult(
            open_units_before=prev_units,
            open_units_after=new_units,
            vwap_entry_price=new_vwap,
            realized_pnl_usd=realized_pnl,
            unrealized_pnl_usd=unrealized,
            fee_cost_usd=fee_usd,
            position_event=event,
            fill_price=fill_price,
        )

    def portfolio_value(self, prices: dict[str, float]) -> float:
        """
        Total account value at end of tick.

        = PAPER_PORTFOLIO_USD (starting capital)
        + cumulative realized P&L across all symbols ever
        + sum of unrealized MTM across all currently open positions

        Uses prices dict (symbol → current price) for MTM; falls back to
        vwap_entry_price (no gain, no loss) if price not available.
        """
        total_unrealized = 0.0
        for sym, state in self._states.items():
            if state.open_units > 0 and state.vwap_entry_price > 0:
                mark = prices.get(sym, state.vwap_entry_price)
                total_unrealized += state.open_units * (mark - state.vwap_entry_price)

        return PAPER_PORTFOLIO_USD + self._total_realized_pnl + total_unrealized

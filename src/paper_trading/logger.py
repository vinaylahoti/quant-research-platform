"""
Append-only SQLite trade log for WS9 paper trading.

One row per sizing decision per symbol per tick. Never updated, only appended.
Schema version is stored per-row so future schema changes are detectable.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from src.paper_trading.config import LOG_DB_PATH

_SCHEMA_VERSION = 2

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS trades (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    schema_version      INTEGER NOT NULL DEFAULT 2,
    timestamp_decision  TEXT    NOT NULL,
    timestamp_fill      TEXT,
    symbol              TEXT    NOT NULL,
    vol_estimate        REAL,
    target_size_notional REAL,
    intended_fill_price REAL,
    actual_fill_price   REAL,
    slippage_bps        REAL,
    fees_bps            REAL,
    latency_ms          REAL,
    error               TEXT,
    universe_snapshot   TEXT,
    -- WS11 position-tracking columns (NULL for error rows or pre-WS11 rows)
    open_units_before   REAL,
    open_units_after    REAL,
    vwap_entry_price    REAL,
    realized_pnl_usd    REAL,
    unrealized_pnl_usd  REAL,
    fee_cost_usd        REAL,
    portfolio_value_usd REAL,
    position_event      TEXT
)
"""

# Migration: add WS11 columns to existing tables that only have v1 schema.
_MIGRATE_SQL = [
    "ALTER TABLE trades ADD COLUMN open_units_before   REAL",
    "ALTER TABLE trades ADD COLUMN open_units_after    REAL",
    "ALTER TABLE trades ADD COLUMN vwap_entry_price    REAL",
    "ALTER TABLE trades ADD COLUMN realized_pnl_usd    REAL",
    "ALTER TABLE trades ADD COLUMN unrealized_pnl_usd  REAL",
    "ALTER TABLE trades ADD COLUMN fee_cost_usd        REAL",
    "ALTER TABLE trades ADD COLUMN portfolio_value_usd REAL",
    "ALTER TABLE trades ADD COLUMN position_event      TEXT",
]


@dataclass
class TradeRecord:
    timestamp_decision: str   # ISO-8601 UTC
    timestamp_fill: str | None
    symbol: str
    vol_estimate: float | None
    target_size_notional: float | None
    intended_fill_price: float | None
    actual_fill_price: float | None
    slippage_bps: float | None
    fees_bps: float | None
    latency_ms: float | None
    error: str | None
    universe_snapshot: list[str] | None
    # WS11 position-tracking fields (None for error rows)
    open_units_before: float | None = None
    open_units_after: float | None = None
    vwap_entry_price: float | None = None
    realized_pnl_usd: float | None = None
    unrealized_pnl_usd: float | None = None
    fee_cost_usd: float | None = None
    portfolio_value_usd: float | None = None
    position_event: str | None = None


class TradeLogger:
    def __init__(self, db_path: Path = LOG_DB_PATH) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db_path = db_path
        self._init_db()

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(_CREATE_SQL)
            # Migrate pre-WS11 tables that are missing the new columns.
            existing = {
                row[1]
                for row in conn.execute("PRAGMA table_info(trades)").fetchall()
            }
            for stmt in _MIGRATE_SQL:
                col = stmt.split("ADD COLUMN")[1].strip().split()[0]
                if col not in existing:
                    conn.execute(stmt)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path)

    def append(self, record: TradeRecord) -> None:
        snapshot_json = (
            json.dumps(record.universe_snapshot)
            if record.universe_snapshot is not None
            else None
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO trades (
                    schema_version, timestamp_decision, timestamp_fill, symbol,
                    vol_estimate, target_size_notional, intended_fill_price,
                    actual_fill_price, slippage_bps, fees_bps,
                    latency_ms, error, universe_snapshot,
                    open_units_before, open_units_after, vwap_entry_price,
                    realized_pnl_usd, unrealized_pnl_usd, fee_cost_usd,
                    portfolio_value_usd, position_event
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    _SCHEMA_VERSION,
                    record.timestamp_decision,
                    record.timestamp_fill,
                    record.symbol,
                    record.vol_estimate,
                    record.target_size_notional,
                    record.intended_fill_price,
                    record.actual_fill_price,
                    record.slippage_bps,
                    record.fees_bps,
                    record.latency_ms,
                    record.error,
                    snapshot_json,
                    record.open_units_before,
                    record.open_units_after,
                    record.vwap_entry_price,
                    record.realized_pnl_usd,
                    record.unrealized_pnl_usd,
                    record.fee_cost_usd,
                    record.portfolio_value_usd,
                    record.position_event,
                ),
            )

    def set_portfolio_value_for_tick(
        self, timestamp_decision: str, portfolio_value_usd: float
    ) -> None:
        """
        Back-fill portfolio_value_usd on all rows written during this tick.

        Called once after all symbols in a tick are logged, so every row in
        the tick carries the same end-of-tick portfolio snapshot.
        """
        with self._connect() as conn:
            conn.execute(
                "UPDATE trades SET portfolio_value_usd = ? "
                "WHERE timestamp_decision = ? AND portfolio_value_usd IS NULL",
                (portfolio_value_usd, timestamp_decision),
            )

    def recent(self, limit: int = 100) -> list[dict]:
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

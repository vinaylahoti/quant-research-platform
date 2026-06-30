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

_SCHEMA_VERSION = 1

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS trades (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    schema_version      INTEGER NOT NULL DEFAULT 1,
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
    universe_snapshot   TEXT
)
"""


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


class TradeLogger:
    def __init__(self, db_path: Path = LOG_DB_PATH) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db_path = db_path
        self._init_db()

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(_CREATE_SQL)

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
                    latency_ms, error, universe_snapshot
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
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
                ),
            )

    def recent(self, limit: int = 100) -> list[dict]:
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

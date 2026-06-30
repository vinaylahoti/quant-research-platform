"""
WS1 research log.

This module gives the project one append-only place to record every experiment.
We use SQLite because it is built into Python, durable, and easy to query.

WS3's validation harness now calls log_experiment() automatically on every run.
Manual calls still exist for one-off utilities and tests, but validation itself
should never bypass this log.

The design goal is simple:
- one row per experiment
- every row gets a running trial number
- rows are never updated or deleted by normal code paths
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LOG_PATH = REPO_ROOT / "data" / "research_log.db"


def _utc_now_iso() -> str:
    """Return a timezone-aware UTC timestamp in ISO format."""

    return datetime.now(timezone.utc).isoformat()


def _to_json(value: Any) -> str:
    """Serialize nested data deterministically for storage."""

    return json.dumps(value, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class ExperimentRecord:
    """Container for one experiment entry before it is written."""

    git_commit: str
    data_snapshot_id: str
    universe_definition: str
    params: dict[str, Any]
    metrics: dict[str, Any]


class ResearchLog:
    """Append-only SQLite-backed experiment log."""

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = Path(db_path or DEFAULT_LOG_PATH)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        """Open a connection with row access by column name."""

        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        """Create the experiments table the first time the log is used."""

        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS experiments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trial_number INTEGER NOT NULL UNIQUE,
                    logged_at_utc TEXT NOT NULL,
                    git_commit TEXT NOT NULL,
                    data_snapshot_id TEXT NOT NULL,
                    universe_definition TEXT NOT NULL,
                    params_json TEXT NOT NULL,
                    metrics_json TEXT NOT NULL
                )
                """
            )
            connection.commit()

    def how_many_trials(self) -> int:
        """Return the number of experiments written so far."""

        with self._connect() as connection:
            row = connection.execute("SELECT COUNT(*) AS count FROM experiments").fetchone()
        return int(row["count"])

    def log_experiment(
        self,
        *,
        git_commit: str,
        data_snapshot_id: str,
        universe_definition: str,
        params: dict[str, Any],
        metrics: dict[str, Any],
    ) -> int:
        """
        Append one immutable experiment row and return its trial number.

        Trial numbers are sequential and stable, which makes them useful
        for multiple-testing accounting later.
        """

        next_trial_number = self.how_many_trials() + 1
        record = ExperimentRecord(
            git_commit=git_commit,
            data_snapshot_id=data_snapshot_id,
            universe_definition=universe_definition,
            params=params,
            metrics=metrics,
        )

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO experiments (
                    trial_number,
                    logged_at_utc,
                    git_commit,
                    data_snapshot_id,
                    universe_definition,
                    params_json,
                    metrics_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    next_trial_number,
                    _utc_now_iso(),
                    record.git_commit,
                    record.data_snapshot_id,
                    record.universe_definition,
                    _to_json(record.params),
                    _to_json(record.metrics),
                ),
            )
            connection.commit()

        return next_trial_number

    def fetch_all(self) -> list[sqlite3.Row]:
        """Small helper for tests and manual inspection."""

        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM experiments ORDER BY trial_number ASC"
            ).fetchall()
        return rows


def log_experiment(
    *,
    git_commit: str,
    data_snapshot_id: str,
    universe_definition: str,
    params: dict[str, Any],
    metrics: dict[str, Any],
    db_path: Path | None = None,
) -> int:
    """Convenience wrapper so callers do not need to manage a class instance."""

    return ResearchLog(db_path=db_path).log_experiment(
        git_commit=git_commit,
        data_snapshot_id=data_snapshot_id,
        universe_definition=universe_definition,
        params=params,
        metrics=metrics,
    )


def how_many_trials(db_path: Path | None = None) -> int:
    """Convenience wrapper returning the number of logged trials."""

    return ResearchLog(db_path=db_path).how_many_trials()

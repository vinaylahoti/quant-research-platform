"""
Download helpers for Binance historical futures data.

The downloader is deterministic because:
1. it reads its symbol list and dates from config/settings.py,
2. it uses explicit Binance dataset types, and
3. it only downloads what we ask for.

This module does not do any feature engineering. Its only job is to fetch
raw source files and place them in a predictable folder layout.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Iterable

from binance_historical_data import BinanceDataDumper

from config.settings import DATE_END, DATE_START, RAW_DATA_DIR, SYMBOLS


def _ensure_dir(path: Path) -> Path:
    """Create a directory if it does not already exist."""

    path.mkdir(parents=True, exist_ok=True)
    return path


def _coerce_date(value: str | dt.date) -> dt.date:
    """Convert a YYYY-MM-DD string into a `datetime.date`."""

    if isinstance(value, dt.date):
        return value
    return dt.date.fromisoformat(value)


def _build_dumper(base_dir: Path, data_type: str) -> BinanceDataDumper:
    """
    Create a Binance dumper configured for USDT-M futures data.

    The README locked in:
    - asset_class = "um"
    - base klines = 1-minute
    - metrics also needed for funding/open-interest research
    """

    if data_type == "klines":
        return BinanceDataDumper(
            path_dir_where_to_dump=str(base_dir),
            asset_class="um",
            data_type=data_type,
            data_frequency="1m",
        )

    return BinanceDataDumper(
        path_dir_where_to_dump=str(base_dir),
        asset_class="um",
        data_type=data_type,
    )


def download_dataset(
    data_type: str,
    symbols: Iterable[str] | None = None,
    date_start: str | dt.date = DATE_START,
    date_end: str | dt.date = DATE_END,
    update_existing: bool = False,
) -> Path:
    """
    Download one Binance dataset type into `data/raw/<data_type>`.

    Parameters
    ----------
    data_type:
        "klines" for candles or "metrics" for funding/open-interest metrics.
    symbols:
        Optional symbol list. Defaults to the project-wide universe.
    date_start, date_end:
        Inclusive date strings in YYYY-MM-DD format.
    update_existing:
        False by default so repeated runs stay deterministic unless you
        intentionally choose to refresh files.
    """

    if data_type not in {"klines", "metrics"}:
        raise ValueError("data_type must be either 'klines' or 'metrics'")

    target_dir = _ensure_dir(RAW_DATA_DIR / data_type)
    dumper = _build_dumper(target_dir, data_type=data_type)
    tickers = list(symbols or SYMBOLS)
    start_date = _coerce_date(date_start)
    end_date = _coerce_date(date_end)

    dumper.dump_data(
        tickers=tickers,
        date_start=start_date,
        date_end=end_date,
        is_to_update_existing=update_existing,
    )
    return target_dir


def download_phase1_data(
    symbols: Iterable[str] | None = None,
    date_start: str | dt.date = DATE_START,
    date_end: str | dt.date = DATE_END,
    update_existing: bool = False,
) -> dict[str, Path]:
    """
    Download both raw datasets needed for Phase 1.

    Returns a small dictionary so calling code can easily see where the
    files were written.
    """

    return {
        "klines": download_dataset(
            data_type="klines",
            symbols=symbols,
            date_start=date_start,
            date_end=date_end,
            update_existing=update_existing,
        ),
        "metrics": download_dataset(
            data_type="metrics",
            symbols=symbols,
            date_start=date_start,
            date_end=date_end,
            update_existing=update_existing,
        ),
    }


if __name__ == "__main__":
    written = download_phase1_data()
    for name, path in written.items():
        print(f"{name}: {path}")

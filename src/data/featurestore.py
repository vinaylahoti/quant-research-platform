"""
Parquet feature store for Phase 1.

Responsibilities:
1. Read Binance raw files from the downloader output.
2. Keep the agreed candle columns only.
3. Write compressed parquet files partitioned by symbol and year.
4. Load any symbol/date/timeframe slice quickly and deterministically.

The code is written in a step-by-step style on purpose so it stays friendly
for someone who is still learning how data pipelines fit together.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
import gzip
import shutil

import pandas as pd

from config.settings import FEATURE_STORE_DIR, RAW_DATA_DIR, SUPPORTED_TIMEFRAMES


# Binance kline CSV columns for the public historical dataset.
BINANCE_KLINE_COLUMNS = [
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_volume",
    "trade_count",
    "taker_buy_base",
    "taker_buy_quote",
    "ignore",
]


# We only keep the columns the README explicitly asked for.
FEATURE_COLUMNS = [
    "open",
    "high",
    "low",
    "close",
    "volume",
    "quote_volume",
    "taker_buy_base",
    "taker_buy_quote",
]


# Pandas resample rules that map cleanly from the README timeframes.
TIMEFRAME_TO_RULE = {
    "1m": "1min",
    "5m": "5min",
    "15m": "15min",
    "1h": "1h",
    "4h": "4h",
    "1d": "1D",
}


@dataclass(frozen=True)
class StorePaths:
    """Small helper to keep raw and processed paths grouped together."""

    raw_klines_dir: Path = RAW_DATA_DIR / "klines"
    featurestore_dir: Path = FEATURE_STORE_DIR


class FeatureStore:
    """Read, write, and load candle data from the local feature store."""

    def __init__(self, paths: StorePaths | None = None) -> None:
        self.paths = paths or StorePaths()
        self.paths.featurestore_dir.mkdir(parents=True, exist_ok=True)

    def build(self, symbols: Iterable[str] | None = None) -> list[Path]:
        """
        Build parquet partitions from the raw kline files.

        Returns the parquet file paths written during this run.
        """

        raw_files = self._find_raw_kline_files(symbols=symbols)
        if not raw_files:
            raise FileNotFoundError(
                f"No raw kline files found under {self.paths.raw_klines_dir}"
            )

        # Rebuilding should replace stale parquet output for the same symbol set.
        target_symbols = sorted({path.stem.split("-")[0] for path in raw_files})
        for symbol in target_symbols:
            symbol_dir = self.paths.featurestore_dir / f"symbol={symbol}"
            if symbol_dir.exists():
                shutil.rmtree(symbol_dir)

        raw_files_by_symbol: dict[str, list[Path]] = {}
        for raw_file in raw_files:
            symbol = raw_file.stem.split("-")[0]
            raw_files_by_symbol.setdefault(symbol, []).append(raw_file)

        written_files: list[Path] = []
        for symbol, symbol_files in sorted(raw_files_by_symbol.items()):
            frames = [self._read_raw_kline_file(path) for path in symbol_files]
            combined = (
                pd.concat(frames)
                .sort_index()
                .loc[lambda frame: ~frame.index.duplicated(keep="last")]
            )
            combined["year"] = combined.index.year

            for year, year_frame in combined.groupby("year", sort=True):
                output_path = self.paths.featurestore_dir / f"symbol={symbol}" / f"year={year}" / "data.parquet"
                output_path.parent.mkdir(parents=True, exist_ok=True)

                to_write = year_frame.drop(columns=["year"]).reset_index()
                to_write.to_parquet(
                    output_path,
                    index=False,
                    compression="zstd",
                )
                written_files.append(output_path)

            # Drop large temporary objects before moving to the next symbol.
            del frames
            del combined

        return written_files

    def load(
        self,
        symbol: str,
        start: str | pd.Timestamp,
        end: str | pd.Timestamp,
        timeframe: str = "1m",
    ) -> pd.DataFrame:
        """
        Load one symbol into a pandas DataFrame, optionally resampled.

        This is the Phase 1 decision-gate function.
        """

        if timeframe not in SUPPORTED_TIMEFRAMES:
            raise ValueError(
                f"Unsupported timeframe '{timeframe}'. Supported: {SUPPORTED_TIMEFRAMES}"
            )

        start_ts = self._coerce_timestamp(start)
        end_ts = self._coerce_timestamp(end)
        year_paths = self._year_paths_for_range(symbol=symbol, start=start_ts, end=end_ts)
        if not year_paths:
            raise FileNotFoundError(
                f"No parquet partitions found for {symbol} between {start_ts.date()} and {end_ts.date()}"
            )

        frames = [pd.read_parquet(path) for path in year_paths]
        df = pd.concat(frames, ignore_index=True)
        df["open_time"] = pd.to_datetime(df["open_time"], utc=True)
        df = df.set_index("open_time").sort_index()
        df = df.loc[start_ts:end_ts, FEATURE_COLUMNS]

        if timeframe == "1m":
            return df.copy()

        rule = TIMEFRAME_TO_RULE[timeframe]
        resampled = df.resample(rule, label="left", closed="left").agg(
            {
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "volume": "sum",
                "quote_volume": "sum",
                "taker_buy_base": "sum",
                "taker_buy_quote": "sum",
            }
        )

        # Drop empty buckets so callers get a clean trading frame.
        return resampled.dropna(subset=["open", "high", "low", "close"])

    def _find_raw_kline_files(self, symbols: Iterable[str] | None = None) -> list[Path]:
        """Find all raw kline CSV or CSV.GZ files for the requested symbols."""

        if not self.paths.raw_klines_dir.exists():
            return []

        allowed = {symbol.upper() for symbol in symbols} if symbols else None
        candidates = sorted(self.paths.raw_klines_dir.rglob("*.csv")) + sorted(
            self.paths.raw_klines_dir.rglob("*.csv.gz")
        )

        selected: list[Path] = []
        for path in candidates:
            symbol = path.name.split("-")[0].upper()
            if allowed is None or symbol in allowed:
                selected.append(path)
        return selected

    def _read_raw_kline_file(self, path: Path) -> pd.DataFrame:
        """Read one raw Binance kline file and normalize its schema."""

        opener = gzip.open if path.suffix == ".gz" else open
        with opener(path, "rt", encoding="utf-8") as handle:
            df = pd.read_csv(handle, header=None, names=BINANCE_KLINE_COLUMNS)

        df["open_time"] = pd.to_datetime(
            pd.to_numeric(df["open_time"], errors="coerce"),
            unit="ms",
            utc=True,
            errors="coerce",
        )
        numeric_columns = FEATURE_COLUMNS
        for column in numeric_columns:
            df[column] = pd.to_numeric(df[column], errors="coerce")

        # Drop malformed/header rows defensively before indexing.
        df = df.dropna(subset=["open_time", *FEATURE_COLUMNS])
        cleaned = df.set_index("open_time")[FEATURE_COLUMNS].sort_index()
        return cleaned

    def _year_paths_for_range(
        self,
        symbol: str,
        start: pd.Timestamp,
        end: pd.Timestamp,
    ) -> list[Path]:
        """Return the parquet files needed for the requested date range."""

        years = range(start.year, end.year + 1)
        paths: list[Path] = []
        for year in years:
            path = self.paths.featurestore_dir / f"symbol={symbol}" / f"year={year}" / "data.parquet"
            if path.exists():
                paths.append(path)
        return paths

    @staticmethod
    def _coerce_timestamp(value: str | pd.Timestamp) -> pd.Timestamp:
        """Normalize timestamps to UTC for consistent slicing."""

        ts = pd.Timestamp(value)
        if ts.tzinfo is None:
            return ts.tz_localize("UTC")
        return ts.tz_convert("UTC")


def load(
    symbol: str,
    start: str | pd.Timestamp,
    end: str | pd.Timestamp,
    timeframe: str = "1m",
    store: FeatureStore | None = None,
) -> pd.DataFrame:
    """
    Module-level convenience wrapper.

    This keeps the Phase 1 API simple:
        from src.data.featurestore import load
    """

    return (store or FeatureStore()).load(symbol=symbol, start=start, end=end, timeframe=timeframe)

"""
Funding-rate downloader and feature-store builder.

Source: data.binance.vision  data/futures/um/monthly/fundingRate/{SYMBOL}/
Format: one CSV per month, columns: calc_time (ms), funding_interval_hours, last_funding_rate

Publish schedule: Binance settles funding 3×/day at 00:00, 08:00, 16:00 UTC.
calc_time is the actual settlement timestamp (milliseconds UTC).

Point-in-time note: funding is observable immediately after calc_time — there
is no publication lag to model, unlike OI metrics.  The row timestamp IS the
settle time.

Storage layout (mirrors klines featurestore convention):
    data/raw/funding_rate/{SYMBOL}/{SYMBOL}-fundingRate-{YYYY}-{MM}.csv
    data/featurestore/funding_rate/symbol={SYM}/year={YYYY}/data.parquet
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from typing import Iterable

import pandas as pd
import requests

from config.settings import RAW_DATA_DIR, FEATURE_STORE_DIR, SYMBOLS

BINANCE_BASE = "https://data.binance.vision"
FUNDING_RAW_DIR = RAW_DATA_DIR / "funding_rate"
FUNDING_STORE_DIR = FEATURE_STORE_DIR / "funding_rate"

FUNDING_COLUMNS = ["calc_time", "funding_interval_hours", "last_funding_rate"]


def _zip_url(symbol: str, year: int, month: int) -> str:
    fname = f"{symbol}-fundingRate-{year}-{month:02d}.zip"
    return f"{BINANCE_BASE}/data/futures/um/monthly/fundingRate/{symbol}/{fname}"


def _raw_csv_path(symbol: str, year: int, month: int) -> Path:
    return FUNDING_RAW_DIR / symbol / f"{symbol}-fundingRate-{year}-{month:02d}.csv"


def download_funding_rates(
    symbols: Iterable[str] | None = None,
    year_start: int = 2022,
    year_end: int = 2026,
    skip_existing: bool = True,
) -> dict[str, list[Path]]:
    """
    Download monthly funding-rate CSVs from data.binance.vision.

    Returns a dict mapping symbol -> list of local CSV paths written.
    Silently skips months where the remote file doesn't exist (symbol not yet listed).
    """
    syms = list(symbols or SYMBOLS)
    written: dict[str, list[Path]] = {s: [] for s in syms}

    session = requests.Session()

    for symbol in syms:
        sym_dir = FUNDING_RAW_DIR / symbol
        sym_dir.mkdir(parents=True, exist_ok=True)

        for year in range(year_start, year_end + 1):
            for month in range(1, 13):
                # Don't request future months
                import datetime
                if (year, month) > (datetime.date.today().year, datetime.date.today().month):
                    continue

                csv_path = _raw_csv_path(symbol, year, month)
                if skip_existing and csv_path.exists():
                    written[symbol].append(csv_path)
                    continue

                url = _zip_url(symbol, year, month)
                resp = session.get(url, timeout=30)
                if resp.status_code == 404:
                    continue  # symbol not listed that month
                resp.raise_for_status()

                with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
                    csv_name = next(n for n in zf.namelist() if n.endswith(".csv"))
                    csv_path.write_bytes(zf.read(csv_name))

                written[symbol].append(csv_path)

        count = len(written[symbol])
        print(f"  {symbol}: {count} months")

    return written


def _read_csv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, header=0, names=FUNDING_COLUMNS, skiprows=1)
    df["calc_time"] = pd.to_datetime(
        pd.to_numeric(df["calc_time"], errors="coerce"), unit="ms", utc=True, errors="coerce"
    )
    df["last_funding_rate"] = pd.to_numeric(df["last_funding_rate"], errors="coerce")
    df["funding_interval_hours"] = pd.to_numeric(df["funding_interval_hours"], errors="coerce")
    df = df.dropna(subset=["calc_time", "last_funding_rate"])
    df = df.set_index("calc_time").sort_index()
    return df


def build_funding_store(
    symbols: Iterable[str] | None = None,
    year_start: int = 2022,
) -> list[Path]:
    """
    Build parquet partitions from the raw funding-rate CSVs.

    Layout: data/featurestore/funding_rate/symbol={SYM}/year={YYYY}/data.parquet
    Columns: last_funding_rate, funding_interval_hours
    Index: calc_time (UTC, the settlement timestamp)
    """
    syms = list(symbols or SYMBOLS)
    written: list[Path] = []

    for symbol in syms:
        sym_raw_dir = FUNDING_RAW_DIR / symbol
        if not sym_raw_dir.exists():
            print(f"  {symbol}: no raw files, skipping")
            continue

        csv_files = sorted(sym_raw_dir.glob(f"{symbol}-fundingRate-*.csv"))
        if not csv_files:
            print(f"  {symbol}: no CSVs found")
            continue

        frames = [_read_csv(p) for p in csv_files]
        combined = (
            pd.concat(frames)
            .sort_index()
            .loc[lambda df: ~df.index.duplicated(keep="last")]
        )
        # Filter to requested start year
        combined = combined[combined.index.year >= year_start]

        if combined.empty:
            continue

        combined["year"] = combined.index.year
        for year, ydf in combined.groupby("year"):
            out = (
                FUNDING_STORE_DIR
                / f"symbol={symbol}"
                / f"year={year}"
                / "data.parquet"
            )
            out.parent.mkdir(parents=True, exist_ok=True)
            ydf.drop(columns=["year"]).reset_index().to_parquet(
                out, index=False, compression="zstd"
            )
            written.append(out)

        del frames, combined

    return written


def load_funding(
    symbol: str,
    start: str | pd.Timestamp,
    end: str | pd.Timestamp,
) -> pd.DataFrame:
    """
    Load funding-rate rows for one symbol over a date range.

    Returns a DataFrame indexed by calc_time (UTC) with columns:
        last_funding_rate, funding_interval_hours
    """
    start_ts = _coerce_ts(start)
    end_ts = _coerce_ts(end)
    years = range(start_ts.year, end_ts.year + 1)

    frames: list[pd.DataFrame] = []
    for year in years:
        p = FUNDING_STORE_DIR / f"symbol={symbol}" / f"year={year}" / "data.parquet"
        if p.exists():
            frames.append(pd.read_parquet(p))

    if not frames:
        raise FileNotFoundError(f"No funding-rate parquet for {symbol} in [{start}, {end}]")

    df = pd.concat(frames, ignore_index=True)
    df["calc_time"] = pd.to_datetime(df["calc_time"], utc=True)
    df = df.set_index("calc_time").sort_index()
    return df.loc[start_ts:end_ts, ["last_funding_rate", "funding_interval_hours"]]


def _coerce_ts(value: str | pd.Timestamp) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


if __name__ == "__main__":
    print("=== Downloading funding rates (2022 onward) ===")
    download_funding_rates(year_start=2022, year_end=2026)
    print("\n=== Building parquet store ===")
    paths = build_funding_store(year_start=2022)
    print(f"Wrote {len(paths)} parquet partitions to {FUNDING_STORE_DIR}")

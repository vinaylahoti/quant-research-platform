"""WS4.5 bounded survivorship mitigation helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import datetime as dt
import zipfile

from config.settings import RAW_DATA_DIR
from src.data.featurestore import FeatureStore


ARCHIVE_ROOT = "https://data.binance.vision/data/futures/um/monthly/klines"


@dataclass(frozen=True)
class DelistedSymbolWindow:
    symbol: str
    start_month: str
    end_month: str


WS45_DELISTED_SYMBOLS: tuple[DelistedSymbolWindow, ...] = (
    DelistedSymbolWindow("LUNAUSDT", "2021-01", "2022-05"),
    DelistedSymbolWindow("FTTUSDT", "2021-01", "2022-11"),
)


def month_range(start_month: str, end_month: str) -> list[str]:
    start = dt.date.fromisoformat(f"{start_month}-01")
    end = dt.date.fromisoformat(f"{end_month}-01")
    months: list[str] = []
    current = start
    while current <= end:
        months.append(current.strftime("%Y-%m"))
        year = current.year + (current.month // 12)
        month = 1 if current.month == 12 else current.month + 1
        current = dt.date(year, month, 1)
    return months


def archive_url(symbol: str, month: str) -> str:
    return f"{ARCHIVE_ROOT}/{symbol}/1m/{symbol}-1m-{month}.zip"


def archive_exists(symbol: str, month: str) -> bool:
    request = Request(archive_url(symbol, month), method="HEAD")
    try:
        with urlopen(request, timeout=30) as response:
            return response.status == 200
    except HTTPError as exc:
        if exc.code == 404:
            return False
        raise
    except URLError as exc:
        raise RuntimeError(f"Could not probe {symbol} {month}: {exc}") from exc


def probe_ws45_archives(
    candidates: tuple[DelistedSymbolWindow, ...] = WS45_DELISTED_SYMBOLS,
) -> dict[str, list[str]]:
    available: dict[str, list[str]] = {}
    for candidate in candidates:
        months = [
            month
            for month in month_range(candidate.start_month, candidate.end_month)
            if archive_exists(candidate.symbol, month)
        ]
        available[candidate.symbol] = months
    return available


def download_ws45_klines(
    *,
    raw_klines_dir: Path = RAW_DATA_DIR / "klines",
    candidates: tuple[DelistedSymbolWindow, ...] = WS45_DELISTED_SYMBOLS,
) -> list[Path]:
    written: list[Path] = []
    for candidate in candidates:
        target_dir = raw_klines_dir / "futures" / "um" / "monthly" / "klines" / candidate.symbol / "1m"
        target_dir.mkdir(parents=True, exist_ok=True)
        for month in month_range(candidate.start_month, candidate.end_month):
            csv_path = target_dir / f"{candidate.symbol}-1m-{month}.csv"
            if csv_path.exists():
                written.append(csv_path)
                continue

            request = Request(archive_url(candidate.symbol, month), method="GET")
            try:
                with urlopen(request, timeout=120) as response:
                    if response.status != 200:
                        continue
                    payload = response.read()
            except HTTPError as exc:
                if exc.code == 404:
                    continue
                raise

            zip_path = target_dir / f"{candidate.symbol}-1m-{month}.zip"
            zip_path.write_bytes(payload)
            with zipfile.ZipFile(zip_path) as archive:
                archive.extract(f"{candidate.symbol}-1m-{month}.csv", target_dir)
            zip_path.unlink()
            written.append(csv_path)
    return written


def build_ws45_featurestore_partitions() -> list[Path]:
    symbols = [candidate.symbol for candidate in WS45_DELISTED_SYMBOLS]
    return FeatureStore().build(symbols=symbols)


if __name__ == "__main__":
    available = probe_ws45_archives()
    for symbol, months in available.items():
        print(f"{symbol}: {len(months)} available months ({months[0]}..{months[-1]})")
    files = download_ws45_klines()
    print(f"downloaded_or_existing_raw_files={len(files)}")
    partitions = build_ws45_featurestore_partitions()
    print(f"featurestore_partitions={len(partitions)}")

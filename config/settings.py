"""
Project settings for Phase 1 of the quant bot build.

This file is intentionally plain and heavily commented so it is easy to edit
without needing to understand the rest of the codebase first.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


# The repository root is the folder that contains this file's parent folder.
REPO_ROOT = Path(__file__).resolve().parents[1]

# All downloaded raw files and processed parquet files live under /data.
DATA_DIR = REPO_ROOT / "data"
RAW_DATA_DIR = DATA_DIR / "raw"
FEATURE_STORE_DIR = DATA_DIR / "featurestore"


# Top-30 liquid USDT-M perpetual symbols.
# Keeping this list explicit makes the project deterministic:
# everyone runs the same universe unless they intentionally edit it here.
SYMBOLS: tuple[str, ...] = (
    "BTCUSDT",
    "ETHUSDT",
    "BNBUSDT",
    "SOLUSDT",
    "XRPUSDT",
    "DOGEUSDT",
    "ADAUSDT",
    "TRXUSDT",
    "LINKUSDT",
    "AVAXUSDT",
    "BCHUSDT",
    "DOTUSDT",
    "LTCUSDT",
    "TONUSDT",
    # Binance USDT-M futures uses the 1000-token contract code here.
    # Important: this contract is quoted per 1000 SHIB, so its price/volume
    # scale will look very different from spot-style SHIB pairs.
    "1000SHIBUSDT",
    "UNIUSDT",
    "NEARUSDT",
    "APTUSDT",
    "ATOMUSDT",
    "XLMUSDT",
    "ETCUSDT",
    "FILUSDT",
    "HBARUSDT",
    "ICPUSDT",
    "ARBUSDT",
    "OPUSDT",
    "INJUSDT",
    "VETUSDT",
    "ALGOUSDT",
    "SUIUSDT",
)


# Five years ending on a fixed date keeps runs reproducible.
# You can move these later if you decide to refresh the dataset.
DATE_START = "2021-01-01"
DATE_END = "2025-12-31"


# Risk defaults requested by the README.
LEVERAGE = 5
STOP_LOSS_PCT = 1.5
TAKE_PROFIT_PCT = 2.5
TIME_STOP_MINUTES = 20


# Only allow known resample targets. This keeps load() predictable.
SUPPORTED_TIMEFRAMES: tuple[str, ...] = ("1m", "5m", "15m", "1h", "4h", "1d")


@dataclass(frozen=True)
class RiskConfig:
    """Small container for the risk parameters used in later phases."""

    leverage: int
    stop_loss_pct: float
    take_profit_pct: float
    time_stop_minutes: int


RISK = RiskConfig(
    leverage=LEVERAGE,
    stop_loss_pct=STOP_LOSS_PCT,
    take_profit_pct=TAKE_PROFIT_PCT,
    time_stop_minutes=TIME_STOP_MINUTES,
)

"""
Locked parameters for WS9 paper trading.

These values are fixed before the first run. Do not change them to produce
better-looking results — that is the same mistake as loosening a signal
definition to manufacture trades.
"""

from __future__ import annotations

import os
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]

# On Railway: set PAPER_TRADING_DATA_DIR=/data (the Volume mount path) so
# the trade log, heartbeat, and reports survive redeploys.
# Locally: leave unset; falls back to data/ inside the repo root.
_DATA_DIR: Path = Path(os.environ.get("PAPER_TRADING_DATA_DIR", str(_REPO_ROOT / "data")))

# Rebalance cadence: 1h bars, matching the existing WS5 execution model.
REBALANCE_INTERVAL_SECONDS: int = 3_600

# Number of 1h bars used to estimate realized volatility.
# 24 bars = 24h trailing window. Same formula as size_position() in WS5.
VOL_LOOKBACK_BARS: int = 24

# Target risk per bar, passed directly to ExecutionConfig.target_trade_risk_pct.
# leverage = TARGET_TRADE_RISK_PCT / realized_vol_per_bar
TARGET_TRADE_RISK_PCT: float = 0.01

MAX_LEVERAGE: float = 5.0
MIN_LEVERAGE: float = 0.1

# Slippage and taker fee, matching ExecutionConfig defaults.
# Taker fee: Binance standard account, ~4 bps/side. Applied both entry and exit.
SLIPPAGE_BPS: float = 2.0
TAKER_FEE_BPS: float = 4.0

# Notional portfolio size for P&L accounting only. No real money is involved.
PAPER_PORTFOLIO_USD: float = 10_000.0

# Dead-man's switch fires if the main loop is silent for this many intervals.
HEARTBEAT_TIMEOUT_MULTIPLIER: int = 2

# Persistence paths — all rooted under _DATA_DIR so a single env var
# controls whether they land on the Railway Volume or the local repo.
LOG_DB_PATH: Path = _DATA_DIR / "paper_trading.db"
HEARTBEAT_PATH: Path = _DATA_DIR / "paper_trading_heartbeat.txt"
REPORT_DIR: Path = _DATA_DIR / "paper_trading_reports"

# Binance USDT-M perpetual futures klines endpoint (public, no auth required).
BINANCE_FAPI_KLINES_URL: str = "https://fapi.binance.com/fapi/v1/klines"

# Retry policy for transient API/network errors within a single tick.
RETRY_MAX_ATTEMPTS: int = 3
RETRY_BASE_DELAY_SECONDS: float = 2.0  # doubles each retry: 2s, 4s, 8s

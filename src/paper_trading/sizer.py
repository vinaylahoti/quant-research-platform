"""
Vol-targeted sizer for WS9 paper trading.

Thin wrapper around WS5's size_position(). No new sizing logic lives here.
Fetches recent 1h klines from Binance's public futures API (no auth required)
to compute the trailing return series, then delegates to WS5.

The sanity check in scripts/sanity_check.py verifies that
compute_target_size_from_returns() produces output identical (within 1e-9
relative error) to calling size_position() directly with the same inputs.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import requests

from src.execution.model import ExecutionConfig, VolatilitySizing, size_position
from src.paper_trading.config import (
    BINANCE_FAPI_KLINES_URL,
    MAX_LEVERAGE,
    MIN_LEVERAGE,
    PAPER_PORTFOLIO_USD,
    RETRY_BASE_DELAY_SECONDS,
    RETRY_MAX_ATTEMPTS,
    SLIPPAGE_BPS,
    TAKER_FEE_BPS,
    TARGET_TRADE_RISK_PCT,
    VOL_LOOKBACK_BARS,
)


@dataclass(frozen=True)
class SizingDecision:
    sizing: VolatilitySizing       # from WS5's size_position()
    target_notional: float         # in USD (PAPER_PORTFOLIO_USD / N * leverage)
    intended_fill_price: float     # close price of the most recent completed bar
    actual_fill_price: float       # intended_fill_price + entry slippage (long)
    closes: tuple[float, ...]      # raw close series used — stored for audit


def make_execution_config() -> ExecutionConfig:
    return ExecutionConfig(
        target_trade_risk_pct=TARGET_TRADE_RISK_PCT,
        max_leverage=MAX_LEVERAGE,
        min_leverage=MIN_LEVERAGE,
        slippage_bps=SLIPPAGE_BPS,
        taker_fee_bps=TAKER_FEE_BPS,
    )


def compute_target_size_from_returns(
    trailing_returns: tuple[float, ...],
    config: ExecutionConfig,
) -> VolatilitySizing:
    """
    WS5 reference path exposed directly so the sanity check can call it.
    This is *identical* to calling size_position() directly — it exists only
    to give the sanity check a named "live path" function to diff against.
    """
    return size_position(trailing_returns=trailing_returns, config=config)


# Maximum ratio between any two consecutive 1h closes before the response
# is treated as corrupt. 50% per hour is already an extreme move (covers
# LUNA-style collapses and FTX-day BTC). Anything larger almost certainly
# means a format change caused us to parse the wrong field.
_MAX_CONSECUTIVE_RATIO = 1.50


def _check_closes(symbol: str, closes: list[float]) -> None:
    """
    Plausibility guard against Binance API format changes.

    Raises ValueError if any close is non-positive or if any consecutive pair
    of closes diverges by more than _MAX_CONSECUTIVE_RATIO. Either condition
    means the parsed values are implausible as prices — treat as a fetch
    failure so the retry/null-fill path handles it, not as valid data.
    """
    for i, price in enumerate(closes):
        if price <= 0:
            raise ValueError(
                f"{symbol}: close[{i}]={price} is non-positive — "
                "possible API format change at index [4]"
            )
    for i in range(1, len(closes)):
        ratio = closes[i] / closes[i - 1]
        if ratio > _MAX_CONSECUTIVE_RATIO or ratio < 1.0 / _MAX_CONSECUTIVE_RATIO:
            raise ValueError(
                f"{symbol}: close[{i}]={closes[i]:.6f} vs close[{i-1}]={closes[i-1]:.6f} "
                f"ratio={ratio:.3f} exceeds +-50% bound — "
                "likely API format change or extreme data corruption"
            )


def fetch_live_universe(top_n: int = 30) -> list[str]:
    """
    Return the current top-N USDT-M perpetual futures symbols ranked by
    24h quote volume, via Binance's public ticker endpoint (no auth required).

    This replaces the featurestore-backed PointInTimeUniverse in production:
    the backtests needed historical point-in-time correctness; the live
    scheduler only needs to know what's liquid right now.

    Raises on failure after RETRY_MAX_ATTEMPTS — caller handles it.
    """
    url = "https://fapi.binance.com/fapi/v1/ticker/24hr"
    last_exc: Exception = RuntimeError("No attempts made")

    for attempt in range(RETRY_MAX_ATTEMPTS):
        try:
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            tickers = resp.json()
            usdt_perps = [
                t for t in tickers
                if isinstance(t.get("symbol"), str)
                and t["symbol"].endswith("USDT")
                and float(t.get("quoteVolume", 0)) > 0
            ]
            usdt_perps.sort(key=lambda t: float(t["quoteVolume"]), reverse=True)
            return [t["symbol"] for t in usdt_perps[:top_n]]
        except Exception as exc:
            last_exc = exc
            if attempt < RETRY_MAX_ATTEMPTS - 1:
                delay = RETRY_BASE_DELAY_SECONDS * (2 ** attempt)
                time.sleep(delay)

    raise last_exc


def fetch_klines(symbol: str, limit: int = VOL_LOOKBACK_BARS + 1) -> list[float]:
    """
    Fetch recent 1h close prices from Binance USDT-M futures public API.

    Returns a list of floats (close prices), oldest first. Raises on failure
    after RETRY_MAX_ATTEMPTS attempts — caller applies the error-handling policy.

    Plausibility check: all closes must be > 0 and no consecutive pair may
    diverge by more than 50%. Failure raises ValueError, which is caught by
    the same retry/null-fill path as any other fetch error.
    """
    params = {"symbol": symbol, "interval": "1h", "limit": limit}
    last_exc: Exception = RuntimeError("No attempts made")

    for attempt in range(RETRY_MAX_ATTEMPTS):
        try:
            resp = requests.get(
                BINANCE_FAPI_KLINES_URL, params=params, timeout=10
            )
            resp.raise_for_status()
            klines = resp.json()
            # Each kline: [open_time, open, high, low, close, ...]
            closes = [float(k[4]) for k in klines]
            _check_closes(symbol, closes)
            return closes
        except Exception as exc:
            last_exc = exc
            if attempt < RETRY_MAX_ATTEMPTS - 1:
                delay = RETRY_BASE_DELAY_SECONDS * (2 ** attempt)
                time.sleep(delay)

    raise last_exc


def size_symbol(
    symbol: str,
    universe_size: int,
    config: ExecutionConfig | None = None,
) -> SizingDecision:
    """
    Fetch live klines for one symbol and compute vol-targeted paper position.

    universe_size: number of symbols currently in the universe, used to
    compute per-symbol base notional (PAPER_PORTFOLIO_USD / universe_size).

    Raises on API failure — caller applies skip-or-halt policy.
    """
    cfg = config or make_execution_config()
    closes = fetch_klines(symbol, limit=VOL_LOOKBACK_BARS + 1)

    if len(closes) < 2:
        raise ValueError(f"{symbol}: fewer than 2 closes returned, cannot compute returns")

    returns = tuple(closes[i] / closes[i - 1] - 1.0 for i in range(1, len(closes)))
    sizing = size_position(trailing_returns=returns, config=cfg)

    base_notional = PAPER_PORTFOLIO_USD / max(universe_size, 1)
    target_notional = base_notional * sizing.leverage

    intended_price = closes[-1]
    # Long entry slippage: price paid is slightly above mid (worst-case long fill).
    actual_price = intended_price * (1.0 + cfg.slippage_bps / 10_000.0)

    return SizingDecision(
        sizing=sizing,
        target_notional=target_notional,
        intended_fill_price=intended_price,
        actual_fill_price=actual_price,
        closes=tuple(closes),
    )

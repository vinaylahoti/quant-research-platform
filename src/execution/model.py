"""Single execution, slippage, funding, and sizing model."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd


PositionSide = Literal["long", "short"]
ExitReason = Literal["stop_loss", "take_profit", "time_stop"]


@dataclass(frozen=True)
class ExecutionConfig:
    stop_loss_pct: float = 0.015
    take_profit_pct: float = 0.025
    target_trade_risk_pct: float = 0.01
    max_leverage: float = 5.0
    min_leverage: float = 0.1
    slippage_bps: float = 2.0
    # Binance taker fee: 0.04% per side for standard accounts.
    # Applied on both entry and exit, so full round-trip = 2 × taker_fee_bps × leverage.
    taker_fee_bps: float = 4.0


@dataclass(frozen=True)
class VolatilitySizing:
    realized_volatility: float
    leverage: float
    expected_risk_pct: float


@dataclass(frozen=True)
class FundingEvent:
    timestamp: pd.Timestamp
    rate: float


@dataclass(frozen=True)
class TradeRequest:
    symbol: str
    side: PositionSide
    entry_time: pd.Timestamp
    entry_price: float
    planned_exit_time: pd.Timestamp
    trailing_returns: tuple[float, ...]


@dataclass(frozen=True)
class ExecutionResult:
    symbol: str
    side: PositionSide
    caller: str
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    entry_price: float
    exit_price: float
    gross_return_pct: float
    funding_cost_pct: float
    slippage_cost_pct: float
    taker_fee_cost_pct: float
    net_return_pct: float
    exit_reason: ExitReason
    sizing: VolatilitySizing


def estimate_realized_volatility(returns: tuple[float, ...]) -> float:
    if len(returns) < 2:
        return 0.0
    series = pd.Series(returns, dtype="float64")
    return float(series.std(ddof=0))


def size_position(
    *,
    trailing_returns: tuple[float, ...],
    config: ExecutionConfig,
) -> VolatilitySizing:
    realized_vol = estimate_realized_volatility(trailing_returns)
    if realized_vol <= 0.0:
        leverage = config.max_leverage
    else:
        leverage = config.target_trade_risk_pct / realized_vol
    leverage = min(config.max_leverage, max(config.min_leverage, leverage))
    return VolatilitySizing(
        realized_volatility=realized_vol,
        leverage=leverage,
        expected_risk_pct=leverage * realized_vol,
    )


def resolve_intrabar_exit(
    *,
    side: PositionSide,
    entry_price: float,
    bars_1m: pd.DataFrame,
    planned_exit_time: pd.Timestamp,
    config: ExecutionConfig,
) -> tuple[pd.Timestamp, float, ExitReason]:
    stop_price, take_profit_price = _exit_levels(
        side=side,
        entry_price=entry_price,
        config=config,
    )
    bars = bars_1m.sort_index()
    if bars.empty:
        return planned_exit_time, stop_price, "stop_loss"

    entry_time = _coerce_timestamp(bars.index.min())
    planned_exit_time = _coerce_timestamp(planned_exit_time)
    bars = bars.loc[(bars.index >= entry_time) & (bars.index <= planned_exit_time)]
    if bars.empty:
        return planned_exit_time, stop_price, "stop_loss"

    for timestamp, row in bars.iterrows():
        high = float(row["high"])
        low = float(row["low"])
        stop_hit, take_profit_hit = _hits(side=side, high=high, low=low, stop_price=stop_price, take_profit_price=take_profit_price)
        if stop_hit and take_profit_hit:
            return _coerce_timestamp(timestamp), stop_price, "stop_loss"
        if stop_hit:
            return _coerce_timestamp(timestamp), stop_price, "stop_loss"
        if take_profit_hit:
            return _coerce_timestamp(timestamp), take_profit_price, "take_profit"

    last_timestamp = _coerce_timestamp(bars.index[-1])
    return last_timestamp, float(bars.iloc[-1]["close"]), "time_stop"


def execute_trade(
    *,
    request: TradeRequest,
    bars_1m: pd.DataFrame,
    funding_rates: pd.DataFrame | None = None,
    config: ExecutionConfig | None = None,
    caller: str,
) -> ExecutionResult:
    config = config or ExecutionConfig()
    sizing = size_position(trailing_returns=request.trailing_returns, config=config)
    exit_time, raw_exit_price, exit_reason = resolve_intrabar_exit(
        side=request.side,
        entry_price=request.entry_price,
        bars_1m=bars_1m,
        planned_exit_time=request.planned_exit_time,
        config=config,
    )
    entry_price = _apply_entry_slippage(request.entry_price, request.side, config.slippage_bps)
    exit_price = _apply_exit_slippage(raw_exit_price, request.side, config.slippage_bps)
    direction = 1.0 if request.side == "long" else -1.0
    gross_unlevered = direction * ((exit_price / entry_price) - 1.0)
    funding_cost = funding_cost_pct(
        side=request.side,
        entry_time=request.entry_time,
        exit_time=exit_time,
        funding_rates=funding_rates,
    )
    slippage_cost = 2.0 * (config.slippage_bps / 10_000.0) * sizing.leverage
    taker_fee_cost = 2.0 * (config.taker_fee_bps / 10_000.0) * sizing.leverage
    gross_return = gross_unlevered * sizing.leverage
    net_return = gross_return - funding_cost - slippage_cost - taker_fee_cost
    return ExecutionResult(
        symbol=request.symbol,
        side=request.side,
        caller=caller,
        entry_time=_coerce_timestamp(request.entry_time),
        exit_time=exit_time,
        entry_price=entry_price,
        exit_price=exit_price,
        gross_return_pct=gross_return,
        funding_cost_pct=funding_cost,
        slippage_cost_pct=slippage_cost,
        taker_fee_cost_pct=taker_fee_cost,
        net_return_pct=net_return,
        exit_reason=exit_reason,
        sizing=sizing,
    )


def funding_cost_pct(
    *,
    side: PositionSide,
    entry_time: pd.Timestamp,
    exit_time: pd.Timestamp,
    funding_rates: pd.DataFrame | None,
) -> float:
    if funding_rates is None or funding_rates.empty:
        return 0.0
    frame = funding_rates.copy().sort_index()
    frame.index = pd.to_datetime(frame.index, utc=True)
    rate_column = "funding_rate" if "funding_rate" in frame.columns else "last_funding_rate"
    if rate_column not in frame.columns:
        return 0.0
    entry_time = _coerce_timestamp(entry_time)
    exit_time = _coerce_timestamp(exit_time)
    observed = frame.loc[(frame.index > entry_time) & (frame.index <= exit_time), rate_column]
    signed_cost = float(observed.sum())
    return signed_cost if side == "long" else -signed_cost


def _exit_levels(
    *,
    side: PositionSide,
    entry_price: float,
    config: ExecutionConfig,
) -> tuple[float, float]:
    if side == "long":
        return entry_price * (1.0 - config.stop_loss_pct), entry_price * (1.0 + config.take_profit_pct)
    return entry_price * (1.0 + config.stop_loss_pct), entry_price * (1.0 - config.take_profit_pct)


def _hits(
    *,
    side: PositionSide,
    high: float,
    low: float,
    stop_price: float,
    take_profit_price: float,
) -> tuple[bool, bool]:
    if side == "long":
        return low <= stop_price, high >= take_profit_price
    return high >= stop_price, low <= take_profit_price


def _apply_entry_slippage(price: float, side: PositionSide, slippage_bps: float) -> float:
    multiplier = 1.0 + (slippage_bps / 10_000.0)
    return price * multiplier if side == "long" else price / multiplier


def _apply_exit_slippage(price: float, side: PositionSide, slippage_bps: float) -> float:
    multiplier = 1.0 - (slippage_bps / 10_000.0)
    return price * multiplier if side == "long" else price / multiplier


def _coerce_timestamp(value: pd.Timestamp) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        return ts.tz_localize("UTC")
    return ts.tz_convert("UTC")

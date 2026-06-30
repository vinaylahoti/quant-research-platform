"""Shared execution model for research, paper, and live adapters."""

from src.execution.model import (
    ExecutionConfig,
    ExecutionResult,
    FundingEvent,
    PositionSide,
    TradeRequest,
    VolatilitySizing,
    execute_trade,
    resolve_intrabar_exit,
)

__all__ = [
    "ExecutionConfig",
    "ExecutionResult",
    "FundingEvent",
    "PositionSide",
    "TradeRequest",
    "VolatilitySizing",
    "execute_trade",
    "resolve_intrabar_exit",
]

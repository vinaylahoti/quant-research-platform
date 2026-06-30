"""Research adapter that delegates all fills and sizing to src.execution."""

from __future__ import annotations

import pandas as pd

from src.execution.model import ExecutionConfig, ExecutionResult, TradeRequest, execute_trade


def simulate_research_trade(
    *,
    request: TradeRequest,
    bars_1m: pd.DataFrame,
    funding_rates: pd.DataFrame | None = None,
    config: ExecutionConfig | None = None,
) -> ExecutionResult:
    return execute_trade(
        request=request,
        bars_1m=bars_1m,
        funding_rates=funding_rates,
        config=config or ExecutionConfig(),
        caller="research",
    )

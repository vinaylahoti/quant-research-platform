"""WS5 execution-realism decision-gate proof."""

from __future__ import annotations

from datetime import UTC
from pathlib import Path
import sys

import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.execution import ExecutionConfig, TradeRequest
from src.execution.model import execute_trade
from src.execution.paper import simulate_paper_trade
from src.execution.research import simulate_research_trade


def _bars(start: str, rows: list[dict[str, float]]) -> pd.DataFrame:
    index = pd.date_range(start=pd.Timestamp(start, tz=UTC), periods=len(rows), freq="1min")
    return pd.DataFrame(rows, index=index)


def _request(*, symbol: str, trailing_returns: tuple[float, ...], planned_exit: str) -> TradeRequest:
    return TradeRequest(
        symbol=symbol,
        side="long",
        entry_time=pd.Timestamp("2025-01-01 00:00:00", tz=UTC),
        entry_price=100.0,
        planned_exit_time=pd.Timestamp(planned_exit, tz=UTC),
        trailing_returns=trailing_returns,
    )


def run_ws5_decision_gate_test() -> str:
    config = ExecutionConfig(
        stop_loss_pct=0.01,
        take_profit_pct=0.02,
        target_trade_risk_pct=0.01,
        max_leverage=5.0,
        min_leverage=0.1,
        slippage_bps=0.0,
    )

    ambiguous_15m = _bars(
        "2025-01-01 00:00:00",
        [
            {"open": 100.0, "high": 103.0, "low": 99.0, "close": 101.0},
        ],
    )
    ambiguous_request = _request(
        symbol="BTCUSDT",
        trailing_returns=(0.002, -0.002, 0.001, -0.001),
        planned_exit="2025-01-01 00:14:00",
    )
    intrabar_result = execute_trade(
        request=ambiguous_request,
        bars_1m=ambiguous_15m,
        funding_rates=None,
        config=config,
        caller="decision_gate",
    )
    assert intrabar_result.exit_reason == "stop_loss", "Ambiguous intrabar SL/TP must resolve SL-first."
    assert intrabar_result.exit_price == 99.0, "Expected long stop price at 1% below entry."

    calm_bars = _bars(
        "2025-01-01 00:00:00",
        [
            {"open": 100.0, "high": 100.6, "low": 99.8, "close": 100.4},
            {"open": 100.4, "high": 100.9, "low": 100.2, "close": 100.7},
            {"open": 100.7, "high": 101.0, "low": 100.5, "close": 100.8},
        ],
    )
    volatile_bars = _bars(
        "2025-01-01 00:00:00",
        [
            {"open": 100.0, "high": 100.6, "low": 99.8, "close": 100.4},
            {"open": 100.4, "high": 100.9, "low": 100.2, "close": 100.7},
            {"open": 100.7, "high": 101.0, "low": 100.5, "close": 100.8},
        ],
    )
    funding = pd.DataFrame(
        {"funding_rate": [0.0003, -0.0001]},
        index=pd.to_datetime(["2025-01-01 00:01:00", "2025-01-01 00:02:00"], utc=True),
    )

    calm_request = _request(
        symbol="BTCUSDT",
        trailing_returns=(-0.004, -0.002, 0.0, 0.002, 0.004),
        planned_exit="2025-01-01 00:02:00",
    )
    volatile_request = _request(
        symbol="BTCUSDT",
        trailing_returns=(-0.02, -0.01, 0.0, 0.01, 0.02),
        planned_exit="2025-01-01 00:02:00",
    )

    research_result = simulate_research_trade(
        request=calm_request,
        bars_1m=calm_bars,
        funding_rates=funding,
        config=config,
    )
    paper_result = simulate_paper_trade(
        request=calm_request,
        bars_1m=calm_bars,
        funding_rates=funding,
        config=config,
    )
    assert research_result.net_return_pct == paper_result.net_return_pct
    assert research_result.exit_reason == paper_result.exit_reason
    assert research_result.caller == "research"
    assert paper_result.caller == "paper"

    volatile_result = simulate_research_trade(
        request=volatile_request,
        bars_1m=volatile_bars,
        funding_rates=funding,
        config=config,
    )
    calm_risk = research_result.sizing.expected_risk_pct
    volatile_risk = volatile_result.sizing.expected_risk_pct
    assert research_result.sizing.leverage > volatile_result.sizing.leverage
    assert abs(calm_risk - volatile_risk) < 0.0000001
    assert abs(research_result.funding_cost_pct - 0.0002) < 0.0000001

    return "\n".join(
        [
            "WS5 decision gate passed",
            "shared_execution_code_path=src.execution.model.execute_trade",
            f"research_caller={research_result.caller}",
            f"paper_caller={paper_result.caller}",
            f"research_net_return={research_result.net_return_pct:.8f}",
            f"paper_net_return={paper_result.net_return_pct:.8f}",
            f"funding_cost_pct={research_result.funding_cost_pct:.8f}",
            f"calm_realized_vol={research_result.sizing.realized_volatility:.8f}",
            f"volatile_realized_vol={volatile_result.sizing.realized_volatility:.8f}",
            f"calm_leverage={research_result.sizing.leverage:.8f}",
            f"volatile_leverage={volatile_result.sizing.leverage:.8f}",
            f"calm_expected_risk={calm_risk:.8f}",
            f"volatile_expected_risk={volatile_risk:.8f}",
            f"ambiguous_intrabar_exit_reason={intrabar_result.exit_reason}",
            f"ambiguous_intrabar_exit_price={intrabar_result.exit_price:.8f}",
        ]
    )


def test_ws5_execution_decision_gate() -> None:
    assert "WS5 decision gate passed" in run_ws5_decision_gate_test()


if __name__ == "__main__":
    print(run_ws5_decision_gate_test())

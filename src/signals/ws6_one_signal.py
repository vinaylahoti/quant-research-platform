"""WS6 one-signal end-to-end harness on real WS1-WS5 components.

Committed rule: long if 20-bar trailing 4h close-to-close return > 0,
short if < 0, flat until lookback exists or return == 0.

Splitter uses calendar-time windows (train_days/test_days), not sample
counts.  With ~25-30 cross-sectional samples per 4h bar, the old
sample-count-based splitter consumed only the first few calendar days of
each year.  train_days=30 / test_days=90 / n_folds=3 / embargo_days=1
covers the full year: 3 × (30 + 1 + 90) = 363 calendar days.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib

import numpy as np
import pandas as pd

from config.settings import FEATURE_STORE_DIR
from src.execution import ExecutionConfig, TradeRequest, execute_trade
from src.features.engine import FeatureEngine
from src.research_log.metadata import compute_data_snapshot_id, resolve_git_commit
from src.research_log.store import ResearchLog, how_many_trials
from src.signals.momentum import momentum_signal
from src.universe.builder import PointInTimeUniverse
from src.validation.metrics import compute_deflated_sharpe_details, compute_sharpe_ratio
from src.validation.runner import FoldBacktestResult, ValidationRunResult, WalkForwardValidator
from src.validation.splitter import PurgedWalkForwardSplitter, WalkForwardFold


SIGNAL_TIMEFRAME = "4h"
SIGNAL_LOOKBACK_BARS = 20
SIGNAL_RULE = "long if 20-bar trailing 4h close-to-close return > 0, short if < 0, flat until lookback exists or return == 0"
MIN_WS4_SYMBOL_COVERAGE = 20
RANDOM_CONTROL_SEED = 20260628
# train_days=30 / test_days=90 / n_folds=3 / embargo_days=1 → 3×121=363 cal-days ≈ full year
SPLITTER_TRAIN_DAYS = 30
SPLITTER_TEST_DAYS = 90
SPLITTER_N_FOLDS = 3
SPLITTER_EMBARGO_DAYS = 1
YEARS = (2022, 2023, 2024, 2025)
FUNDING_SCOPE = {
    "included": False,
    "reason": "Funding-rate history is permanently out of project scope.",
}


@dataclass(frozen=True)
class SignalSample:
    timestamp: datetime
    label_end: datetime
    symbol: str
    close: float
    signal: int
    random_signal: int
    trailing_returns: tuple[float, ...]
    bars_1m: tuple[dict[str, float], ...]


@dataclass(frozen=True)
class StrategyReport:
    name: str
    result: ValidationRunResult
    profit_factor: float
    deflated_profit_factor: float
    deflated_sharpe: float
    aggregate_sharpe: float
    total_return: float
    trade_count: int
    clears_bar: bool


class WS6IntegrationObstacle(RuntimeError):
    pass


def signal_definition() -> dict[str, object]:
    return {
        "timeframe": SIGNAL_TIMEFRAME,
        "lookback_bars": SIGNAL_LOOKBACK_BARS,
        "rule": SIGNAL_RULE,
        "output": "-1 short, 0 flat, +1 long",
    }


def determinism_digest() -> tuple[str, str, bool]:
    features = _load_real_features_for_determinism()
    first = momentum_signal(features, lookback=SIGNAL_LOOKBACK_BARS)
    second = momentum_signal(features, lookback=SIGNAL_LOOKBACK_BARS)
    first_digest = _series_digest(first)
    second_digest = _series_digest(second)
    return first_digest, second_digest, first_digest == second_digest


def run_ws6_decision_gate() -> dict[str, object]:
    digest_one, digest_two, deterministic = determinism_digest()
    if not deterministic:
        raise WS6IntegrationObstacle("Momentum signal is not deterministic on real WS2 feature input.")

    yearly_reports = []
    for year in YEARS:
        yearly_reports.append(run_year(year))

    return {
        "signal_definition": signal_definition(),
        "determinism": {
            "first_digest": digest_one,
            "second_digest": digest_two,
            "passed": deterministic,
        },
        "funding_scope": FUNDING_SCOPE,
        "random_control_seed": RANDOM_CONTROL_SEED,
        "years": yearly_reports,
    }


def run_year(year: int) -> dict[str, object]:
    start = f"{year}-01-01 00:00:00+00:00"
    end = f"{year}-12-31 23:59:00+00:00"
    dataset, symbols_used, diagnostics = build_dataset(start=start, end=end)
    if len(dataset) < 360:
        raise WS6IntegrationObstacle(f"Not enough real WS2/WS4 samples for {year}: found {len(dataset)}")

    timestamps = [sample.timestamp for sample in dataset]
    label_end_times = [sample.label_end for sample in dataset]
    splitter = PurgedWalkForwardSplitter(
        train_days=SPLITTER_TRAIN_DAYS,
        test_days=SPLITTER_TEST_DAYS,
        n_folds=SPLITTER_N_FOLDS,
        embargo_days=SPLITTER_EMBARGO_DAYS,
    )
    validator = WalkForwardValidator(splitter=splitter)
    git_commit = resolve_git_commit()
    data_snapshot_id = compute_data_snapshot_id(paths=[FEATURE_STORE_DIR])

    candidate_sharpes = [
        _strategy_sharpe(dataset=dataset, splitter=splitter, field="signal"),
        _strategy_sharpe(dataset=dataset, splitter=splitter, field="random_signal"),
    ]
    starting_trial_count = how_many_trials()

    signal_report = _run_strategy(
        name=f"momentum_4h_20bar_{year}",
        signal_field="signal",
        dataset=dataset,
        timestamps=timestamps,
        label_end_times=label_end_times,
        validator=validator,
        candidate_sharpes=candidate_sharpes,
        git_commit=git_commit,
        data_snapshot_id=data_snapshot_id,
        symbols_used=symbols_used,
        year=year,
    )
    control_report = _run_strategy(
        name=f"random_control_momentum_pipeline_{year}",
        signal_field="random_signal",
        dataset=dataset,
        timestamps=timestamps,
        label_end_times=label_end_times,
        validator=validator,
        candidate_sharpes=candidate_sharpes,
        git_commit=git_commit,
        data_snapshot_id=data_snapshot_id,
        symbols_used=symbols_used,
        year=year,
    )

    ending_trial_count = how_many_trials()
    if ending_trial_count - starting_trial_count != 2:
        raise WS6IntegrationObstacle(
            f"Expected 2 new real WS1 trials for {year}, got {ending_trial_count - starting_trial_count}."
        )

    fold_trade_counts = {
        "signal": _fold_trade_counts(signal_report),
        "control": _fold_trade_counts(control_report),
    }

    return {
        "year": year,
        "date_range": {"start": start, "end": end},
        "sample_count": len(dataset),
        "symbols_used": symbols_used,
        "load_diagnostics": diagnostics,
        "fold_trade_counts": fold_trade_counts,
        "signal": _report_dict(signal_report),
        "random_control": _report_dict(control_report),
        "trial_count_before": starting_trial_count,
        "trial_count_after": ending_trial_count,
        "new_trials_logged": ending_trial_count - starting_trial_count,
        "log_trial_numbers_tail": [row["trial_number"] for row in ResearchLog().fetch_all()][-5:],
    }


def build_dataset(*, start: str, end: str) -> tuple[list[SignalSample], tuple[str, ...], dict[str, object]]:
    engine = FeatureEngine()
    universe = PointInTimeUniverse(top_n=30)
    membership = universe.membership_table()
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)
    real_dates = sorted(date for date in membership["date"].unique() if start_ts.date() <= date.date() <= end_ts.date())
    if not real_dates:
        raise WS6IntegrationObstacle("WS4 returned no universe dates inside the requested real-data window.")

    candidate_symbols = tuple(dict.fromkeys(symbol for date in real_dates[:10] for symbol in [row.symbol for row in universe.as_of(date)]))
    if not candidate_symbols:
        raise WS6IntegrationObstacle("WS4 returned no symbols for the real-data window.")

    # Load a warmup buffer before the window so early bars have full lookback
    warmup_start = (start_ts - pd.Timedelta(days=30)).isoformat()
    features_by_symbol: dict[str, pd.DataFrame] = {}
    one_minute_by_symbol: dict[str, pd.DataFrame] = {}
    load_errors: dict[str, str] = {}
    loaded_symbols: list[str] = []
    for symbol in candidate_symbols:
        try:
            features = engine.load_point_in_time_bars(
                symbol=symbol,
                start=warmup_start,
                end=end,
                timeframe=SIGNAL_TIMEFRAME,
            )
            bars_1m = engine.load_point_in_time_bars(
                symbol=symbol,
                start=start,
                end=end,
                timeframe="1m",
            )
        except Exception as exc:
            load_errors[symbol] = f"{type(exc).__name__}: {exc}"
            continue
        if len(features) < SIGNAL_LOOKBACK_BARS + 20:
            load_errors[symbol] = f"insufficient 4h rows: {len(features)} < {SIGNAL_LOOKBACK_BARS + 20}"
            continue
        if bars_1m.empty:
            load_errors[symbol] = "empty 1m bars"
            continue
        features_by_symbol[symbol] = features
        one_minute_by_symbol[symbol] = bars_1m
        loaded_symbols.append(symbol)

    diagnostics: dict[str, object] = {
        "ws4_candidate_symbols": candidate_symbols,
        "feature_engine_loaded_symbols": tuple(loaded_symbols),
        "feature_engine_load_errors": load_errors,
        "funding_scope": FUNDING_SCOPE,
    }
    if len(loaded_symbols) < MIN_WS4_SYMBOL_COVERAGE:
        raise WS6IntegrationObstacle(
            f"Only {len(loaded_symbols)}/{len(candidate_symbols)} WS4 symbols loaded. Diagnostics: {diagnostics}"
        )

    signals_by_symbol = {
        symbol: momentum_signal(features, lookback=SIGNAL_LOOKBACK_BARS)
        for symbol, features in features_by_symbol.items()
    }
    random_by_symbol = {
        symbol: _random_control_signal(signals, symbol=symbol)
        for symbol, signals in signals_by_symbol.items()
    }

    all_timestamps = sorted(set().union(*(set(features.index) for features in features_by_symbol.values())))
    dataset: list[SignalSample] = []
    symbols_used: set[str] = set()
    for timestamp in all_timestamps:
        if timestamp < start_ts + pd.Timedelta(days=5) or timestamp > end_ts:
            continue
        day = timestamp.date().isoformat()
        universe_symbols = {row.symbol for row in universe.as_of(day)}
        for symbol in sorted(universe_symbols):
            if symbol not in features_by_symbol:
                continue
            features = features_by_symbol[symbol]
            if timestamp not in features.index:
                continue
            trailing_returns = _trailing_returns(features=features, timestamp=timestamp)
            if not trailing_returns:
                continue
            planned_exit = timestamp + pd.Timedelta(hours=1)
            bars_1m = one_minute_by_symbol[symbol].loc[
                (one_minute_by_symbol[symbol].index >= timestamp) &
                (one_minute_by_symbol[symbol].index <= planned_exit)
            ]
            if bars_1m.empty:
                continue
            close = float(features.loc[timestamp, "close"])
            dataset.append(
                SignalSample(
                    timestamp=timestamp.to_pydatetime(),
                    label_end=planned_exit.to_pydatetime(),
                    symbol=symbol,
                    close=close,
                    signal=int(signals_by_symbol[symbol].loc[timestamp]),
                    random_signal=int(random_by_symbol[symbol].loc[timestamp]),
                    trailing_returns=trailing_returns,
                    bars_1m=_frame_to_rows(bars_1m),
                )
            )
            symbols_used.add(symbol)

    if not dataset:
        raise WS6IntegrationObstacle(f"Real WS2 + WS4 integration produced zero executable WS5 samples. Diagnostics: {diagnostics}")
    return dataset, tuple(sorted(symbols_used)), diagnostics


def _run_strategy(
    *,
    name: str,
    signal_field: str,
    dataset: list[SignalSample],
    timestamps: list[datetime],
    label_end_times: list[datetime],
    validator: WalkForwardValidator,
    candidate_sharpes: list[float],
    git_commit: str,
    data_snapshot_id: str,
    symbols_used: tuple[str, ...],
    year: int,
) -> StrategyReport:
    result = validator.run(
        dataset=dataset,
        timestamps=timestamps,
        label_end_times=label_end_times,
        evaluator=lambda train, test, fold: _evaluate_fold(test_slice=test, fold=fold, signal_field=signal_field),
        candidate_sharpes=candidate_sharpes,
        git_commit=git_commit,
        data_snapshot_id=data_snapshot_id,
        universe_definition="real-ws4-point-in-time-top30-with-ws45-luna-ftt-bound",
        params={
            "workstream": "WS6",
            "year": year,
            "strategy": name,
            "signal_definition": signal_definition(),
            "signal_field": signal_field,
            "symbols_used_from_ws4": symbols_used,
            "data_source": "FeatureEngine.load_point_in_time_bars",
            "execution_model": "src.execution.model.execute_trade",
            "funding_scope": FUNDING_SCOPE,
            "splitter": {
                "train_days": SPLITTER_TRAIN_DAYS,
                "test_days": SPLITTER_TEST_DAYS,
                "n_folds": SPLITTER_N_FOLDS,
                "embargo_days": SPLITTER_EMBARGO_DAYS,
            },
        },
    )
    returns = _aggregate_returns(result)
    profit_factor = _profit_factor(returns)
    deflated_pf = _deflated_profit_factor(returns=returns, candidate_sharpes=candidate_sharpes, profit_factor=profit_factor)
    return StrategyReport(
        name=name,
        result=result,
        profit_factor=profit_factor,
        deflated_profit_factor=deflated_pf,
        deflated_sharpe=float(result.aggregate["deflated_sharpe_ratio_raw"]),
        aggregate_sharpe=float(result.aggregate["aggregate_sharpe_ratio"]),
        total_return=sum(returns),
        trade_count=sum(1 for value in returns if value != 0.0),
        clears_bar=deflated_pf > 1.5,
    )


def _evaluate_fold(*, test_slice: list[SignalSample], fold: WalkForwardFold, signal_field: str) -> FoldBacktestResult:
    config = ExecutionConfig(stop_loss_pct=0.015, take_profit_pct=0.025, target_trade_risk_pct=0.01, max_leverage=5.0, min_leverage=0.1, slippage_bps=2.0)
    realized_returns: list[float] = []
    active_trades = 0
    symbols = set()
    for sample in test_slice:
        signal_value = getattr(sample, signal_field)
        if signal_value == 0:
            realized_returns.append(0.0)
            continue
        active_trades += 1
        symbols.add(sample.symbol)
        side = "long" if signal_value > 0 else "short"
        bars = pd.DataFrame(list(sample.bars_1m), index=pd.date_range(start=sample.timestamp, periods=len(sample.bars_1m), freq="1min"))
        trade = execute_trade(
            request=TradeRequest(
                symbol=sample.symbol,
                side=side,
                entry_time=pd.Timestamp(sample.timestamp),
                entry_price=sample.close,
                planned_exit_time=pd.Timestamp(sample.label_end),
                trailing_returns=sample.trailing_returns,
            ),
            bars_1m=bars,
            funding_rates=None,
            config=config,
            caller="ws6_momentum_research",
        )
        realized_returns.append(round(trade.net_return_pct, 10))
    return FoldBacktestResult(
        returns=tuple(realized_returns),
        metadata={
            "fold_number": fold.fold_number,
            "active_trades": active_trades,
            "symbols": ",".join(sorted(symbols)),
            "signal_field": signal_field,
            "execution_model": "src.execution.model.execute_trade",
        },
    )


def _strategy_sharpe(*, dataset: list[SignalSample], splitter: PurgedWalkForwardSplitter, field: str) -> float:
    returns: list[float] = []
    folds = splitter.split(
        timestamps=[sample.timestamp for sample in dataset],
        label_end_times=[sample.label_end for sample in dataset],
    )
    for fold in folds:
        test = [dataset[index] for index in fold.test_indices]
        returns.extend(_evaluate_fold(test_slice=test, fold=fold, signal_field=field).returns)
    return compute_sharpe_ratio(returns)


def _load_real_features_for_determinism() -> pd.DataFrame:
    engine = FeatureEngine()
    universe = PointInTimeUniverse(top_n=30)
    start = "2024-01-01 00:00:00+00:00"
    end = "2024-03-31 23:59:00+00:00"
    for row in universe.as_of("2024-01-01"):
        try:
            features = FeatureEngine().load_point_in_time_bars(
                symbol=row.symbol, start=start, end=end, timeframe=SIGNAL_TIMEFRAME
            )
        except Exception:
            continue
        if len(features) >= SIGNAL_LOOKBACK_BARS + 5:
            return features
    raise WS6IntegrationObstacle("Could not load any real WS2 feature frame for the determinism test.")


def _random_control_signal(signal: pd.Series, *, symbol: str) -> pd.Series:
    seed_material = f"{RANDOM_CONTROL_SEED}:{symbol}:{signal.index[0].isoformat()}:{signal.index[-1].isoformat()}:{len(signal)}"
    seed = int(hashlib.sha256(seed_material.encode("utf-8")).hexdigest()[:16], 16) % (2**32)
    rng = np.random.default_rng(seed)
    values = rng.choice(np.array([-1, 0, 1], dtype="int64"), size=len(signal))
    return pd.Series(values, index=signal.index, name="random_control_signal", dtype="int64")


def _trailing_returns(*, features: pd.DataFrame, timestamp: pd.Timestamp) -> tuple[float, ...]:
    location = features.index.get_loc(timestamp)
    if location < 20:
        return ()
    closes = features["close"].iloc[location - 20 : location + 1]
    returns = closes.pct_change().dropna()
    return tuple(float(value) for value in returns)


def _frame_to_rows(frame: pd.DataFrame) -> tuple[dict[str, float], ...]:
    return tuple(
        {
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
        }
        for _, row in frame.iterrows()
    )


def _series_digest(series: pd.Series) -> str:
    row_hashes = pd.util.hash_pandas_object(series, index=True).values.tobytes()
    return hashlib.sha256(row_hashes).hexdigest()


def _aggregate_returns(result: ValidationRunResult) -> list[float]:
    return list(result.all_returns)


def _profit_factor(returns: list[float]) -> float:
    gains = sum(value for value in returns if value > 0.0)
    losses = -sum(value for value in returns if value < 0.0)
    if losses == 0.0:
        return float("inf") if gains > 0.0 else 0.0
    return gains / losses


def _deflated_profit_factor(*, returns: list[float], candidate_sharpes: list[float], profit_factor: float) -> float:
    if profit_factor == float("inf"):
        return profit_factor
    dsr = compute_deflated_sharpe_details(returns=returns, candidate_sharpes=candidate_sharpes)
    return profit_factor * dsr.probability


def _fold_trade_counts(report: StrategyReport) -> list[int]:
    return [int(fold["metadata"]["active_trades"]) for fold in report.result.per_fold]


def _report_dict(report: StrategyReport) -> dict[str, object]:
    return {
        "name": report.name,
        "trial_number": report.result.trial_number,
        "candidate_batch_size": report.result.n_trials_used_for_deflation,
        "aggregate_sharpe": report.aggregate_sharpe,
        "deflated_sharpe": report.deflated_sharpe,
        "profit_factor": report.profit_factor,
        "deflated_profit_factor": report.deflated_profit_factor,
        "total_return": report.total_return,
        "trade_count": report.trade_count,
        "clears_deflated_pf_gt_1_5": report.clears_bar,
        "per_fold": report.result.per_fold,
    }


def _format_pct(value: float) -> str:
    return f"{value * 100:.1f}%"


if __name__ == "__main__":
    report = run_ws6_decision_gate()
    print("\n=== WS6 Momentum decision gate ===")
    print(f"signal_definition={report['signal_definition']}")
    print(f"determinism={report['determinism']}")
    print(f"funding_scope={report['funding_scope']}")
    print(f"random_control_seed={report['random_control_seed']}")
    print()
    print(f"{'year':<6} {'sig_dpf':>9} {'ctl_dpf':>9} {'sig_ret':>9} {'ctl_ret':>9} {'sig_tr':>7} {'ctl_tr':>7}  sig_fold_trades  ctl_fold_trades")
    for row in report["years"]:
        signal = row["signal"]
        control = row["random_control"]
        print(
            f"{row['year']:<6}"
            f" {signal['deflated_profit_factor']:>9.4f}"
            f" {control['deflated_profit_factor']:>9.4f}"
            f" {_format_pct(signal['total_return']):>9}"
            f" {_format_pct(control['total_return']):>9}"
            f" {signal['trade_count']:>7}"
            f" {control['trade_count']:>7}"
            f"  {row['fold_trade_counts']['signal']}"
            f"  {row['fold_trade_counts']['control']}"
        )

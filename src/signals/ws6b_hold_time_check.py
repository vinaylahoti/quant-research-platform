"""WS6b — hold-time diagnostic: re-run WS6 time-series momentum at 24h hold.

DIAGNOSTIC ONLY.  Everything is identical to WS6 except:
  planned_exit = timestamp + 24h   (was 1h in WS6)
Signal logic, lookback, threshold, universe, execution config, random control
seed and structure are all unchanged.

Purpose: confirm WS8b's finding that hold-time is not masking signal content,
across a second structurally different signal family (time-series momentum vs
cross-sectional momentum).
Decision rule: same as WS8b — report gap, compare against WS6 1h numbers.
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


# ── Only this constant differs from WS6 ──────────────────────────────────────
HOLD_HOURS = 24          # WS6 used 1; WS6b uses 24
# ─────────────────────────────────────────────────────────────────────────────

SIGNAL_TIMEFRAME = "4h"
SIGNAL_LOOKBACK_BARS = 20
SIGNAL_RULE = "long if 20-bar trailing 4h close-to-close return > 0, short if < 0, flat until lookback exists or return == 0"
MIN_WS4_SYMBOL_COVERAGE = 20
RANDOM_CONTROL_SEED = 20260628      # same seed as WS6
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
    # bars_1m NOT stored — sliced on demand to avoid 24× memory blowup


@dataclass(frozen=True)
class StrategyReport:
    name: str
    result: ValidationRunResult
    profit_factor: float
    deflated_profit_factor: float
    deflated_sharpe: float
    aggregate_sharpe: float
    total_return: float
    active_trades: int
    mean_return_per_active_trade: float
    clears_bar: bool


class WS6bObstacle(RuntimeError):
    pass


def signal_definition() -> dict[str, object]:
    return {
        "timeframe": SIGNAL_TIMEFRAME,
        "lookback_bars": SIGNAL_LOOKBACK_BARS,
        "hold_hours": HOLD_HOURS,
        "rule": SIGNAL_RULE,
        "output": "-1 short, 0 flat, +1 long",
    }


def run_ws6b_decision_gate() -> dict[str, object]:
    yearly_reports = []
    for year in YEARS:
        yearly_reports.append(run_year(year))
    return {
        "workstream": "WS6b",
        "hold_hours": HOLD_HOURS,
        "signal_definition": signal_definition(),
        "funding_scope": FUNDING_SCOPE,
        "random_control_seed": RANDOM_CONTROL_SEED,
        "years": yearly_reports,
    }


def run_year(year: int) -> dict[str, object]:
    start = f"{year}-01-01 00:00:00+00:00"
    end = f"{year}-12-31 23:59:00+00:00"
    dataset, symbols_used, diagnostics, one_minute_by_symbol = build_dataset(start=start, end=end)
    if len(dataset) < 360:
        raise WS6bObstacle(f"Not enough real WS2/WS4 samples for {year}: found {len(dataset)}")

    timestamps = [sample.timestamp for sample in dataset]
    label_end_times = [sample.label_end for sample in dataset]
    splitter = PurgedWalkForwardSplitter(
        train_days=SPLITTER_TRAIN_DAYS,
        test_days=SPLITTER_TEST_DAYS,
        n_folds=SPLITTER_N_FOLDS,
        embargo_days=SPLITTER_EMBARGO_DAYS,
    )

    probe_folds = splitter.split(timestamps=timestamps, label_end_times=label_end_times)
    fold1 = probe_folds[0]
    fold1_test_start = fold1.test_start
    fold1_test_end = fold1.test_end
    fold1_span_days = (fold1_test_end - fold1_test_start).total_seconds() / 86400
    calendar_ok = fold1_span_days >= SPLITTER_TEST_DAYS - 1
    print(
        f"[WS6b {year}] Calendar verification — fold 1: "
        f"test_start={fold1_test_start.isoformat()} "
        f"test_end={fold1_test_end.isoformat()} "
        f"span={fold1_span_days:.1f} days "
        f"(requested {SPLITTER_TEST_DAYS}) "
        f"{'OK' if calendar_ok else 'FAIL'}"
    )
    if not calendar_ok:
        raise WS6bObstacle(
            f"Fold 1 test window spans only {fold1_span_days:.1f} days — "
            f"calendar splitter not working. Expected ~{SPLITTER_TEST_DAYS} days."
        )

    validator = WalkForwardValidator(splitter=splitter)
    git_commit = resolve_git_commit()
    data_snapshot_id = compute_data_snapshot_id(paths=[FEATURE_STORE_DIR])

    candidate_sharpes = [
        _strategy_sharpe(dataset=dataset, splitter=splitter, field="signal", one_minute_by_symbol=one_minute_by_symbol),
        _strategy_sharpe(dataset=dataset, splitter=splitter, field="random_signal", one_minute_by_symbol=one_minute_by_symbol),
    ]
    starting_trial_count = how_many_trials()

    signal_report = _run_strategy(
        name=f"ws6b_momentum_4h_20bar_24h_hold_{year}",
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
        one_minute_by_symbol=one_minute_by_symbol,
    )
    control_report = _run_strategy(
        name=f"ws6b_random_control_24h_hold_{year}",
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
        one_minute_by_symbol=one_minute_by_symbol,
    )

    ending_trial_count = how_many_trials()
    if ending_trial_count - starting_trial_count != 2:
        raise WS6bObstacle(
            f"Expected 2 new WS1 trials for {year}, got {ending_trial_count - starting_trial_count}."
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
        "calendar_verification": {
            "fold": 1,
            "test_start": fold1_test_start.isoformat(),
            "test_end": fold1_test_end.isoformat(),
            "span_days": round(fold1_span_days, 1),
            "requested_days": SPLITTER_TEST_DAYS,
            "passed": calendar_ok,
        },
        "signal": _report_dict(signal_report),
        "random_control": _report_dict(control_report),
        "trial_count_before": starting_trial_count,
        "trial_count_after": ending_trial_count,
        "new_trials_logged": ending_trial_count - starting_trial_count,
        "log_trial_numbers_tail": [row["trial_number"] for row in ResearchLog().fetch_all()][-5:],
    }


def build_dataset(*, start: str, end: str) -> tuple[list[SignalSample], tuple[str, ...], dict[str, object], dict[str, pd.DataFrame]]:
    engine = FeatureEngine()
    universe = PointInTimeUniverse(top_n=30)
    membership = universe.membership_table()
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)
    real_dates = sorted(
        date for date in membership["date"].unique()
        if start_ts.date() <= date.date() <= end_ts.date()
    )
    if not real_dates:
        raise WS6bObstacle("WS4 returned no universe dates inside the requested window.")

    candidate_symbols = tuple(dict.fromkeys(
        symbol
        for date in real_dates[:10]
        for symbol in [row.symbol for row in universe.as_of(date)]
    ))
    if not candidate_symbols:
        raise WS6bObstacle("WS4 returned no symbols for the real-data window.")

    warmup_start = (start_ts - pd.Timedelta(days=30)).isoformat()
    features_by_symbol: dict[str, pd.DataFrame] = {}
    one_minute_by_symbol: dict[str, pd.DataFrame] = {}
    load_errors: dict[str, str] = {}
    loaded_symbols: list[str] = []

    for symbol in candidate_symbols:
        try:
            features = engine.load_point_in_time_bars(
                symbol=symbol, start=warmup_start, end=end, timeframe=SIGNAL_TIMEFRAME
            )
            bars_1m = engine.load_point_in_time_bars(
                symbol=symbol, start=start, end=end, timeframe="1m"
            )
        except Exception as exc:
            load_errors[symbol] = f"{type(exc).__name__}: {exc}"
            continue
        if len(features) < SIGNAL_LOOKBACK_BARS + 20:
            load_errors[symbol] = f"insufficient 4h rows: {len(features)}"
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
        raise WS6bObstacle(
            f"Only {len(loaded_symbols)}/{len(candidate_symbols)} symbols loaded. Diagnostics: {diagnostics}"
        )

    signals_by_symbol = {
        symbol: momentum_signal(features, lookback=SIGNAL_LOOKBACK_BARS)
        for symbol, features in features_by_symbol.items()
    }
    random_by_symbol = {
        symbol: _random_control_signal(signals, symbol=symbol)
        for symbol, signals in signals_by_symbol.items()
    }

    all_timestamps = sorted(set().union(*(set(f.index) for f in features_by_symbol.values())))
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
            trailing_rets = _trailing_returns(features=features, timestamp=timestamp)
            if not trailing_rets:
                continue
            # ── Only change vs WS6: 24h hold instead of 1h ──────────────────
            planned_exit = timestamp + pd.Timedelta(hours=HOLD_HOURS)
            # ────────────────────────────────────────────────────────────────
            sym_1m = one_minute_by_symbol[symbol]
            has_bars = ((sym_1m.index >= timestamp) & (sym_1m.index <= planned_exit)).any()
            if not has_bars:
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
                    trailing_returns=trailing_rets,
                )
            )
            symbols_used.add(symbol)

    if not dataset:
        raise WS6bObstacle(f"Zero executable WS5 samples built. Diagnostics: {diagnostics}")
    return dataset, tuple(sorted(symbols_used)), diagnostics, one_minute_by_symbol


def _random_control_signal(signal: pd.Series, *, symbol: str) -> pd.Series:
    seed_material = f"{RANDOM_CONTROL_SEED}:{symbol}:{signal.index[0].isoformat()}:{signal.index[-1].isoformat()}:{len(signal)}"
    seed = int(hashlib.sha256(seed_material.encode("utf-8")).hexdigest()[:16], 16) % (2**32)
    rng = np.random.default_rng(seed)
    values = rng.choice(np.array([-1, 0, 1], dtype="int64"), size=len(signal))
    return pd.Series(values, index=signal.index, name="random_control_signal", dtype="int64")


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
    one_minute_by_symbol: dict[str, pd.DataFrame],
) -> StrategyReport:
    result = validator.run(
        dataset=dataset,
        timestamps=timestamps,
        label_end_times=label_end_times,
        evaluator=lambda train, test, fold: _evaluate_fold(
            test_slice=test, fold=fold, signal_field=signal_field,
            one_minute_by_symbol=one_minute_by_symbol,
        ),
        candidate_sharpes=candidate_sharpes,
        git_commit=git_commit,
        data_snapshot_id=data_snapshot_id,
        universe_definition="real-ws4-point-in-time-top30-with-ws45-luna-ftt-bound",
        params={
            "workstream": "WS6b",
            "year": year,
            "strategy": name,
            "hold_hours": HOLD_HOURS,
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
    returns = list(result.all_returns)
    active_trades = sum(int(f["metadata"]["active_trades"]) for f in result.per_fold)
    total_return = sum(float(f["metadata"]["gross_active_return"]) for f in result.per_fold)
    mean_rpt = total_return / active_trades if active_trades > 0 else 0.0
    profit_factor = _profit_factor(returns)
    deflated_pf = _deflated_profit_factor(
        returns=returns, candidate_sharpes=candidate_sharpes, profit_factor=profit_factor
    )
    return StrategyReport(
        name=name,
        result=result,
        profit_factor=profit_factor,
        deflated_profit_factor=deflated_pf,
        deflated_sharpe=float(result.aggregate["deflated_sharpe_ratio_raw"]),
        aggregate_sharpe=float(result.aggregate["aggregate_sharpe_ratio"]),
        total_return=total_return,
        active_trades=active_trades,
        mean_return_per_active_trade=mean_rpt,
        clears_bar=deflated_pf > 1.5,
    )


def _evaluate_fold(
    *,
    test_slice: list[SignalSample],
    fold: WalkForwardFold,
    signal_field: str,
    one_minute_by_symbol: dict[str, pd.DataFrame],
) -> FoldBacktestResult:
    config = ExecutionConfig(
        stop_loss_pct=0.015, take_profit_pct=0.025,
        target_trade_risk_pct=0.01, max_leverage=5.0,
        min_leverage=0.1, slippage_bps=2.0,
    )
    realized_returns: list[float] = []
    active_trades = 0
    gross_active_return = 0.0
    symbols = set()
    for sample in test_slice:
        signal_value = getattr(sample, signal_field)
        if signal_value == 0:
            realized_returns.append(0.0)
            continue
        active_trades += 1
        symbols.add(sample.symbol)
        side = "long" if signal_value > 0 else "short"
        entry_ts = pd.Timestamp(sample.timestamp)
        exit_ts = pd.Timestamp(sample.label_end)
        sym_1m = one_minute_by_symbol[sample.symbol]
        bars = sym_1m.loc[(sym_1m.index >= entry_ts) & (sym_1m.index <= exit_ts)]
        if bars.empty:
            realized_returns.append(0.0)
            continue
        trade = execute_trade(
            request=TradeRequest(
                symbol=sample.symbol,
                side=side,
                entry_time=entry_ts,
                entry_price=sample.close,
                planned_exit_time=exit_ts,
                trailing_returns=sample.trailing_returns,
            ),
            bars_1m=bars,
            funding_rates=None,
            config=config,
            caller="ws6b_hold_time_check",
        )
        net = round(trade.net_return_pct, 10)
        realized_returns.append(net)
        gross_active_return += net
    return FoldBacktestResult(
        returns=tuple(realized_returns),
        metadata={
            "fold_number": fold.fold_number,
            "active_trades": active_trades,
            "gross_active_return": round(gross_active_return, 10),
            "symbols": ",".join(sorted(symbols)),
            "signal_field": signal_field,
            "execution_model": "src.execution.model.execute_trade",
        },
    )


def _strategy_sharpe(
    *,
    dataset: list[SignalSample],
    splitter: PurgedWalkForwardSplitter,
    field: str,
    one_minute_by_symbol: dict[str, pd.DataFrame],
) -> float:
    returns: list[float] = []
    folds = splitter.split(
        timestamps=[s.timestamp for s in dataset],
        label_end_times=[s.label_end for s in dataset],
    )
    for fold in folds:
        test = [dataset[i] for i in fold.test_indices]
        returns.extend(_evaluate_fold(
            test_slice=test, fold=fold, signal_field=field,
            one_minute_by_symbol=one_minute_by_symbol,
        ).returns)
    return compute_sharpe_ratio(returns)


def _trailing_returns(*, features: pd.DataFrame, timestamp: pd.Timestamp) -> tuple[float, ...]:
    location = features.index.get_loc(timestamp)
    if location < 20:
        return ()
    closes = features["close"].iloc[location - 20 : location + 1]
    returns = closes.pct_change().dropna()
    return tuple(float(v) for v in returns)


def _profit_factor(returns: list[float]) -> float:
    gains = sum(v for v in returns if v > 0.0)
    losses = -sum(v for v in returns if v < 0.0)
    if losses == 0.0:
        return float("inf") if gains > 0.0 else 0.0
    return gains / losses


def _deflated_profit_factor(
    *, returns: list[float], candidate_sharpes: list[float], profit_factor: float
) -> float:
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
        "active_trades": report.active_trades,
        "mean_return_per_active_trade_pct": round(report.mean_return_per_active_trade * 100, 4),
        "clears_deflated_pf_gt_1_5": report.clears_bar,
        "per_fold": report.result.per_fold,
    }


if __name__ == "__main__":
    report = run_ws6b_decision_gate()
    print("\n=== WS6b Hold-time diagnostic (24h hold) ===")
    print(f"signal_definition={report['signal_definition']}")
    print(f"funding_scope={report['funding_scope']}")
    print()
    hdr = (
        f"{'year':<6} {'sig_dpf':>9} {'ctl_dpf':>9} "
        f"{'sig_mrpt':>10} {'ctl_mrpt':>10} "
        f"{'sig_tr':>7} {'ctl_tr':>7}  "
        f"sig_fold_trades  ctl_fold_trades  cal_span_days"
    )
    print(hdr)
    for row in report["years"]:
        sig = row["signal"]
        ctl = row["random_control"]
        cal = row["calendar_verification"]
        print(
            f"{row['year']:<6}"
            f" {sig['deflated_profit_factor']:>9.4f}"
            f" {ctl['deflated_profit_factor']:>9.4f}"
            f" {sig['mean_return_per_active_trade_pct']:>9.4f}%"
            f" {ctl['mean_return_per_active_trade_pct']:>9.4f}%"
            f" {sig['active_trades']:>7}"
            f" {ctl['active_trades']:>7}"
            f"  {row['fold_trade_counts']['signal']}"
            f"  {row['fold_trade_counts']['control']}"
            f"  {cal['span_days']}"
        )

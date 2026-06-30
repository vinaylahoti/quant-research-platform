"""Validation harness that runs folds, aggregates results, and logs them."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from statistics import fmean, pstdev
from typing import Callable

from src.research_log.store import how_many_trials, log_experiment
from src.validation.metrics import compute_deflated_sharpe_details, compute_sharpe_ratio
from src.validation.splitter import PurgedWalkForwardSplitter, WalkForwardFold


@dataclass(frozen=True)
class FoldBacktestResult:
    """The output of a backtest on one test fold."""

    returns: tuple[float, ...]
    metadata: dict[str, float | int | str]


@dataclass(frozen=True)
class ValidationRunResult:
    """Full validation output for one walk-forward run."""

    trial_number: int
    n_trials_used_for_deflation: int
    per_fold: tuple[dict[str, object], ...]
    aggregate: dict[str, object]
    passes_decision_gate: bool
    all_returns: tuple[float, ...]  # actual per-sample returns; not persisted to DB


class WalkForwardValidator:
    """Run a deterministic walk-forward validation and auto-log the result."""

    def __init__(self, splitter: PurgedWalkForwardSplitter) -> None:
        self.splitter = splitter

    def run(
        self,
        *,
        dataset: list[object],
        timestamps: list,
        label_end_times: list,
        evaluator: Callable[[list[object], list[object], WalkForwardFold], FoldBacktestResult],
        candidate_sharpes: list[float],
        git_commit: str,
        data_snapshot_id: str,
        universe_definition: str,
        params: dict[str, object],
        db_path: Path | None = None,
    ) -> ValidationRunResult:
        """Execute all folds, aggregate the metrics, and persist one log row."""

        folds = self.splitter.split(timestamps=timestamps, label_end_times=label_end_times)
        per_fold_results: list[dict[str, object]] = []
        all_out_of_sample_returns: list[float] = []

        for fold in folds:
            train_slice = [dataset[index] for index in fold.train_indices]
            test_slice = [dataset[index] for index in fold.test_indices]
            fold_backtest = evaluator(train_slice, test_slice, fold)
            fold_returns = list(fold_backtest.returns)
            all_out_of_sample_returns.extend(fold_returns)
            fold_sharpe = compute_sharpe_ratio(fold_returns)
            total_return = sum(fold_returns)

            per_fold_results.append(
                {
                    "fold_number": fold.fold_number,
                    "train_size": len(fold.train_indices),
                    "test_size": len(fold.test_indices),
                    "purged_count": len(fold.purged_indices),
                    "embargo_days": fold.embargo_days,
                    "train_start": fold.train_start.isoformat(),
                    "train_end": fold.train_end.isoformat(),
                    "test_start": fold.test_start.isoformat(),
                    "test_end": fold.test_end.isoformat(),
                    "total_return": round(total_return, 8),
                    "mean_return": round(fmean(fold_returns), 8),
                    "sharpe_ratio": round(fold_sharpe, 8),
                    "metadata": dict(fold_backtest.metadata),
                }
            )

        fold_sharpes = [float(fold["sharpe_ratio"]) for fold in per_fold_results]
        fold_total_returns = [float(fold["total_return"]) for fold in per_fold_results]
        research_log_trial_count = how_many_trials(db_path=db_path) + 1
        aggregate_sharpe = compute_sharpe_ratio(all_out_of_sample_returns)
        deflated_sharpe = compute_deflated_sharpe_details(
            all_out_of_sample_returns,
            candidate_sharpes=candidate_sharpes,
        )
        dispersion = {
            "sharpe_std": round(pstdev(fold_sharpes), 8),
            "return_std": round(pstdev(fold_total_returns), 8),
            "sharpe_min": round(min(fold_sharpes), 8),
            "sharpe_max": round(max(fold_sharpes), 8),
        }
        passes_decision_gate = all(value > 0.0 for value in fold_total_returns) and deflated_sharpe.probability > 0.5

        aggregate = {
            "n_folds": len(per_fold_results),
            "fold_sharpes": fold_sharpes,
            "fold_total_returns": fold_total_returns,
            "dispersion": dispersion,
            "aggregate_sharpe_ratio": round(aggregate_sharpe, 8),
            "deflated_sharpe_ratio": round(deflated_sharpe.probability, 8),
            "deflated_sharpe_ratio_raw": deflated_sharpe.probability,
            "deflated_sharpe_benchmark": deflated_sharpe.benchmark_sharpe,
            "deflated_sharpe_z_score": deflated_sharpe.z_score,
            "deflated_sharpe_standard_error": deflated_sharpe.sharpe_standard_error,
            "candidate_batch_size": deflated_sharpe.candidate_batch_size,
            "candidate_sharpe_variance": deflated_sharpe.candidate_sharpe_variance,
            "research_log_trial_count": research_log_trial_count,
            "n_trials": research_log_trial_count,
        }
        metrics = {
            "per_fold": per_fold_results,
            "aggregate": aggregate,
            "n_trials": research_log_trial_count,
            "candidate_batch_size": deflated_sharpe.candidate_batch_size,
            "passes_decision_gate": passes_decision_gate,
        }
        trial_number = log_experiment(
            git_commit=git_commit,
            data_snapshot_id=data_snapshot_id,
            universe_definition=universe_definition,
            params=params,
            metrics=metrics,
            db_path=db_path,
        )

        return ValidationRunResult(
            trial_number=trial_number,
            n_trials_used_for_deflation=deflated_sharpe.candidate_batch_size,
            per_fold=tuple(per_fold_results),
            aggregate=aggregate,
            passes_decision_gate=passes_decision_gate,
            all_returns=tuple(all_out_of_sample_returns),
        )

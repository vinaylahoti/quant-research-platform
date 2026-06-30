"""
WS3 decision-gate proof.

This test proves that:
- validation runs across five folds
- per-fold dispersion is reported, not only an average
- the validation harness auto-logs every run
- repeating the same run with the same data/code version is deterministic

Splitter parameters are calendar-time durations (train_days, test_days,
embargo_days), not raw sample counts.  The synthetic dataset spans 200 days
of hourly data so that five folds of 14-day train + 7-day test each fit.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import json
import math
from pathlib import Path
import shutil
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.research_log.metadata import compute_data_snapshot_id, resolve_git_commit
from src.research_log.store import ResearchLog
from src.validation.runner import FoldBacktestResult, WalkForwardValidator
from src.validation.splitter import PurgedWalkForwardSplitter, WalkForwardFold


@dataclass(frozen=True)
class SyntheticSample:
    timestamp: datetime
    label_end: datetime
    feature_value: float
    forward_return: float


def _make_synthetic_dataset() -> list[SyntheticSample]:
    """200 days of hourly samples — enough for 5 folds of 14d train + 7d test."""
    samples: list[SyntheticSample] = []
    start = datetime(2024, 1, 1, tzinfo=UTC)
    hold_period = timedelta(hours=3)
    n_hours = 200 * 24  # 4800 samples

    for index in range(n_hours):
        timestamp = start + timedelta(hours=index)
        feature_value = math.sin(index / 2.7) + (((index % 7) - 3) * 0.18)
        forward_return = (0.016 * feature_value) + (0.0025 * math.cos(index / 5.0))
        samples.append(
            SyntheticSample(
                timestamp=timestamp,
                label_end=timestamp + hold_period,
                feature_value=feature_value,
                forward_return=forward_return,
            )
        )

    return samples


def _evaluate_fold(
    train_slice: list[SyntheticSample],
    test_slice: list[SyntheticSample],
    fold: WalkForwardFold,
) -> FoldBacktestResult:
    numerator = sum(sample.feature_value * sample.forward_return for sample in train_slice)
    denominator = sum(sample.feature_value**2 for sample in train_slice)
    beta = 0.0 if denominator == 0.0 else numerator / denominator

    realized_returns: list[float] = []
    for sample in test_slice:
        predicted_return = beta * sample.feature_value
        position = 1.0 if predicted_return >= 0.0 else -1.0
        realized_returns.append(position * sample.forward_return)

    return FoldBacktestResult(
        returns=tuple(round(value, 10) for value in realized_returns),
        metadata={
            "beta": round(beta, 8),
            "fold_number": fold.fold_number,
        },
    )


def _synthetic_candidate_sharpes() -> list[float]:
    return [3.25, 2.9, 2.45, 1.85, 1.1]


def run_ws3_decision_gate_test() -> str:
    temp_root = Path("tests/.tmp_ws3")
    if temp_root.exists():
        shutil.rmtree(temp_root)
    temp_root.mkdir(parents=True, exist_ok=True)

    db_path = temp_root / "research_log.db"
    snapshot_marker = temp_root / "snapshot_marker.txt"
    snapshot_marker.write_text("ws3-synthetic-series-v2", encoding="utf-8")

    dataset = _make_synthetic_dataset()
    timestamps = [sample.timestamp for sample in dataset]
    label_end_times = [sample.label_end for sample in dataset]

    # 14-day train, 7-day test, 5 folds, 1-day embargo.
    # Total calendar consumed: 5 * (14 + 1 + 7) = 110 days — fits in 200-day dataset.
    splitter = PurgedWalkForwardSplitter(
        train_days=14,
        test_days=7,
        n_folds=5,
        embargo_days=1,
    )
    validator = WalkForwardValidator(splitter=splitter)
    git_commit = resolve_git_commit()
    data_snapshot_id = compute_data_snapshot_id(paths=[snapshot_marker])
    params = {
        "model": "deterministic_linear_sign",
        "n_folds": 5,
        "purge_horizon_hours": 3,
        "embargo_days": 1,
    }

    first_result = validator.run(
        dataset=dataset,
        timestamps=timestamps,
        label_end_times=label_end_times,
        evaluator=_evaluate_fold,
        candidate_sharpes=_synthetic_candidate_sharpes(),
        git_commit=git_commit,
        data_snapshot_id=data_snapshot_id,
        universe_definition="survivorship-biased-plumbing-check",
        params=params,
        db_path=db_path,
    )
    second_result = validator.run(
        dataset=dataset,
        timestamps=timestamps,
        label_end_times=label_end_times,
        evaluator=_evaluate_fold,
        candidate_sharpes=_synthetic_candidate_sharpes(),
        git_commit=git_commit,
        data_snapshot_id=data_snapshot_id,
        universe_definition="survivorship-biased-plumbing-check",
        params=params,
        db_path=db_path,
    )

    log_rows = ResearchLog(db_path=db_path).fetch_all()
    first_metrics = json.loads(log_rows[0]["metrics_json"])
    second_metrics = json.loads(log_rows[1]["metrics_json"])

    assert len(first_result.per_fold) == 5, "Expected five folds in the validation run."
    assert first_result.per_fold == second_result.per_fold, "Per-fold results should be deterministic."
    assert first_result.aggregate["dispersion"]["sharpe_std"] > 0.0, "Expected non-zero fold dispersion."
    assert first_result.passes_decision_gate, "Expected the synthetic run to pass the decision gate."
    assert len(log_rows) == 2, f"Expected 2 logged validation runs, found {len(log_rows)}."
    assert first_metrics["n_trials"] == 1, f"Expected first run to use n_trials=1, got {first_metrics['n_trials']}."
    assert second_metrics["n_trials"] == 2, f"Expected second run to use n_trials=2, got {second_metrics['n_trials']}."
    assert first_metrics["candidate_batch_size"] == 5, f"Expected 5 candidate Sharpes, got {first_metrics['candidate_batch_size']}."

    # Verify each fold's test window actually spans the requested calendar duration.
    for fold_dict in first_result.per_fold:
        test_start = datetime.fromisoformat(fold_dict["test_start"])
        test_end = datetime.fromisoformat(fold_dict["test_end"])
        span_days = (test_end - test_start).total_seconds() / 86400
        assert span_days >= 6.5, (
            f"Fold {fold_dict['fold_number']} test window spans only {span_days:.1f} days "
            f"(expected ~7). Calendar splitter is not working correctly."
        )

    fold_sharpes = [fold["sharpe_ratio"] for fold in first_result.per_fold]
    purged_counts = [fold["purged_count"] for fold in first_result.per_fold]

    return (
        "WS3 decision gate passed | "
        f"fold_sharpes={fold_sharpes} | "
        f"dispersion={first_result.aggregate['dispersion']} | "
        f"purged_counts={purged_counts} | "
        f"deterministic_per_fold={first_result.per_fold == second_result.per_fold} | "
        f"deflated_sharpe={first_result.aggregate['deflated_sharpe_ratio_raw']} | "
        f"candidate_batch_size={first_result.aggregate['candidate_batch_size']} | "
        f"trial_numbers={[row['trial_number'] for row in log_rows]}"
    )


if __name__ == "__main__":
    print(run_ws3_decision_gate_test())

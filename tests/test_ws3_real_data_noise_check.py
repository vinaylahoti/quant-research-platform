"""
WS3 real-data leak check.

This uses the actual BTCUSDT feature store data and runs the same walk-forward
validation harness with a deterministic random long/short signal. A healthy
splitter should not turn pure noise into a stable positive edge.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from random import Random
import json
import shutil
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from config.settings import FEATURE_STORE_DIR
from src.data.featurestore import FeatureStore
from src.research_log.metadata import compute_data_snapshot_id, resolve_git_commit
from src.research_log.store import ResearchLog
from src.validation.runner import FoldBacktestResult, WalkForwardValidator
from src.validation.splitter import PurgedWalkForwardSplitter, WalkForwardFold


@dataclass(frozen=True)
class RealSample:
    timestamp: object
    label_end: object
    forward_return: float


def _load_btcusdt_real_samples() -> list[RealSample]:
    store = FeatureStore()
    frame = store.load(
        symbol="BTCUSDT",
        start="2024-01-01 00:00:00+00:00",
        end="2024-12-31 23:59:00+00:00",
        timeframe="1h",
    )
    closes = frame["close"].tolist()
    timestamps = list(frame.index)
    hold_bars = 3
    samples: list[RealSample] = []

    for index in range(len(frame) - hold_bars):
        forward_return = (closes[index + hold_bars] / closes[index]) - 1.0
        samples.append(
            RealSample(
                timestamp=timestamps[index].to_pydatetime(),
                label_end=(timestamps[index] + timedelta(hours=hold_bars)).to_pydatetime(),
                forward_return=float(forward_return),
            )
        )

    return samples


def _evaluate_random_noise_fold(
    train_slice: list[RealSample],
    test_slice: list[RealSample],
    fold: WalkForwardFold,
) -> FoldBacktestResult:
    del train_slice

    positions = [1.0] * (len(test_slice) // 2) + [-1.0] * (len(test_slice) - (len(test_slice) // 2))
    rng = Random(20240628 + fold.fold_number)
    rng.shuffle(positions)
    realized_returns = [
        position * sample.forward_return
        for position, sample in zip(positions, test_slice)
    ]

    return FoldBacktestResult(
        returns=tuple(round(value, 10) for value in realized_returns),
        metadata={"seed": 20240628 + fold.fold_number},
    )


def _noise_candidate_sharpes() -> list[float]:
    return [0.95, 0.55, 0.2, -0.1, -0.45, 0.72, 0.31, 0.05, -0.22, -0.58]


def run_ws3_real_data_noise_check() -> str:
    temp_root = Path("tests/.tmp_ws3_real_noise")
    if temp_root.exists():
        shutil.rmtree(temp_root)
    temp_root.mkdir(parents=True, exist_ok=True)

    db_path = temp_root / "research_log.db"
    dataset = _load_btcusdt_real_samples()
    # 1000h ≈ 42 days train, 500h ≈ 21 days test, embargo 24h = 1 day.
    splitter = PurgedWalkForwardSplitter(
        train_days=42,
        test_days=21,
        n_folds=5,
        embargo_days=1,
    )
    validator = WalkForwardValidator(splitter=splitter)
    result = validator.run(
        dataset=dataset,
        timestamps=[sample.timestamp for sample in dataset],
        label_end_times=[sample.label_end for sample in dataset],
        evaluator=_evaluate_random_noise_fold,
        candidate_sharpes=_noise_candidate_sharpes(),
        git_commit=resolve_git_commit(),
        data_snapshot_id=compute_data_snapshot_id(paths=[FEATURE_STORE_DIR / "symbol=BTCUSDT"]),
        universe_definition="survivorship-biased-btcusdt-real-noise-check",
        params={
            "model": "deterministic_random_long_short",
            "symbol": "BTCUSDT",
            "timeframe": "1h",
            "date_start": "2024-01-01",
            "date_end": "2024-12-31",
            "hold_bars": 3,
            "train_days": 42,
            "test_days": 21,
            "n_folds": 5,
            "embargo_days": 1,
        },
        db_path=db_path,
    )

    rows = ResearchLog(db_path=db_path).fetch_all()
    metrics = json.loads(rows[0]["metrics_json"])
    fold_sharpes = [float(fold["sharpe_ratio"]) for fold in result.per_fold]

    assert len(result.per_fold) == 5, "Expected five folds."
    assert any(value < 0.0 for value in fold_sharpes), "Expected at least one negative fold Sharpe for a noise signal."
    assert any(value > 0.0 for value in fold_sharpes), "Expected at least one positive fold Sharpe for a noise signal."
    assert max(abs(value) for value in fold_sharpes) < 2.5, f"Noise signal produced an unexpectedly strong fold Sharpe series: {fold_sharpes}"
    assert abs(sum(fold_sharpes) / len(fold_sharpes)) < 0.5, f"Noise signal should stay near zero on average, got {fold_sharpes}"
    assert not result.passes_decision_gate, "A random signal should not pass the WS3 decision gate."
    assert metrics["n_trials"] == 1, f"Expected n_trials=1 for the isolated leak check, got {metrics['n_trials']}."
    assert metrics["candidate_batch_size"] == 10, f"Expected candidate batch size 10, got {metrics['candidate_batch_size']}."
    assert result.aggregate["deflated_sharpe_ratio_raw"] < 0.95, "Noise-check DSR should stay meaningfully below 1.0."

    return (
        "WS3 real-data noise check passed | "
        f"fold_sharpes={fold_sharpes} | "
        f"fold_total_returns={result.aggregate['fold_total_returns']} | "
        f"dispersion={result.aggregate['dispersion']} | "
        f"aggregate_sharpe={result.aggregate['aggregate_sharpe_ratio']} | "
        f"deflated_sharpe={result.aggregate['deflated_sharpe_ratio_raw']} | "
        f"candidate_batch_size={result.aggregate['candidate_batch_size']} | "
        f"passes_decision_gate={result.passes_decision_gate}"
    )


if __name__ == "__main__":
    print(run_ws3_real_data_noise_check())

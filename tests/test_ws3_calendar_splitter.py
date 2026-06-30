"""
WS3 calendar-splitter decision-gate proof.

Proves that PurgedWalkForwardSplitter's test windows span the requested
calendar duration regardless of how many samples fall in each window.
This is the property that was broken by the old sample-count-based splitter:
on a cross-sectional dataset with N symbols per timestamp, test_size=270
samples covered only 270/N timestamps = a few calendar hours, not 270 days.

Three scenarios are tested:
  1. Dense cross-sectional dataset (30 symbols × 6 4h-bars/day = 180 samples/day)
     — previously the bug scenario.
  2. Sparse time-series dataset (1 symbol × 24 1h-bars/day = 24 samples/day)
     — should also span correct calendar duration.
  3. Uneven density (symbol count varies month to month)
     — fold windows must still span full test_days regardless.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.validation.splitter import PurgedWalkForwardSplitter


def _make_cross_sectional(
    *,
    start: datetime,
    n_days: int,
    n_symbols: int,
    bar_hours: int = 4,
    hold_hours: int = 1,
) -> tuple[list[datetime], list[datetime]]:
    """Simulate a cross-sectional dataset: n_symbols samples per bar."""
    timestamps: list[datetime] = []
    label_ends: list[datetime] = []
    bars_per_day = 24 // bar_hours
    for day in range(n_days):
        for bar in range(bars_per_day):
            ts = start + timedelta(days=day, hours=bar * bar_hours)
            for _ in range(n_symbols):
                timestamps.append(ts)
                label_ends.append(ts + timedelta(hours=hold_hours))
    return timestamps, label_ends


def _make_time_series(
    *,
    start: datetime,
    n_days: int,
    bar_hours: int = 1,
    hold_hours: int = 3,
) -> tuple[list[datetime], list[datetime]]:
    timestamps: list[datetime] = []
    label_ends: list[datetime] = []
    bars_per_day = 24 // bar_hours
    for day in range(n_days):
        for bar in range(bars_per_day):
            ts = start + timedelta(days=day, hours=bar * bar_hours)
            timestamps.append(ts)
            label_ends.append(ts + timedelta(hours=hold_hours))
    return timestamps, label_ends


def _span_days(fold_dict_or_fold, timestamps) -> float:
    """Calendar days between first and last test sample timestamp."""
    test_start = timestamps[fold_dict_or_fold.test_indices[0]]
    test_end = timestamps[fold_dict_or_fold.test_indices[-1]]
    return (test_end - test_start).total_seconds() / 86400


def run_calendar_splitter_decision_gate() -> str:
    start = datetime(2022, 1, 1, tzinfo=UTC)
    train_days = 30
    test_days = 90
    embargo_days = 1
    n_folds = 3

    # ── Scenario 1: dense cross-sectional (30 symbols, 4h bars) ──────────────
    # Old sample-count splitter with test_size=270 would cover only 270/(30*6)=
    # 1.5 calendar days here.  Calendar splitter must cover ~90 days.
    total_days_needed = n_folds * (train_days + embargo_days + test_days) + 10
    xs_ts, xs_le = _make_cross_sectional(
        start=start, n_days=total_days_needed, n_symbols=30, bar_hours=4
    )
    splitter = PurgedWalkForwardSplitter(
        train_days=train_days, test_days=test_days, n_folds=n_folds, embargo_days=embargo_days
    )
    xs_folds = splitter.split(timestamps=xs_ts, label_end_times=xs_le)

    assert len(xs_folds) == n_folds, f"Expected {n_folds} folds, got {len(xs_folds)}"
    for fold in xs_folds:
        span = _span_days(fold, xs_ts)
        # Test window should span at least test_days - 1 (one bar width tolerance)
        assert span >= test_days - 1, (
            f"Cross-sectional fold {fold.fold_number}: test window spans only {span:.1f} days "
            f"(expected ~{test_days}). Calendar splitter broken."
        )
        # Samples per fold should be proportional to symbols × bars_per_day × test_days
        expected_samples = 30 * 6 * test_days
        actual_samples = len(fold.test_indices)
        assert abs(actual_samples - expected_samples) < expected_samples * 0.05, (
            f"Cross-sectional fold {fold.fold_number}: {actual_samples} test samples, "
            f"expected ~{expected_samples} (30 syms × 6 bars/day × {test_days} days)."
        )

    # Folds must not overlap in calendar time
    for i in range(len(xs_folds) - 1):
        end_i = xs_ts[xs_folds[i].test_indices[-1]]
        start_next = xs_ts[xs_folds[i + 1].test_indices[0]]
        assert start_next > end_i, (
            f"Fold {i+1} and fold {i+2} test windows overlap: "
            f"{end_i} >= {start_next}"
        )

    # ── Scenario 2: sparse time-series (1 symbol, 1h bars) ───────────────────
    ts_ts, ts_le = _make_time_series(start=start, n_days=total_days_needed)
    ts_folds = splitter.split(timestamps=ts_ts, label_end_times=ts_le)

    assert len(ts_folds) == n_folds
    for fold in ts_folds:
        span = _span_days(fold, ts_ts)
        assert span >= test_days - 1, (
            f"Time-series fold {fold.fold_number}: test window spans only {span:.1f} days."
        )

    # ── Scenario 3: uneven density (symbol count doubles mid-year) ────────────
    uneven_ts: list[datetime] = []
    uneven_le: list[datetime] = []
    for day in range(total_days_needed):
        n_sym = 10 if day < total_days_needed // 2 else 25
        for bar in range(6):
            ts = start + timedelta(days=day, hours=bar * 4)
            for _ in range(n_sym):
                uneven_ts.append(ts)
                uneven_le.append(ts + timedelta(hours=1))

    uneven_folds = splitter.split(timestamps=uneven_ts, label_end_times=uneven_le)
    assert len(uneven_folds) == n_folds
    for fold in uneven_folds:
        span = _span_days(fold, uneven_ts)
        assert span >= test_days - 1, (
            f"Uneven fold {fold.fold_number}: test window spans only {span:.1f} days "
            f"despite varying symbol count."
        )

    # ── Purging: train samples whose label_end overlaps the test window ───────
    # Use a dataset with a long hold period to force purges.
    purge_ts, purge_le = _make_cross_sectional(
        start=start,
        n_days=total_days_needed,
        n_symbols=5,
        bar_hours=4,
        hold_hours=48,  # 2-day hold period bleeds across embargo
    )
    purge_splitter = PurgedWalkForwardSplitter(
        train_days=train_days, test_days=test_days, n_folds=1, embargo_days=embargo_days
    )
    purge_folds = purge_splitter.split(timestamps=purge_ts, label_end_times=purge_le)
    assert len(purge_folds[0].purged_indices) > 0, (
        "Expected purged samples when hold_hours=48 crosses the test window start."
    )
    # No purged index should appear in train_indices
    purged_set = set(purge_folds[0].purged_indices)
    for idx in purge_folds[0].train_indices:
        assert idx not in purged_set, f"Purged index {idx} appeared in train_indices."

    return (
        f"Calendar splitter decision gate passed | "
        f"cross_sectional_fold_spans_days={[round(_span_days(f, xs_ts), 1) for f in xs_folds]} | "
        f"time_series_fold_spans_days={[round(_span_days(f, ts_ts), 1) for f in ts_folds]} | "
        f"uneven_fold_spans_days={[round(_span_days(f, uneven_ts), 1) for f in uneven_folds]} | "
        f"cross_sectional_test_sample_counts={[len(f.test_indices) for f in xs_folds]} | "
        f"purge_count_with_48h_hold={len(purge_folds[0].purged_indices)}"
    )


if __name__ == "__main__":
    print(run_calendar_splitter_decision_gate())

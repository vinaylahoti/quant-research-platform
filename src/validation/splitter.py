"""Purged walk-forward splitter for WS3.

train_days / test_days / embargo_days are CALENDAR durations, not sample
counts.  This is the correct unit for cross-sectional datasets where the
number of samples per timestamp varies (e.g. 24-30 symbols active at each
4h bar).  Using sample counts on such a dataset silently consumes only the
first few calendar days of each year, regardless of how much data exists.

Fold progression (non-overlapping, sequential):
    fold 1: train [t0, t0+train_days)
            embargo gap: [t0+train_days, t0+train_days+embargo_days)
            test  [t0+train_days+embargo_days, t0+train_days+embargo_days+test_days)
    fold 2: starts immediately after fold 1 test window ends.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True)
class WalkForwardFold:
    """One rolling train/test window."""

    fold_number: int
    train_indices: tuple[int, ...]
    test_indices: tuple[int, ...]
    purged_indices: tuple[int, ...]
    embargo_days: int
    train_start: datetime
    train_end: datetime
    test_start: datetime
    test_end: datetime


class PurgedWalkForwardSplitter:
    """Yield rolling calendar-time folds with purging and embargo."""

    def __init__(
        self,
        *,
        train_days: int,
        test_days: int,
        n_folds: int,
        embargo_days: int = 1,
    ) -> None:
        if train_days <= 0:
            raise ValueError("train_days must be positive")
        if test_days <= 0:
            raise ValueError("test_days must be positive")
        if n_folds <= 0:
            raise ValueError("n_folds must be positive")
        if embargo_days < 0:
            raise ValueError("embargo_days cannot be negative")

        self.train_days = train_days
        self.test_days = test_days
        self.n_folds = n_folds
        self.embargo_days = embargo_days

    def split(
        self,
        *,
        timestamps: list[datetime],
        label_end_times: list[datetime],
    ) -> list[WalkForwardFold]:
        """Return deterministic calendar-time folds over the provided sequence.

        Each fold's train and test windows are defined by calendar duration.
        All samples whose timestamp falls in the window are included — this
        naturally handles varying symbol counts per timestamp without bias.
        """

        if len(timestamps) != len(label_end_times):
            raise ValueError("timestamps and label_end_times must have matching lengths")
        if not timestamps:
            raise ValueError("timestamps must not be empty")

        fold_origin = min(timestamps)
        folds: list[WalkForwardFold] = []

        for fold_number in range(1, self.n_folds + 1):
            train_start_dt = fold_origin
            train_end_dt = fold_origin + timedelta(days=self.train_days)
            embargo_end_dt = train_end_dt + timedelta(days=self.embargo_days)
            test_start_dt = embargo_end_dt
            test_end_dt = test_start_dt + timedelta(days=self.test_days)

            # All samples in the training calendar window (before purging)
            train_all = tuple(
                i for i, t in enumerate(timestamps)
                if train_start_dt <= t < train_end_dt
            )

            # Purge: training samples whose label extends into the test window
            purged_set = {
                i for i in train_all
                if label_end_times[i] > test_start_dt
            }
            purged = tuple(sorted(purged_set))
            clean_train = tuple(i for i in train_all if i not in purged_set)

            # All samples in the test calendar window
            test = tuple(
                i for i, t in enumerate(timestamps)
                if test_start_dt <= t < test_end_dt
            )

            if not test:
                raise ValueError(
                    f"Fold {fold_number}: no test samples in calendar window "
                    f"[{test_start_dt.date()}, {test_end_dt.date()}). "
                    f"Dataset only spans [{min(timestamps)}, {max(timestamps)}]. "
                    f"Reduce n_folds or widen the date range."
                )
            if not clean_train:
                raise ValueError(
                    f"Fold {fold_number}: no usable training samples in "
                    f"[{train_start_dt.date()}, {train_end_dt.date()}) "
                    f"(window empty or all samples purged)."
                )

            folds.append(
                WalkForwardFold(
                    fold_number=fold_number,
                    train_indices=clean_train,
                    test_indices=test,
                    purged_indices=purged,
                    embargo_days=self.embargo_days,
                    train_start=min(timestamps[i] for i in clean_train),
                    train_end=max(timestamps[i] for i in clean_train),
                    test_start=timestamps[test[0]],
                    test_end=timestamps[test[-1]],
                )
            )

            # Next fold starts immediately after this test window ends (calendar)
            fold_origin = test_end_dt

        return folds

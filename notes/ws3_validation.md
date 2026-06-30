# WS3 — Walk-forward validation (purged + embargoed)

> Standalone spec. Implement only what's in this file.
> Full context (if ever needed): `notes/ARCHITECTURE_REVIEW.md`.
> Depends on: WS1 (research log) — done. WS2 (feature layer) — done.

---

## Caveat — read before building

The point-in-time universe (WS4) is **not built yet**. Until it exists, treat
every result from this workstream as **plumbing validation only — not evidence.**
Walk-forward on today's survivorship-biased universe is still biased; this
workstream proves the *mechanism* works, not that any signal has real edge.

---

## Goal

Replace a single train/test split with rolling out-of-sample testing, so a
result is not "real" just because one split happened to look good.

## Build

- `src/validation/` — a splitter yielding rolling `(train, test)` windows moving
  forward in time, with:
  - **Purging:** drop training samples whose label/holding window overlaps the
    test window.
  - **Embargo:** a gap after the test window before the next train window starts,
    to kill serial-correlation leakage.
- Wrap the backtest runner so results are reported **per-fold and aggregated**
  (not just one blended number).
- Every validation run must write a row to the WS1 research log, including the
  current trial count from `how_many_trials()`.
- Report **deflated Sharpe ratio** using `n_trials` from the log (the Sharpe
  haircut for the number of configurations tried — see Bailey & López de Prado,
  "The Deflated Sharpe Ratio," if the agent needs the reference formula).

## What this defends against

The test set silently becoming an in-sample set the moment you iterate on it.
Over-optimistic single-split results that don't generalize.

## Decision gate

- Validation runs across N folds (use a small synthetic series for the proof,
  e.g. N=5 folds) and reports **per-fold dispersion**, not just an average.
- A result only counts as "real" if it survives out-of-sample folds **and**
  the deflation — not because one split looked good.
- Prove: running the same validation twice with the same code/data version
  produces identical per-fold results (determinism).

## Reminder for whoever builds this

WS1's `log_experiment()` is currently **manual** — there's a TODO note in
`src/research_log/store.py` saying it must become automatic once a real
backtest/validation runner exists. **This is that runner.** Wire the validation
harness so it calls `log_experiment()` automatically on every run — it should be
impossible to run a fold without it landing in the log.

## Stop condition

Build this only. Do not start WS4. Show the decision-gate output, then stop.

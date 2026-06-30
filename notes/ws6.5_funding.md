# WS6.5 — Wire real funding data into WS2 (close the gap before signal #2)

> Standalone spec. Implement only what's in this file.
> Depends on: WS1-WS6 done. This is a small, bounded fix — not a new signal,
> not a new architecture layer. Do this before designing signal #2.

---

## Why this comes before signal #2, not after

WS6's finalized momentum result (see `notes/ARCHITECTURE_REVIEW.md` "Current
status") is honest but **pre-funding-cost**. Every future signal test inherits
that same gap unless it's closed now. Funding is also directly relevant to
the natural next signal idea (a funding-extreme fade/follow signal needs this
data anyway), so fixing it now serves two purposes at once.

This is explicitly scoped as small. If it turns out to be bigger than expected,
stop and report that rather than quietly expanding scope.

---

## The actual gap (confirmed during WS6)

`FeatureEngine.attach_metrics_asof()` does not expose a `funding_rate` or
`last_funding_rate` column for any symbol — confirmed by testing all 30 WS4
universe symbols, all failed. This is despite the raw funding/OI metrics CSVs
existing on disk from Phase 1 (~1,368MB, daily granularity, downloaded
alongside klines back in the original data layer build).

The point-in-time as-of join *logic* already exists in WS2 (it's what makes
`attach_metrics_asof` point-in-time-correct for whatever it currently does
expose) — this is about wiring the *raw funding data* through that existing
mechanism, not building new join logic.

## Build

1. **Confirm the raw funding CSV schema first.** Look at the actual columns in
   the raw metrics files on disk (likely `data/raw/.../metrics/...`) — find the
   real column name for funding rate (it may not be called exactly
   `funding_rate` in the raw Binance files; check before assuming).
2. **Build the parquet feature-store path for metrics**, the same way klines
   already went from raw CSV to parquet in Phase 1. (Recall: at the time
   Phase 1 finished, only klines had been converted to parquet — metrics was
   still sitting as raw CSV only. Confirm whether that's still the case before
   building, since this may already be partially done.)
3. **Expose funding rate through `attach_metrics_asof`** (or a clearly-named
   sibling function) as a point-in-time-correct series — a bar at time T must
   only see funding data that was actually published at or before T. Funding
   publishes with a lag/schedule (roughly every 8 hours on Binance) — respect
   that, don't let a bar see a funding rate from later in its own funding period.
4. **Confirm `src/execution/model.py`'s existing funding-cost logic** (built in
   WS5, tested with synthetic funding data at the time) now receives real data
   correctly when called from a real signal pipeline.

## What this defends against

Every signal result being silently pre-funding-cost without that being
visible. Funding is directional in trends (you pay to sit on the crowded
side) — a signal that looks fine without funding cost may look meaningfully
worse once it's included, especially momentum-style signals.

## Decision gate

- For at least 3 real symbols, `attach_metrics_asof` (or its replacement)
  returns real, non-empty funding rates, with values that look like
  plausible real funding rates (typically small, e.g. -0.1% to +0.1% per
  8h period — sanity-check the magnitude, not just that something returns).
- Point-in-time correctness: confirm a bar at time T does not see a funding
  rate published after T (construct one explicit test case, the same pattern
  as WS2's original point-in-time assertion test).
- Re-run WS6's exact harness (one of the three years is enough — 2023 is a
  reasonable choice since it showed the clearest negative momentum result)
  WITH funding now included instead of explicitly excluded. Report whether
  momentum's already-negative result gets worse (expected, if it tends to
  sit on funded-against positions) or stays roughly the same.
- Update `FUNDING_SCOPE` in `src/signals/ws6_one_signal.py` to reflect that
  funding is now included, and update the Known Limitations section in
  `notes/ARCHITECTURE_REVIEW.md` to mark this gap as closed.

## Stop condition

Build this only. If the raw CSV schema or the metrics-to-parquet conversion
turns out to be a bigger job than expected, stop and report that rather than
expanding scope to fix it all in one pass. Show the decision-gate output,
then stop. Signal #2 design is the next conversation after this, not part of
this file's scope.

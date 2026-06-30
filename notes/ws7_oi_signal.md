# WS7 — Signal #2: open interest / positioning extremes

> Standalone spec. Implement only what's in this file.
> Depends on: WS1-WS6 done. Funding is permanently out of scope (see
> Known Limitation #2 in ARCHITECTURE_REVIEW.md) — do not revisit that here.

---

## Why this signal, why now

WS6 finalized a clean negative on time-series momentum (4h/20-bar) across
three full years — it never meaningfully beat a random control. That result
pointed at trying something **structurally different**, not another
momentum variant. Separately, WS6.5's investigation found that the raw
metrics data already on disk (`data/raw/metrics/`) is open interest and
long/short positioning ratios — real, available, completely unused data.
Crowd-positioning extremes (everyone crowded long, or everyone crowded short)
are a well-documented source of edge in perpetual futures markets, distinct
from price-momentum. This is a genuinely different signal family, not a
variation on WS6.

## Goal

Design one signal based on open interest and/or long-short ratio extremes,
run it through the same proven WS6 harness, across the same multi-year
windows, with the same rigor — and get an honest answer on whether it shows
edge that momentum didn't.

## Carry forward every lesson from WS6 — do not relearn these the hard way

- **Test the full available history, not a fixed 3-year window.** OI/positioning
  metrics start ~2021-12-01 for most symbols (the same cutoff found during the
  funding investigation), so for this OI signal the usable span is roughly
  **late-2021 through 2025 (~4 years)**, per symbol, with later listings
  (TON 2024, SUI/ARB 2023) naturally contributing less — that's the
  point-in-time universe working correctly, not a gap. Use all of it; more
  independent market regimes makes the screen harder to fool. (Do NOT claim a
  clean "5-year OI backtest" — OI data doesn't go back to early 2021.)
- **Use a `test_size` large enough that each fold gets tens of trades, not
  single digits.** A small fold produced a PF-of-infinity artifact from pure
  chance during WS6's 2024 run. Sanity-check trade-per-fold counts before
  trusting any PF/Sharpe number.
- **The random control must use a real seeded RNG**, not a repeating pattern.
  This was already fixed once in `src/signals/ws6_one_signal.py` — reuse that
  fixed version, don't reintroduce the bug in a new file.
- **Deflated PF, not deflated Sharpe, is the trustworthy metric** when the
  candidate batch is small (e.g. 2 candidates). Deflated Sharpe saturated to
  exactly `1.0` in WS6's 2024 run for this reason — watch for the same
  pattern here and don't read it literally if it recurs.
- **If a result looks suspiciously clean (PF infinity, PF exactly 0, a
  "random" control that doesn't look random), stop and ask what it's
  actually built on before reporting it as a finding.** This happened
  multiple times in WS6 and every single time the suspicious number turned
  out to be a real, fixable bug, not a real result.
- **No funding cost.** This is now permanent project scope, not a gap to
  re-investigate. Use `FUNDING_SCOPE`-style explicit documentation in the
  report output, same pattern as WS6, so it's never silently forgotten.

## CRITICAL — the mistake that broke the first WS7 attempt (do not repeat)

The first attempt at this signal walked the extreme threshold from z>=2.0 down
to z>=1.0, then z>=0.75, then **z>=0.0** — each step to manufacture more trades
because folds had too few. At z>=0.0 the signal no longer tested "positioning
extreme" at all; it tested "OI is above its own mean ~50% of the time," a
completely different and much weaker hypothesis. **The result of that run was
thrown out because it no longer tested the actual idea.**

**Hard rule for this rebuild:** the threshold that *defines an extreme* does
NOT get lowered to make trades appear. If an honest threshold produces too few
trades per fold:
- Shorten `OI_LOOKBACK_BARS` (what "extreme" is measured *against*) — e.g. 30-40
  bars instead of 120 — so genuine extremes relative to *recent* history fire
  more often. This is a legitimate scale choice; it does NOT redefine "extreme."
- And/or use a longer total test window so more real extreme events accumulate.
- If real extremes are STILL too rare after that, **that is the finding** —
  report "OI extremes don't fire often enough on this universe to validate,"
  do not lower the z-threshold to force it.

## Pick the actual signal (do this first, in writing)

Decide and document the exact rule before building. Commit to it; it does not
change after seeing the result.

**OI-extreme mean-reversion:** when open interest reaches a statistical extreme
relative to its own recent history — **use an honest extreme threshold, z>=1.5
or higher, NOT lower** — treat it as overcrowded positioning and signal
**against** the prevailing price direction (a bet that extreme positioning
unwinds). Different hypothesis from momentum: "the crowd is over-positioned and
a reversal is more likely," not "the trend continues."

Starting parameters to commit (adjust the LOOKBACK if needed for trade count,
NEVER the threshold): z-threshold >= 1.5, OI lookback 30-40 bars on 4h.

If the long-short ratio columns turn out to give a cleaner rule once you look at
real values, that's allowed — but write the exact rule down first, same
discipline.

## Build

### Layer 1 — The signal (`src/signals/`)

- One new file, e.g. `src/signals/oi_extreme.py`. Pure function:
  `f(features_df) -> signal_series`, same `[-1, 0, 1]` output convention as
  momentum for direct comparability.
- Input must come from real OI/positioning data loaded through WS2's feature
  layer — confirm during WS6.5's investigation that this data is accessible
  via `FeatureEngine`/`attach_metrics_asof`, or extend that loader if needed
  (this should be more straightforward than the funding case, since the data
  genuinely exists on disk in the metrics CSVs already).
- No I/O, no sizing in this file. Determinism test required, same as momentum.

### Layer 2 — Sizing (reuse WS5, do not reimplement)

Same as WS6 — the signal feeds WS5's volatility-targeted sizing. No new
execution logic.

## Wiring through the full harness

Run through WS3 (purged/embargoed folds, corrected `test_size`), WS4
(point-in-time universe), WS5 (shared execution, no funding), WS1 (auto-log).
Run the seeded random control alongside it, identical pipeline.

## Decision gate

- The signal passes its determinism test.
- Multi-year run across the **full available OI history (~late-2021 through
  2025)** completes with properly-sized folds (tens of trades per fold, not
  single digits).
- Report deflated PF and total return for both the OI-extreme signal and its
  random control, **per year across the full available history**, same table
  format as WS6's final result.
- Report honestly: does this signal beat its control by a meaningful margin
  (not just "both positive," the way 2024's momentum result was misleadingly
  close to its control) in any or all years? If not, that's an acceptable,
  complete WS7 outcome — record it and move to designing signal #3, the same
  way WS6's negative result led here.

## Stop condition

Build this only. Show: the exact signal rule chosen, the determinism result,
the multi-year table, and the honest comparison against the random control.
Do not start a combination layer or move toward paper trading — that decision
gets made deliberately after reviewing this result, same as after WS6.

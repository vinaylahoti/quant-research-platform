# WS8b — Does hold-time explain three consecutive zeros? (test before building signal #5)

> Standalone spec. Implement only what's in this file.
> Depends on: WS1-WS8 done, DPF per-trade fix in place, splitter calendar
> fix verified. This is a DIAGNOSTIC, not a new signal — it reuses an
> existing signal at a different hold time to answer a structural question
> before spending a build cycle on signal #5.

---

## The question this answers

WS6 (time-series momentum), WS7 (OI-extreme), and WS8 (cross-sectional
momentum) — three structurally different hypotheses — all showed essentially
zero signal-vs-control gap, at the same configuration: ~1h hold, 5x max
leverage, ~19,000 trades/year. Before building signal #5, it's worth knowing
whether that's because **these specific ideas have no edge**, or because
**1h is too short a horizon for any of these effects to express themselves**
(momentum and mean-reversion are typically studied over hours-to-days in the
literature, not one hour) — in which case every signal tested so far may have
been tested at the wrong timescale, and signal #5 would just produce a fourth
identical zero regardless of merit.

This is cheap to check: re-run an EXISTING signal with one parameter changed
(hold time), not a new build.

## What to change, what NOT to change

- **Pick ONE already-built signal to re-run** — cross-sectional momentum
  (WS8) is the recommended choice since it's the most recently verified and
  has confirmed-correct calendar splitting and DPF already in place.
- **Change ONLY the hold time** — from ~1h to 24h (one full day). Keep the
  ranking lookback, the long/short cutoff, the universe, the leverage config,
  and everything else exactly as WS8 had it. This isolates hold-time as the
  single variable being tested.
- **Do NOT also change the signal's logic, lookback, or threshold** in the
  same run — if multiple things change at once and the result differs, you
  won't know which change caused it.
- Reuse the existing seeded random control unchanged in structure, just run
  at the same new 24h hold so it's still a fair comparison.

## Build

- This should be a small modification to the existing WS8 harness
  (`src/signals/cross_sectional_momentum.py` / its runner), not a new file
  from scratch — change the planned-exit/hold-duration parameter, re-run.
- Confirm WS5's execution model and sizing still behave sensibly at a 24h
  hold (e.g. funding would matter more at this horizon in a real system, but
  funding remains out of scope per project decision — just confirm nothing
  else breaks at the longer hold).
- Auto-log to WS1 as usual.

## Decision gate

- Calendar span check repeated (same discipline as WS8 — don't assume
  correctness just because WS8 verified it; confirm the new run's folds also
  span real calendar time correctly).
- Report the signal-vs-control gap at 24h hold, per year (2022-2025), using
  the now-fixed graded DPF and `mean_return_per_active_trade`.
- **Compare directly against WS8's 1h-hold gap, side by side.**

## How to read the result (both outcomes are useful, decide what's next based on which)

- **If the gap at 24h is meaningfully different from ~zero** (even if still
  not clearing a full screening bar) — that's a real, important finding:
  hold-time was masking real signal content. The right next move is NOT
  signal #5 — it's re-testing WS6 and WS7 at longer holds too, since all
  three may have been tested at the wrong horizon.
- **If the gap at 24h is still ~zero, same as 1h** — that's good evidence the
  issue is the signal ideas themselves, not the test horizon. Proceed to
  signal #5 from the queue with more confidence that the harness's verdicts
  are measuring real absence of edge, not an artifact of hold-time.
- **Either result is a complete, useful answer.** Do not iterate on hold-time
  values hunting for one that shows a gap (e.g. trying 6h, then 12h, then 18h
  until something looks positive) — that's the same "loosen until it passes"
  trap from WS7's threshold mistake, just applied to hold-time instead of a
  signal threshold. One clean comparison (1h vs 24h), report what it shows.

## Stop condition

Build and run this only. Show: the 24h-hold per-year table, the direct
comparison against WS8's 1h-hold numbers, and a clear recommendation —
signal #5 next, or re-test WS6/WS7 at longer holds first — based on what the
comparison actually shows. Do not build signal #5 or re-test WS6/WS7 in the
same pass; report the finding and let that decision get made deliberately.

# Orientation — read this alongside notes/RESEARCH_DIRECTION_V2.md

> This file is pointers only — where things are, what's safe to reuse, what to
> avoid touching. The REASONING for every decision lives in
> RESEARCH_DIRECTION_V2.md — read that first for "why," this file second for
> "where."

---

## Current task

Building **Track A**: a vol-targeted portfolio paper-trading system. No
directional signal — sizing only, across the real point-in-time universe.
See RESEARCH_DIRECTION_V2.md Section 9 ("Track A") and 9b (must/must-not, what
must be in place before switching it on) for the full spec of what this is.

If no WS9 spec file exists yet in `notes/`, one needs to be written first,
following the same pattern as `notes/ws6_one_signal.md` / `ws7_oi_signal.md` /
`ws8_cross_sectional_momentum.md` (goal, build, decision gate, stop condition)
— but for a paper-trading deployment rather than a backtest screen.

## Repo structure — what's safe to build on vs. what to leave alone

**Reusable infrastructure (WS1-WS5, built and verified, build on top of these,
do not rebuild):**
- `src/research_log/` — WS1, auto-logging, append-only SQLite.
- `src/features/` — WS2, versioned feature layer.
- `src/validation/` — WS3, walk-forward splitter (recently fixed — see below)
  and deflated-PF/Sharpe metrics (recently fixed — see below).
- `src/universe/` — WS4/4.5, point-in-time universe with bounded LUNA/FTT
  survivorship fix.
- `src/execution/` — WS5, shared fill/sizing model. **Real exchange fees were
  added recently** — verify this is actually wired into whatever path Track A
  uses, don't assume.
- `src/data/` — Phase 1 data layer, klines + OI/positioning metrics feature
  store, plus `query.py` (DuckDB cross-symbol query layer).

**Signal files — tested, confirmed null, do NOT build on these or treat their
results as anything but closed:**
- `src/signals/ws6_one_signal.py` (momentum, short + long horizon, both null)
- `src/signals/oi_extreme.py` / `ws7_oi_signal.py` (OI mean-reversion, null)
- `src/signals/cross_sectional_momentum.py` / `ws8_*` (null)
- Any carry-simulation script from the funding investigation (unhedged carry,
  confirmed to lose 2/4 years — disproven, not a paper-trading candidate)

**Two recent infrastructure bugs, both fixed — if anything looks suspiciously
clean, check these are still actually fixed in whatever path is being used:**
1. `PurgedWalkForwardSplitter` used to define folds by sample count instead of
   calendar time, silently shrinking "a year" of test data down to ~7 days on
   this cross-sectional dataset. Fixed to use real calendar-day boundaries.
   Decision-gate test: `test_ws3_calendar_splitter.py`.
2. Deflated PF used to reconstruct fake-identical per-trade returns from fold
   totals, making it a binary 0/infinity switch that couldn't detect partial
   edge. Fixed to use real per-trade returns (`ValidationRunResult.all_returns`).

## Key documents, in reading order

1. `notes/RESEARCH_DIRECTION_V2.md` — the why and what's next. Start here.
2. `notes/ARCHITECTURE_REVIEW.md` — the infrastructure status (WS1-WS5) and
   the full history of bugs found/fixed. Reference if you need to understand
   *how* something was built, not just that it exists.
3. This file — where things are.

## House rules (apply regardless of what's being built)

- Verify what a number is actually built on before trusting it — every real
  bug in this project was caught this way, including bugs in infrastructure
  that had already passed its own decision gate.
- Lock parameters/definitions before running; only ever tighten, never loosen
  to manufacture a result.
- One workstream/task at a time with an explicit stop condition — don't let
  scope silently expand mid-build.
- Skip step-by-step narration in responses; report files changed, judgment
  calls made, and real output/numbers only.

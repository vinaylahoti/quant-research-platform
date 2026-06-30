# WS8 — Signal #4: cross-sectional momentum

> Standalone spec. Implement only what's in this file.
> Depends on: WS1-WS7 done, splitter calendar-bug fixed and verified.
> Read `notes/signal_screening_plan.md` first — this is a screening run
> within that plan, not a standalone bet.

---

## Why this signal, why now

Two signals tested so far — time-series momentum (WS6) and OI-extreme
mean-reversion (WS7) — were both **absolute, directional** bets ("will price
go up," "is OI abnormally high") and both failed identically: no edge, no
regime awareness, indistinguishable from random once properly measured on
full calendar-year data. Cross-sectional momentum is a structurally different
hypothesis: **not absolute direction, but relative strength** — rank symbols
against each other each period, long the strongest, short the weakest. This
can work in flat or choppy markets where absolute-direction signals fail,
because it never needs the whole market to move — only for some symbols to
move more than others.

## Carry forward every lesson — these are not optional, re-verify each one

- **THE BIG ONE: confirm the splitter fix is actually in effect before
  trusting anything.** `PurgedWalkForwardSplitter` previously defined folds by
  sample count, which on this cross-sectional dataset meant a "year" was
  really ~7 calendar days — this silently invalidated the first WS6 and WS7
  results. It was fixed to use real calendar-day fold boundaries. **Before
  running WS8, verify one fold's `test_start`/`test_end` actually spans real
  calendar days matching what was requested** — do not assume the fix carried
  over correctly into a new harness file just because it exists elsewhere.
- **Never loosen a signal's definition to manufacture trades.** If genuine
  cross-sectional spread (top performers meaningfully ahead of bottom
  performers) is rare on some dates, that's information — extend the test
  window or adjust the ranking lookback, never lower what counts as a real
  rank-spread to force trades.
- **Real seeded RNG for the random control**, not a repeating pattern. Reuse
  the already-fixed control logic from `src/signals/ws6_one_signal.py` /
  `ws7_oi_signal.py` rather than rewriting it.
- **Deflated PF is the trustworthy metric**, not deflated Sharpe, when the
  candidate batch is small (e.g. 2 candidates). Deflated Sharpe saturated to
  exactly `1.0` in earlier runs for this reason.
- **Fix `total_return` before trusting it** — it was a raw sum across all
  trades, producing nonsensical numbers (-2389%) at high trade counts. Confirm
  this display bug was actually fixed in shared code before reading this field;
  if not fixed yet, ignore `total_return` and rely on deflated PF only.
- **No funding cost.** Permanent project scope decision — do not revisit.
- **Use full available history** (2022-2025 for price-only signals — this is
  price-only, so the full span applies, not the shorter OI-data window).
- **If a result looks suspiciously clean, stop and check what it's built on**
  before reporting it. Every suspicious-looking number tonight turned out to
  be a real bug, including ones buried in infrastructure everyone thought was
  already proven.

## Pick the actual signal (commit in writing before building)

**Cross-sectional momentum, rank-based:** at each rebalance point (e.g. every
4h bar), rank all symbols currently in WS4's point-in-time universe by their
trailing N-bar return (start with N=20, same lookback as WS6's momentum for
rough comparability). Go long the top quintile (or top-K, pick one and commit
to it — e.g. top 6 of ~30), short the bottom quintile/top-K, flat on the
middle. Output convention: signal per symbol per timestamp, same `[-1,0,1]`
shape as prior signals (or a rank/weight if that's more natural — document
whichever is chosen).

Commit the exact lookback, the exact long/short cutoff (quintile vs fixed-K),
and the rebalance frequency in writing before running. These do not change
after seeing results.

## Build

### Layer 1 — The signal (`src/signals/cross_sectional_momentum.py`)

- Pure function operating across the **full universe at a timestamp**, not
  one symbol at a time — this is the structural difference from WS6/WS7, which
  were single-symbol functions. Input: a dict or DataFrame of all universe
  symbols' features at time T (from WS2, via WS4's point-in-time membership).
  Output: per-symbol signal at that T.
- No I/O, no sizing. Determinism test required: same input ranks always
  produce the same long/short split.

### Layer 2 — Sizing (reuse WS5, do not reimplement)

Same as before — WS5's volatility-targeted sizing, no new execution logic.
Note: this signal naturally produces multiple simultaneous positions (longs
and shorts across several symbols at once) rather than one symbol at a time —
confirm WS5's execution model handles concurrent multi-symbol positions
correctly, since WS6/WS7 likely only exercised one position at a time.

## Wiring through the full harness

WS3 (corrected calendar splitter — verify this explicitly per the lesson
above), WS4 (point-in-time universe, the natural fit for this signal since it
needs the real symbol set at each timestamp anyway), WS5 (shared execution,
no funding), WS1 (auto-log). Seeded random control alongside it — for this
signal, the control should also be a *cross-sectional* random rank shuffle
(randomly assign long/short/flat across the universe each period), not a
single-symbol random signal, so it's a fair comparison.

## Decision gate

- Determinism test passes.
- One fold's calendar span is explicitly verified (e.g. print `test_start`,
  `test_end`, confirm the gap matches the requested duration) before trusting
  any other output from this run.
- Full 2022-2025 run completes with properly-sized folds.
- Report deflated PF and (if fixed) total return for both the cross-sectional
  signal and its cross-sectional random control, per year.
- Honest read: does this signal beat its control consistently, given it's a
  structurally different (relative, not absolute) bet than the first two?
  Either outcome is an acceptable, complete result — record it.

## Stop condition

Build this only. Show: the committed signal rule, the determinism result, the
explicit fold-calendar-span verification, the full per-year table, and the
honest comparison. Do not start correlation analysis or combination — that's
the next move only once 2+ signals show real survival, per the screening plan.

# Architecture Review — Master Spec (v3)

> This file is the single source of truth for project state and ordering.
> v2 of this file (and the original README) referred to "porting 9 existing
> signal engines." **That was never true** — there was no pre-existing
> 9-engine system. That phrase originated from an unverified past-conversation
> summary and got repeated as fact across sessions until it was caught and
> corrected during WS6. This version replaces it permanently.

> **Token-budget note:** this is the reference copy. When driving a coding
> agent through a specific workstream, point it at that workstream's standalone
> file instead of this whole document:
> - WS3 → `notes/ws3_validation.md`
> - WS4 → `notes/ws4_universe.md`
> - WS4.5 → `notes/ws4.5_survivorship_bound.md`
> - WS5 → `notes/ws5_execution.md`
> - WS6 → `notes/ws6_one_signal.md`
> - WS7 (next signal) → `notes/ws7_oi_signal.md`
>
> (WS1 and WS2 are built — no spec file needed for them anymore.)
> Open this file for cross-workstream reasoning, current project status, or
> deciding what to build next.

> **GOVERNING STRATEGY from WS6 onward:** `notes/signal_screening_plan.md`.
> The foundation (WS1-WS5) is built. Everything after is signal research, and
> the screening plan defines HOW: screen many honestly-defined signals for
> "real-but-weak" (not winners), measure correlation between survivors, combine
> the uncorrelated ones, and judge the combination deflated for the whole
> search. Individual signal specs (WS6, WS7, ...) are runs *within* that plan,
> not standalone bets. Read the screening plan before designing or judging any
> signal.

---

## Current status (as of end of WS6)

**Done and verified:** WS1 (research log), WS2 (versioned feature layer), WS3
(purged + embargoed walk-forward validation, real deflated Sharpe formula),
WS4 (point-in-time universe ranking), WS4.5 (bounded survivorship fix — LUNA +
FTT added so the universe shows real historical turnover, explicitly documented
as partial, not exhaustive), WS5 (one shared execution model — fills, intrabar
SL-first resolution, funding-as-cost wiring, volatility-targeted sizing).

**WS6 — first real signal result, finalized (corrected twice):** A single
4h/20-bar time-series momentum signal was tested through several stages before
reaching a trustworthy result. An early 3-month-only test, then an early
3-year test, both turned out to rest on too-small per-fold sample sizes. After
fixing `test_size`, a **second, more serious bug was found**: the validation
splitter (`PurgedWalkForwardSplitter`) defined fold size by raw sample count,
but the dataset is cross-sectional (~30 symbols per timestamp) — so
"test_size=90 samples" was consuming only ~12 hours of calendar time per fold.
Every "full year" result actually covered **the first ~7 calendar days of
January of that year, repeated 3-4 times** — not the year at all. This
affected WS6 and WS7 identically, since both used the same splitter.

**The splitter was fixed to use calendar-time fold boundaries** (real days,
not sample counts) and both signals were re-run on genuinely full-year data.
The corrected, final result (deflated PF, full calendar years, ~12,000-16,000
trades per fold):

| Year | Momentum deflated PF | Control deflated PF |
|---|---|---|
| 2022 | 0.000 | 0.000 |
| 2023 | 0.000 | 0.000 |
| 2024 | 0.000 | 0.000 |
| 2025 | 0.000 | 0.000 |

**Honest verdict: this signal has no demonstrated edge, now confirmed on real
annual data.** The earlier "2024 momentum vs control, close but momentum
ahead" framing from the pre-splitter-fix run is retracted — that result was
built on roughly two calendar days of January 2024, not the year. The
underlying directional conclusion (no edge) happened to survive the fix, but
the *evidence quality* was completely different before and after — this is
the first version of this result that should be trusted.

**Known issue, still pending:** the `total_return` field is a raw arithmetic
sum across all trades rather than a compounded or averaged return — at
~40,000+ trades/year this produces nonsensical-looking numbers (e.g. -2389%)
even on a correctly-functioning run. Deflated PF is unaffected and remains the
trustworthy metric. Fix the display before relying on `total_return` again.

**WS7 — second signal, finalized:** An OI-extreme mean-reversion signal
(z-score >= 1.5 on 35-bar trailing open interest, signal against recent price
direction) was tested through the same splitter-bug discovery as WS6. An
early run showed an apparent win in 2024 (deflated PF 7.42) — this was
**entirely a 7-calendar-day sampling artifact**, gone completely once the
splitter was fixed to use real calendar-time folds. The corrected, full-year
result: deflated PF 0.000 in every year (2022-2025), for both the signal and
its random control. The signal does select genuine statistical extremes (it
fires on ~23% of bars, consistent with a real z>=1.5 filter) — but per-trade
loss rate is nearly identical to the random control (-0.062% vs -0.065%),
meaning the extremes it selects don't predict reversal any better than chance.
**Honest verdict: no demonstrated edge.**

**Major infrastructure bug found and fixed during WS6/WS7 re-verification:**
`PurgedWalkForwardSplitter` defined `train_size`/`test_size` as raw sample
counts. On this project's cross-sectional dataset (~30 symbols active per
timestamp), that meant a "year" of test samples was consumed in ~7 calendar
days, with the remaining ~358 days of real downloaded data sitting unused
and untested. This silently affected every multi-year result produced before
the fix — it was caught by checking actual fold date ranges against research
log entries, the same "verify what a number is actually built on" habit that
caught every other issue tonight, just pointed at the harness itself instead
of at a signal's output. The splitter now uses real calendar-day fold
boundaries; a new decision-gate test (`test_ws3_calendar_splitter.py`) proves
fold spans match requested calendar duration, not sample count.

**Known limitation, newly found:** the WS1 research log (`research_log.db`)
is not safe for concurrent writers. Running two signal harnesses in parallel
caused each to misread the other's trial count mid-run. Run one signal
harness at a time until this is fixed.

---

## Known limitations (live list — update as new ones are found)

1. **Splitter calendar-vs-sample-count bug — FIXED, but caused real damage
   before discovery (see WS6/WS7 status above).** `PurgedWalkForwardSplitter`
   used to define folds by raw sample count, which silently meant "a year" was
   really ~7 calendar days on this project's cross-sectional dataset. Fixed to
   use real calendar-day fold boundaries, with a decision-gate test
   (`test_ws3_calendar_splitter.py`) proving fold spans match requested
   duration. **Any result produced before this fix (including originally
   "finalized" WS6/WS7 tables) should be treated as invalid until re-run** —
   this has been done for WS6 and WS7; any other pre-fix result should be
   assumed untrustworthy.

2. **Research log concurrency (WS1):** not safe for concurrent writers.
   Running two signal harnesses in parallel against the same `research_log.db`
   caused each to misread the other's trial count mid-run. Run one signal
   harness at a time until this is fixed.

3. **Universe completeness (from WS4.5):** the point-in-time universe includes
   the current top-30 actively-traded symbols plus a small fixed set of
   historically significant delisted symbols (LUNA, FTT). It is NOT a complete
   historical reconstruction of every symbol that ever traded. Backtest results
   should be read with this in mind — survivorship bias is reduced, not
   eliminated.

4. **Funding cost is OUT OF SCOPE for this project, permanently (corrected during
   WS6.5).** Earlier versions of this file said funding/OI data was "already
   pulled" in Phase 1 and just needed wiring into WS2 — **that was wrong.**
   When WS6.5 actually inspected the raw metrics files, the real columns are
   `sum_open_interest`, `sum_open_interest_value`,
   `count_toptrader_long_short_ratio`, `sum_toptrader_long_short_ratio`,
   `count_long_short_ratio`, `sum_taker_long_short_vol_ratio` — open interest
   and long/short positioning data, **not funding rates**. Actual Binance
   funding-rate history was never downloaded, in any phase, ever. The
   "funding" name had been repeated as background fact since Phase 1 without
   anyone opening the file to check, the same way "9 signal engines" was
   repeated without ever being verified — both corrected the same way, by
   actually looking.

   **Decision: do not pursue funding-rate data.** It would require a new,
   separate download from a different Binance endpoint, for a cost component
   on signals that have not yet shown enough promise to justify it. All
   signal results in this project are and will remain pre-funding-cost. This
   is a permanent, accepted limitation, not a pending task.

   **What IS real and available, unused so far:** open interest and
   long/short positioning ratios, sitting in `data/raw/metrics/` right now.
   This is a more promising lead for signal design than funding ever was —
   crowd positioning extremes are a well-documented source of edge in perps.
   See WS7 (signal #2) for the proposal to use this directly.

5. **Fold sample size matters — learned the hard way during WS6's re-test.**
   With too small a `test_size`, a handful of trades per fold can produce
   PF-of-infinity or inflated Sharpe purely from small-sample luck (e.g. 8
   random trades all landing as winners by chance). Any future signal test
   should sanity-check that trade counts per fold are large enough (tens, not
   single digits) before trusting PF/Sharpe from that run. (Superseded in
   practice by the calendar-splitter fix above, which also fixes this.)

6. **WS5 models slippage only — exchange fees are not included (optimistic by ~3.5x
   vs. real Binance taker costs).** `execute_trade` charges 2 bps per side in
   slippage at the position's realized leverage (~1.6-1.9x average in practice),
   producing ~0.067% drag per active trade. Real Binance taker fees of 0.05% per
   side add ~0.167% more at that leverage, bringing true per-trade cost to ~0.235%
   — 3.5x the modeled amount. **This does NOT affect signal screening rankings:**
   both the signal and its cross-sectional random control face identical execution
   costs, so the signal-minus-control gap measures pure directional edge with costs
   cancelling. It DOES matter when asking "does this combination survive live?" —
   the cost floor is 3.5x higher than the model shows, and at ~19,000 active
   trades/year the aggregate fee drag is substantial. Fix before treating any result
   as paper-trading-ready.

---

## The metric kill-list (apply everywhere, still in force)

- **KILL** `P(session hits +10%)` — artifact of arbitrary windowing, not a property of the strategy.
- **KILL** win-rate in isolation — you can win 90% of trades and lose money.
- **KEEP / ADD** deflated Sharpe (haircut for number of trials, now actually
  implemented and validated in `src/validation/metrics.py`), net-of-cost
  (turnover-adjusted) return, max drawdown **and** time-to-recovery, per-trade
  expectancy **with confidence intervals**, performance **conditional on regime**,
  and **capacity** (capital before impact eats the edge).

---

## WS1 — Research log ✅ DONE

Append-only SQLite log. Every backtest auto-writes a row: UTC timestamp, git
commit, data snapshot hash, params, metrics, running trial count. Auto-logging
is wired through WS3's validator and exercised for real in WS6 — trial counts
reported in WS6's output are genuine, not placeholders.

## WS2 — Feature layer ✅ DONE

Versioned, pure feature functions over immutable raw data, computed on demand
with a disposable cache. Point-in-time correctness (as-of joins, right-edge bar
closing) verified. **Gap:** funding metrics are not yet exposed through this
layer — see Known limitation #2.

## WS3 — Walk-forward validation ✅ DONE (corrected after a real bug found in production use)

Purged + embargoed rolling splitter. Deflated Sharpe ratio is implemented per
Bailey & López de Prado's actual formula (not an approximation) — this required
a real fix mid-build when the first version saturated near 1.0 due to using raw
return-series length instead of cross-candidate dispersion as the standard-error
input. Verified against both curated and genuinely empirical noise-candidate
batches; correctly punishes lucky random results as the candidate batch grows.
"Candidate batch" for deflation = explicit configurations supplied per
experiment, not the entire historical log and not an undefined "strategy family."

**Found later, during WS6/WS7 use, not during WS3's own original build:** the
splitter defined fold size by sample count, which on this project's
cross-sectional dataset silently meant "a year" was really ~7 calendar days.
This passed WS3's own original decision gate (which used a smaller synthetic
dataset where the bug didn't manifest) and was only caught when real signal
results looked suspiciously clean and someone checked actual fold dates against
calendar time. Fixed to use real calendar-day fold boundaries; a new test
(`test_ws3_calendar_splitter.py`) proves fold spans match requested calendar
duration directly, not just sample count — closing the gap that let the
original bug pass its own gate undetected.

## WS4 / WS4.5 — Point-in-time universe ✅ DONE (bounded)

Daily top-N membership table built purely from historical volume in the real
feature store — verified NOT anchored to `config/settings.py`'s symbol list.
The 2022-06-01 spot-check confirmed real, named turnover (LUNA, FTT dropping
out; APT/ARB/INJ/SUI/TON appearing later) once the bounded survivorship fix
(WS4.5) was added. See Known limitation #1 for what this does and doesn't cover.

## WS5 — Execution realism ✅ DONE

One shared `execute_trade` function used identically by research and paper
adapters — proven by byte-identical returns from both call paths in the
decision gate. Intrabar SL-first resolution confirmed on an ambiguous-bar test
case. Volatility-targeted sizing confirmed: leverage swings ~5x between calm
and volatile synthetic regimes while realized risk stays constant. Funding-cost
application exists in the model itself; the gap is upstream, in WS2 not
exposing real funding data yet (Known limitation #2).

## WS6 — One signal, end to end ✅ DONE (finalized, corrected twice: no edge found)

See "Current status" above for the full, calendar-corrected result. The
harness is proven on real, genuinely full-year data. The first tested signal
(momentum, 4h/20-bar) showed no demonstrated edge across 2022-2025. Multiple
real bugs were caught and fixed before this result could be trusted: a silent
symbol-truncation bug, a fake/invented deflation formula, a non-random
"random" control, a too-small fold size producing small-sample artifacts, a
duplicate full-year data load, and — found last, found biggest — the
calendar/sample-count splitter bug that meant every prior "full year" was
really ~7 calendar days. Each one was found by asking "what is this number
actually built on" rather than accepting a clean-looking pass — including,
in the end, pointing that question at the harness's own infrastructure, not
just at a signal's output. Keep doing that for every future signal test.

## WS7 — Signal #2 (OI-extreme) ✅ DONE (finalized: no edge found)

See "Current status" above for the full, calendar-corrected result. An early
run showed an apparent 2024 win that was entirely a 7-calendar-day sampling
artifact, gone once the splitter was fixed. Real verdict: no demonstrated
edge in any year 2022-2025; the signal selects genuine statistical OI
extremes but they don't predict reversal any better than chance.

---

## What's next (governed by `notes/signal_screening_plan.md`)

Everything from here is open-ended signal research, run as a **screening
campaign**, not a hunt for one magic signal. The full strategy — including the
core idea, the hard rules, the signal queue, and the four-move process — lives
in `notes/signal_screening_plan.md`. Summary of the standing approach:

- **Screen many signals for "real-but-weak," not winners.** Most single signals
  score near-zero after costs (WS6 momentum did) — that's normal. Edge comes
  from combining several weak-but-real, *uncorrelated* signals, not from one
  strong one.
- **Never loosen a signal's definition to manufacture trades** (this broke the
  first WS7 attempt — threshold walked to z>=0.0). Fix trade-count problems via
  lookback/test-window, never by redefining the signal.
- **Use real calendar-time fold boundaries, not sample counts** (the bug that
  invalidated the first WS6/WS7 results — see Known Limitation #1). Any new
  signal harness must use the corrected `PurgedWalkForwardSplitter`.
- **Use full available history per signal** (~5yr price signals, ~4yr OI).
- **Judge the eventual combination deflated for the whole search** — every
  signal and every combination scheme tried counts toward the multiple-testing
  penalty.

**Immediate next action: signal #4, cross-sectional momentum, not signal #3.**
Two signals tested so far (momentum, OI-extreme) were both *directional,
regime-sensitive* bets — absolute price direction and absolute OI level —
and both failed in the same family of way (no edge, no regime awareness).
Cross-sectional momentum (rank symbols against *each other*, long the
strongest, short the weakest) is a structurally different bet — relative, not
absolute — and a better use of the next test slot than another directional
variant (OI-change). Reorder the queue: try signal #4 next, return to #3
(OI-change) afterward if useful.

**Then, per the plan:** continue the signal queue (OI-change, cross-sectional
momentum, volatility expansion, volume imbalance) → measure correlation between
survivors → combine uncorrelated ones → judge deflated → only then consider the
freqtrade dry-run paper engine → only after 100+ real paper trades, revisit
real-money as a conversation (with capital you can afford to lose entirely).

Funding, basis, and liquidation signals are shelved — they need data not on
disk. See Known Limitation #2 and the screening plan's "shelved" list.

---

## What a pro platform skips (still true, still in force)

No streaming infra, no giant persisted feature store, no live-deploy
scaffolding yet. Knowing what to skip is part of the craft.

---

## How to drive this with a coding agent (unchanged discipline)

One workstream — or one well-scoped extension of a workstream — at a time.
Point the agent at the specific standalone file, not this whole document.
When it reports back, verify the substance, not just the pass/fail line:
ask what real data it ran on, ask to see suspicious numbers recomputed by
hand, ask whether a "random" control is actually random, ask whether a
metric formula is real math or a placeholder. Every real bug caught so far in
this project was found by asking one of those questions after a "done" report,
not by trusting the first summary.

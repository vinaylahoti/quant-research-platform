# Architecture Review — Corrected Build Spec (v2)

> This file supersedes the **Phase 2+ ordering** in `README.md`.
> Phase 1 (data layer) stays as-is and is done.
> Everything below comes **before** any signal/indicator work.

---

## Verdict (read first)

The infrastructure is fine — over-scoped in places, not under-scoped.
The gap between this project and institutional-grade research is **not tooling.
It is methodology: defenses against fooling ourselves.** That is where months
get wasted, so that is what these five workstreams install.

**Hard rule:** no signal optimization, no new indicators, until WS1–WS5 exist.
Sharpening a knife you cannot yet measure is wasted motion.

### Two sequencing caveats (do not ignore)

1. **Universe before belief.** The point-in-time universe (WS4) must be in place
   before you *trust* any walk-forward result (WS3). Walk-forward on a
   survivorship-biased universe is still biased. Until WS4 lands, treat every
   backtest as **plumbing validation only — not evidence.**
2. **Version definitions, don't persist a golden store (yet).** At 1–2 GB, solo,
   a heavyweight persisted feature store is premature and dangerous: a lookahead
   bug baked into the store propagates silently into every experiment. We version
   feature *definitions* and compute on demand, caching as a pure speed
   optimization. Raw data is the single source of truth.

---

## The metric kill-list (apply everywhere)

- **KILL** `P(session hits +10%)` — artifact of arbitrary windowing, not a property of the strategy.
- **KILL** win-rate in isolation — you can win 90% of trades and lose money.
- **KEEP / ADD** deflated Sharpe (haircut for number of trials), net-of-cost
  (turnover-adjusted) return, max drawdown **and** time-to-recovery, per-trade
  expectancy **with confidence intervals**, performance **conditional on regime**,
  and **capacity** (capital before impact eats the edge).

---

## WS1 — Research log  *(build first; everything writes to it)*

**Goal:** every experiment recorded immutably, so multiple-testing is auditable.

**Build:**
- `src/research_log/` — append-only log (SQLite or JSONL).
- One row per backtest: UTC timestamp, **git commit of the code**, **data snapshot
  id/hash**, universe definition used, full params, all metrics, and a running
  **`n_trials`** counter.
- Helper `log_experiment(...)` plus a query `how_many_trials()`.

**Defends against:** silent multiple testing. Deflated Sharpe is impossible
without a trial count, and PF > 1.5 appears by chance across hundreds of trials.

**Decision gate:** running any backtest auto-writes a row; you can answer
"how many configurations have I tried?" with one query.

---

## WS2 — Feature layer (versioned definitions, computed on demand)

**Goal:** features as pure, versioned functions over immutable raw — not a persisted store.

**Build:**
- `src/features/` — each feature is a pure function carrying a `version` string.
- A registry mapping name → (function, version).
- A cache keyed on `(feature_version, data_version, symbol, timeframe)` content hash.
  Cache is disposable; raw is truth. Changing a definition **bumps the version**
  and invalidates that cache entry.
- **Point-in-time correctness is mandatory here:** as-of joins for funding/OI
  (they publish with lag — a bar must never see funding it couldn't have known),
  and resampling that **closes bars on the right edge** so no bar sees its own future.

**Defends against:** lookahead leakage (the #1 cause of backtests that die live)
and untracked feature drift.

**Decision gate:** same `(feature_version, data_version)` always yields a
byte-identical result (hash test). A deliberate lookahead bug is caught by a
"feature at time t uses only data ≤ t" assertion test.

---

## WS3 — Walk-forward validation (purged + embargoed)

**Goal:** replace the single train/test split with rolling out-of-sample testing.

**Build:**
- `src/validation/` — a splitter yielding rolling `(train, test)` windows moving
  forward in time, with **purging** (drop training samples whose label window
  overlaps the test window) and an **embargo** gap (kill serial-correlation leakage).
- Wrap the backtest so results are reported **per-fold and aggregated**, and every
  run writes to the research log (WS1) with the current trial count.
- Report **deflated Sharpe** using `n_trials` from the log.

**Defends against:** the test set silently becoming an in-sample set the moment you
iterate on it; over-optimistic single-split results.

**Decision gate:** validation runs across N folds and reports per-fold dispersion.
A result is "real" only if it survives out-of-sample folds **and** deflation —
not because one split looked good.

---

## WS4 — Point-in-time / dynamic universe

**Goal:** reconstruct "top-N by trailing volume **as of each date**," including
symbols that later died. (See caveat #1 — this gates belief in WS3.)

**Build:**
- `src/universe/` — from raw, compute rolling volume (e.g. trailing 30d) per symbol
  per date; emit a **daily membership table**.
- Handle listing/delisting: a symbol is present until it actually delists, then drops
  out — it does not retroactively vanish from history.
- Backtests draw their universe **as-of the simulated date**, never from today's list.

**Defends against:** survivorship + selection bias — the single most likely source
of a fake edge in this whole project.

**Decision gate:** querying the universe as-of a past date returns symbols later
delisted. Spot-check: **does the 2022 membership include coins that are now zero?**
If the answer is no, it's still wrong.

---

## WS5 — Execution realism (one model, intrabar-aware, vol-targeted)

**Goal:** a single fill/sizing model carried identically through research → paper → live.

**Build:**
- `src/execution/` — one fill + slippage model used **everywhere** (no validating in
  vectorbt then trading in a different freqtrade fill model; that re-overfits at the
  boundary and the numbers won't reconcile).
- **Intrabar resolution:** use 1m base bars to adjudicate whether SL or TP hit first
  inside a higher-timeframe bar; if unavailable, assume **SL-first (worst case)**.
  Never assume TP-first — it silently inflates every result.
- **Funding as a real cost:** time-series funding applied via as-of join, not a flat
  constant. In trends it is directional and you pay to sit on the crowded side.
- **Volatility-targeted sizing:** size each position to a constant risk contribution.
  **Leverage becomes an output of the vol estimate, not a fixed 5x input.**

**Defends against:** backtests that assume fills they'd never get; uniform leverage
that under-risks calm regimes and liquidates in volatile ones.

**Decision gate:** backtest and paper share the **same** execution code path; with
vol-targeting on, realized per-trade risk is roughly constant across calm vs volatile
periods.

---

## After WS1–WS5 only: signal work

Now port the 9 engines — but as pure alpha functions evaluated **through** this
harness, with combination/weighting done by a regularized, cross-validated method,
not hand-set weights. Keep alpha generation, signal combination, and position sizing
as **three separate layers**. Fusing them (as the original design did) is an
overfitting machine.

---

## Datasets still missing (add as needed, not all now)

- Point-in-time listing/delisting dates (feeds WS4).
- Bid-ask spread or L2 depth (even proxied from high-low range) — required to model
  impact in WS5.
- **Liquidation data** — in perps, liquidation cascades *are* the moves.
- Cross-asset context: BTC dominance / total-market-cap / rolling beta to BTC —
  most alt moves are just BTC beta; a signal blind to this is trading noise.

(Funding + OI via the metrics dump are already pulled — that was the right call.)

---

## What a pro platform skips (so the agent doesn't over-build)

No streaming infra, no giant persisted feature store, no live-deploy scaffolding yet.
Knowing what to skip is part of the craft. Build WS1–WS5, nothing more, until a
signal survives this harness.

---

## How to drive this with the coding agent

One workstream at a time. Example:

> "Read notes/ARCHITECTURE_REVIEW.md. Implement **WS1 (research log) only**. Create
>  the files, write a test that proves the Decision gate, run it, show me the output,
>  then stop. Do not start WS2."

Review output → run the decision-gate test → unlock the next workstream.
You stay in control by reading results, not writing code.

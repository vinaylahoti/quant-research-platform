# WS6 — Port signal engines through the harness (alpha, combination, sizing as separate layers)

> Standalone spec. Implement only what's in this file.
> Depends on: WS1-WS5, all done and verified. This is the first workstream
> where a real trading edge gets tested — expect mixed results, not a clean
> sweep. That is the harness working, not failing.

---

## Why this is different from WS1-WS5

Every prior workstream was mechanical: build a defense, prove it works with a
pass/fail test. This one is judgment-heavy and probabilistic. Some of the 9
engines will likely not survive contact with a purged walk-forward test on a
point-in-time universe. That is the harness correctly telling you something
true about a signal that looked fine on paper — not a bug to fix.

**Do not let "the backtest came back bad" trigger silently loosening the
gates from WS1-WS5 to make a number look better.** If a signal fails, the
honest move is recording that failure (WS1 logs it either way) and either
dropping the signal or redesigning it — not reaching back into WS3's deflation
math or WS4's universe to relax things until it passes.

---

## Goal

Port the 9 existing signal engines into this codebase as pure, testable alpha
functions, combine them through a method that is itself validated rather than
hand-tuned, and run the result through the full WS1-WS5 harness to get an
honest answer: does this have edge, after costs, after deflation, on a
point-in-time universe, with realistic fills?

## The three layers (build and keep these separate — do not fuse them)

### Layer 1 — Alpha generation (`src/signals/`)

- One file per engine, e.g. `src/signals/engine_01_<name>.py` through `engine_09`.
- Each engine is a **pure function**: `f(features_df) -> signal_series`, where
  `features_df` comes from WS2's versioned feature layer (not raw klines
  directly — go through the feature layer so point-in-time correctness carries
  through automatically).
- Each engine's signal output should be a consistent shape (e.g. a series in
  `[-1, 0, 1]` for short/flat/long, or a continuous score — pick one convention
  and apply it to all 9, document the choice).
- No I/O, no sizing, no leverage, no SL/TP logic in this layer. An engine only
  answers "what does this indicator think right now."
- Each engine gets its own decision-gate-style determinism test: same input
  features always produce the same signal output (same discipline as WS2's
  hash test).

### Layer 2 — Signal combination (`src/signals/combine.py`)

- This is the layer the original design fused into hand-set weights and
  "confidence scaling." That is now explicitly disallowed.
- Combination weights must be **learned through WS3's validation harness**, not
  set by hand. Concretely: treat different weighting schemes (or a regression/
  ML method fit on training folds) as the **candidate batch** WS3's deflated
  Sharpe calculation expects — this is exactly the `candidate_sharpes` input
  WS3 was built to consume. Each weighting scheme tried is one candidate in the
  batch, and the batch size (`N`) genuinely reflects how many combination
  schemes were attempted.
- Start simple: even an equal-weight baseline and a couple of basic
  cross-validated alternatives (e.g. inverse-volatility weighting, a simple
  ridge/logistic combiner fit per training fold) is enough to make this a real
  comparison rather than a single hardcoded guess.
- Output of this layer: a single combined signal series, with the weighting
  scheme and its WS1-logged provenance fully traceable.

### Layer 3 — Position sizing (already built — reuse, don't reimplement)

- This is WS5's `src/execution/` model. The combined signal from Layer 2 feeds
  into WS5's volatility-targeted sizing — leverage is still an *output* of the
  vol estimate, never a constant set per-signal.
- Do not let this layer creep back into Layer 1 or Layer 2 files. If a signal
  engine or the combiner starts computing position size or leverage directly,
  that is the fusion bug the architecture review warned about, happening again.

## Wiring through the full harness

- The combined, sized signal runs through WS3's purged walk-forward splitter.
- The universe at each point in time comes from WS4's point-in-time membership
  table (including the bounded LUNA/FTT survivorship fix) — never from today's
  `config/settings.py` list directly.
- Every fill, every SL/TP/funding cost comes from WS5's shared execution model
  — the same code path used in the WS5 decision gate, not a new approximation.
- Every run — each combination scheme tried, each fold — auto-logs to WS1.
  This is the real, intended use of the auto-logging wired in WS3: by the time
  this workstream is done, `how_many_trials()` should return a number that
  honestly reflects how many combination schemes and configurations were
  actually tried, because that count feeds directly into how much the result
  gets deflated.

## What this defends against

A signal (or a combined portfolio of signals) looking profitable purely because
of survivorship bias, lookahead leakage, optimistic fills, or because enough
configurations were tried that something was bound to look good by chance.
Every one of those is a real failure mode this exact harness was built to catch
— this is the first workstream where they get tested against something with
real economic content instead of a fixture or a noise signal.

## Decision gate

- All 9 engines pass their individual determinism tests.
- At least 2 distinct combination schemes were actually run (not just planned)
  through WS3, so the candidate batch for deflation is genuine, not a stub.
- The research log (WS1) shows a real, non-trivial trial count by the end of
  this workstream — query `how_many_trials()` and report the number.
- Report, honestly, whichever of these is true:
  - The deflated Sharpe and profit factor clear the bar (**PF > 1.5, deflated**)
    on out-of-sample folds — in which case, name which combination scheme and
    which subset of engines actually carried the result.
  - They do not clear the bar — in which case say so plainly, report the real
    numbers, and do not propose loosening WS1-WS5's gates to fix it. The next
    move in that case is redesigning signals or combination, not redefining
    "pass."
- Either outcome is an acceptable result for this decision gate. A poor result
  honestly reported is the gate passing; a good result obtained by quietly
  relaxing an earlier workstream's defense is the gate failing, even if the
  printed number looks nice.

## Stop condition

Build this only. Show: the per-engine determinism test results, the candidate
combination schemes actually tried, the real trial count from WS1, and the
honest final answer on whether anything cleared the deflated PF > 1.5 bar.
Do not start Phase 4 (the freqtrade paper engine) until this is reviewed —
that decision (move to paper trading, redesign signals, or stop) should be made
deliberately, not as an automatic next step.

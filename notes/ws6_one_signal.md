# WS6 — One signal, end to end, proven honestly

> Standalone spec. Implement only what's in this file.
> Depends on: WS1-WS5, all done and verified.
> This is the first workstream that tests something with real economic content.
> Expect the honest answer to possibly be "no edge." That is the machine
> working, not failing.

---

## Scope discipline — read this first

This workstream builds **exactly one signal** and runs it through the full
WS1-WS5 harness. Not 3-5. Not a combiner. **One.**

Why one and not many:
- The first time real signals flow through WS3 (validation) + WS4 (universe) +
  WS5 (execution) together, there will be wiring bugs — date misalignments,
  universe-membership lookups returning the wrong day, funding joins off by a
  period. With one simple, hand-checkable signal, those bugs are findable.
  With five signals plus a learned combiner, a wiring bug hides inside
  "well, the combination is complex" and you never catch it.
- Building five signals before proving the harness end-to-end works is the same
  mistake as paper-trading everything to "see what happens" — it's experimenting
  before the measuring instrument is verified.

Multiple signals and the combination layer are **WS7**, and only start after
WS6 proves a single signal flows through the whole harness correctly. The
correctness of the plumbing is what WS6 establishes; whether any *given* signal
has edge is secondary to that.

---

## Goal

Take ONE simple, fully explainable signal, run it through the complete
WS1-WS5 harness, and get an honest, deflated, out-of-sample answer to: does
this one signal have any edge on USDT-M perps after costs — and, more
importantly for this workstream, **does the full pipeline actually work
end-to-end without lookahead, survivorship, or fill bugs.**

## Pick the one signal (do this first, in writing)

Choose a single signal that is simple enough to verify its output by hand on a
few bars. Recommended starting choice: **time-series momentum** (e.g. sign of
the N-period return — go long if the trailing return is positive, short if
negative, on a clear timeframe like 4h or 1d). Reasons it's the right *first*
signal specifically:
- It is the most robustly documented source of return in futures markets across
  decades of academic literature — if anything has a fighting chance of
  surviving the harness, a momentum baseline does.
- It is trivial to hand-check: you can eyeball whether "trailing return was
  positive, so signal = long" is correct on any given bar.
- It has one obvious parameter (the lookback), which keeps the trial count
  honest and small for this first pass.

Write down, in the research log or a companion note, the exact definition
chosen (timeframe, lookback, long/short/flat rule) before building it. No
parameter sweeping yet — pick one lookback, defensibly, and commit to it for
this workstream.

## The two layers (sizing is already built)

### Layer 1 — The signal (`src/signals/`)

- One file, the chosen signal as a **pure function**: `f(features_df) ->
  signal_series`, output in a documented convention (e.g. `[-1, 0, 1]`).
- Input comes from WS2's versioned feature layer, never raw klines directly, so
  point-in-time correctness carries through automatically.
- No I/O, no sizing, no leverage, no SL/TP logic in this file. The signal only
  answers "long, short, or flat right now."
- Determinism test: same input features always produce identical signal output
  (same discipline as WS2's hash test).

### Layer 2 — Sizing (reuse WS5, do not reimplement)

- The signal feeds WS5's `src/execution/` volatility-targeted sizing. Leverage
  stays an *output* of the vol estimate. No sizing or leverage logic leaks back
  into the signal file.

(There is no combination layer in WS6 — there's only one signal to combine.
That's WS7.)

## Wiring through the full harness — this is the real point of WS6

Run the single sized signal through:
- WS3's purged + embargoed walk-forward splitter (per-fold + aggregate results).
- WS4's point-in-time universe (including the bounded LUNA/FTT fix) — universe
  membership drawn **as-of each simulated date**, never from today's list.
- WS5's shared execution model — the same code path proven in the WS5 gate.
- WS1 auto-logging every run.

Because there's only one signal with one parameter, the candidate batch for
WS3's deflated Sharpe is small and honest (likely N=1 or a tiny N if you also
log a random-signal control alongside it — see below).

## The control that makes the result trustworthy

Alongside the real momentum signal, run a **random/shuffled-signal control**
through the identical harness (same universe, same execution, same folds). This
is the single most important verification in WS6:
- The momentum signal's result is only meaningful *relative to* what pure noise
  scores on the exact same pipeline.
- If a random signal also shows positive deflated Sharpe on this pipeline, there
  is a leak somewhere (lookahead, survivorship, or fill optimism) — and the
  momentum result is worthless until that leak is found. This is the same
  noise-check logic that validated WS3's splitter; here it validates the *whole
  pipeline* end-to-end.

## Decision gate

- The signal passes its determinism test.
- The full harness runs end-to-end: signal → WS5 sizing → WS4 universe → WS3
  folds → WS1 log, with no errors and no manual data patching between stages.
- The random-signal control comes back with **no meaningful edge** (deflated
  Sharpe near zero / not clearing the bar). If the control shows edge, STOP —
  there's a leak, and finding it is the real task before any signal result can
  be believed.
- Report honestly, whichever is true:
  - Momentum clears deflated PF > 1.5 out-of-sample while the control does not —
    a real (if preliminary) positive. Name the exact signal definition.
  - Momentum does NOT clear the bar — report the real numbers plainly. This is a
    completely acceptable WS6 outcome. The pipeline being proven correct is the
    win; the signal not having edge yet is information, not failure. Do not
    loosen any WS1-WS5 gate to manufacture a pass.

A correctly-working pipeline that honestly reports "this signal has no edge" is
WS6 passing. A nice-looking number obtained by relaxing an earlier defense is
WS6 failing, even if it prints green.

## Stop condition

Build this only. Show: the determinism test result, confirmation the full
pipeline ran end-to-end, the random-control result, the real trial count from
WS1, and the honest momentum result. Do not build a second signal or a
combiner (that's WS7), and do not start paper trading. The decision about what
comes next — iterate signals, or move a survivor toward paper — is made
deliberately after reviewing this, never automatically.

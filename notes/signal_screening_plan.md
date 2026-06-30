# Signal Screening & Combination Plan

> The strategy for everything after WS6/WS7. Supersedes the
> "one signal at a time, hope it clears PF 1.5 alone" framing.
> Read alongside `notes/ARCHITECTURE_REVIEW.md`.

---

## The core idea (read this first, it changes everything downstream)

We are **not** hunting for one magic signal that clears PF > 1.5 by itself.
Almost no single raw signal does that on retail-dominated crypto perps after
costs — WS6 momentum scoring near-zero is the *normal* result, not a failure.

The actual strategy: **find several individually-weak-but-real signals that are
uncorrelated with each other, then combine them so their errors partially
cancel and their small edges add.** Three signals that are each right 52% of
the time, if they're wrong at *different* times, combine into something
meaningfully better than any of them alone. That combination is where edge
either emerges or doesn't — not in any solo signal's score.

Two traps this plan exists to avoid:
1. **Test-many-pick-the-best = overfitting.** If we test 10 signals and keep
   whichever scored highest, we've picked the luckiest, not the best. The
   deflated-Sharpe / trial-count machinery from WS3 exists precisely to catch
   this — it must be applied to the whole screening campaign, not per signal.
2. **Combining redundant signals does nothing.** If momentum, OI-change, and
   volume-imbalance are all secretly "is price going up," they're correlated,
   wrong at the same time, and stacking them adds complexity but no
   diversification. Correlation between signals must be measured, not assumed.

---

## The hard rule that broke WS7 (do not repeat)

**Never loosen a signal's definition to manufacture trades.** WS7's OI-extreme
signal got walked from z>=2.0 to z>=0.0 to make enough trades appear — at
z>=0.0 it no longer tested "positioning extreme," it tested "OI above its mean
~50% of the time," a different and much weaker hypothesis.

If a signal's honest definition produces too few trades to validate, that is
**information, not a bug to patch around**:
- Either the signal genuinely fires rarely (extremes are rare) → accept fewer,
  more meaningful trades and use a longer test window to accumulate enough.
- Or the lookback is mis-scaled → shorten the *lookback* (what "extreme" is
  measured against), which is a legitimate scale choice, NOT lowering the
  threshold that defines the event.
- Or the signal just doesn't fit this market → record that and move on.

Commit each signal's exact rule **in writing before running it.** The rule does
not change after seeing the result.

---

## The screening criterion (weaker and smarter than "PF > 1.5 alone")

For screening candidates, the question is NOT "does this clear PF 1.5 by
itself." It's:

> **Does this signal, defined honestly, beat its seeded random control by a
> small but consistent margin across multiple years (2022/2023/2024), and is it
> cheap to compute?**

We're looking for *real-but-weak*, not *winner*. A signal that beats its control
by a few percent consistently across three years is a keeper for the
combination stage, even if its solo PF is nowhere near 1.5.

"Consistent" matters more than "large": a signal that's +slightly in all years
is far more valuable than one that's +huge in 2024 and negative in 2022/2023
(that's just 2024's market direction, the WS6 momentum lesson).

**Use the full available history per signal, not a fixed window.** The data
spans 2021-2025, but it's not uniform: price-only signals (momentum,
cross-sectional, volatility, volume) have ~5 years (2021-2025) for established
symbols; OI-based signals have ~4 years (OI metrics start ~late-2021). Later
listings (TON 2024, SUI/ARB 2023) naturally contribute less — the point-in-time
universe handles that correctly. More years = more independent regimes = a
harder-to-fool screen, so use all that's available for each signal rather than
truncating to a shared 3-year window.

---

## The signal queue — testable with data ALREADY on disk

Each gets one honest pure-function definition, a determinism test, and a
multi-year harness run with a seeded random control. Committed rules to be
written into each signal file before running.

1. **Momentum (4h/20-bar)** — DONE (WS6). No edge. Keep as the baseline
   reference every other signal is implicitly compared against.
2. **OI-extreme mean-reversion** — WS7, to be redone with an HONEST threshold
   (z>=1.5 or higher = genuinely rare) and a shorter OI lookback (e.g. 30-40
   bars) to make real extremes fire often enough, WITHOUT lowering the
   threshold. If real extremes are too rare even then, that's a finding.
3. **OI *change* (not level)** — signal off the rate-of-change / acceleration
   of open interest rather than its level. Often a different bet than OI level.
4. **Cross-sectional momentum** — rank all WS4-universe symbols against *each
   other* each period; long the strongest, short the weakest. Structurally
   different from time-series momentum (relative, not absolute).
5. **Volatility expansion/contraction** — signal off realized-vol regime
   changes (e.g. a vol breakout after a quiet period). Uses only price data.
6. **Volume / taker-flow imbalance** — use the `taker_buy_base`/`taker_buy_quote`
   columns already in the feature store to measure buy/sell pressure imbalance.

## Shelved — needs data NOT on disk (do not attempt without acquiring it first)

- **Funding-rate extremes** — funding-rate data was never downloaded; closed as
  out of scope (ARCHITECTURE_REVIEW Known Limitation #2).
- **Basis / futures premium** — needs spot price data we don't have.
- **Liquidation-cascade mean-reversion** — needs liquidation data we don't have.

Naming what's out is half the discipline — don't quietly start one of these.

---

## The four-move process (this is the actual plan)

**Move 1 — Screen for real-but-weak.** Run each queued signal through the
proven harness across the **full available history per signal** (~5 years for
price signals, ~4 for OI signals), honest definition, seeded control. Record
each signal's per-year result vs its control in the research log (WS1). Most
will be near-zero — expected. Output: a shortlist of signals that beat their
control *consistently* across years.

**Move 2 — Measure correlation between survivors.** For the shortlisted
signals, compute how correlated their signal outputs (and their returns) are
with each other. Keep a set that is genuinely *different* bets; discard
redundant ones (two signals that are ~the same bet add no diversification).
A small set of uncorrelated weak signals beats a large set of correlated ones.

**Move 3 — Combine the uncorrelated survivors.** Build the combination layer
the original architecture review specified: weights learned via a
**regularized, cross-validated** method (NOT hand-set), with each weighting
scheme treated as a candidate in WS3's deflated-Sharpe batch so the
multiple-testing penalty is honest. Keep alpha generation, combination, and
sizing as separate layers — fusing them is the overfitting machine the review
warned about.

**Move 4 — Judge the combination, deflated.** Does the *combination* clear the
bar that no individual signal did — AFTER applying the deflated-Sharpe penalty
for every signal AND every combination scheme tried across the whole campaign?
That total trial count is what makes this honest. If yes: a preliminary real
system, candidate for paper trading. If no: either more/different uncorrelated
signals are needed, or this market doesn't yield to this approach — both are
legitimate, honestly-reported outcomes.

---

## What success and failure both look like (so neither fools you)

- **Honest success:** a combination of 3+ uncorrelated, individually-weak
  signals clears a deflated bar that accounts for the full search. Preliminary,
  pre-funding-cost, still needs paper validation — but real.
- **Honest failure:** nothing combines into deflated edge. This is a complete,
  respectable result. Most retail quant efforts produce this and the honest
  ones admit it. It is vastly better than a fake success from an un-deflated
  lucky combination.
- **The failure mode to fear is neither of those — it's a nice-looking number
  obtained by loosening a signal definition, skipping the correlation step,
  hand-tuning combination weights, or forgetting to deflate for the full trial
  count.** Every one of those produces a mirage. The whole plan is built to
  make those mirages impossible to mistake for the real thing.

---

## How to run this (pacing)

One signal at a time, attended — each harness run is ~10-15 min, but the
judgment between runs (is the definition honest? does the result mean what it
appears to?) is the part that matters and can't be unattended. Do NOT batch
all signals to run unsupervised; that's how WS7's threshold got loosened
without anyone noticing. Screen the queue over several short sessions, then do
correlation + combination as their own deliberate steps once the shortlist
exists.

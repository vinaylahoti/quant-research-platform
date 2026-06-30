# WS5 — Execution realism (one model, intrabar-aware, vol-targeted)

> Standalone spec. Implement only what's in this file.
> Full context (if ever needed): `notes/ARCHITECTURE_REVIEW.md`.
> Depends on: WS1-WS4 done. This is the last workstream before signal work.

---

## Why this exists

For a high-turnover perp strategy, the fill model basically *is* the strategy.
A naive backtest assumes fills it would never get live, and the gap between
"validated in vectorbt" and "traded in freqtrade" is itself a source of fake
edge if the two use different fill assumptions.

## Goal

One fill/sizing model, used identically in research, paper, and (eventually)
live — never re-derive or re-approximate execution at each engine boundary.

## Build

- `src/execution/` — single fill + slippage model, importable and usable from
  both the WS3 validation harness and the eventual paper-trading engine. Do not
  let vectorbt-style research use one fill model while freqtrade dry-run uses
  another — that re-overfits at the boundary and the numbers won't reconcile.
- **Intrabar resolution:** use 1-minute base bars (already in the feature store)
  to determine whether a stop-loss or take-profit was hit first inside a
  higher-timeframe bar. If 1m resolution is genuinely unavailable for a
  particular check, default to **assuming SL hit first (worst case)**.
  Never assume TP-first by default — it silently inflates every backtest result.
- **Funding as a real, time-varying cost:** apply the actual funding rate
  time-series via an as-of join (already built in WS2's point-in-time metrics
  join), not a flat constant. Funding is directional in trends — a momentum
  signal can end up paying to sit on the crowded side.
- **Volatility-targeted sizing:** size each position so its *expected risk
  contribution* is roughly constant, instead of using a fixed leverage input.
  Leverage becomes an **output** of the volatility estimate (e.g. from realized
  vol over a trailing window), not a constant set in `config/settings.py`.

## What this defends against

Backtests that assume fills they'd never actually get. Fixed leverage that
under-risks calm regimes and risks liquidation in volatile ones.

## Decision gate

- The backtest (WS3) and the eventual paper engine call the **same** execution
  code path — prove this by having both consume `src/execution/` rather than
  each implementing their own fill logic.
- With vol-targeting enabled, run the model across a calm period and a volatile
  period (can use real historical segments already in the feature store) and
  confirm realized per-trade risk is roughly constant across both — not that
  leverage is constant while risk varies.
- Confirm the intrabar SL/TP resolution test: construct a case where a 15m bar's
  high and low would trigger both SL and TP, and confirm the model picks SL-first
  by default rather than silently assuming the favorable outcome.

## Stop condition

Build this only. After this, nothing else in WS1-WS5 remains — signal work
(porting the 9 engines through this full harness) is next, but that's a
separate conversation, not part of this file's scope. Show the decision-gate
output, then stop.

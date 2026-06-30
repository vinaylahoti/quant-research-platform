# WS4 — Point-in-time / dynamic universe

> Standalone spec. Implement only what's in this file.
> Full context (if ever needed): `notes/ARCHITECTURE_REVIEW.md`.
> Depends on: WS1, WS2 done. Build after WS3.

---

## Why this exists

This is the single most likely source of a fake edge in the whole project.
"Top-30 by volume" selected **today**, then backtested over 5 years, bakes in
survivorship bias: today's list is made of coins that survived and grew. Coins
that died (delisted, went to zero) are invisible to a backtest that only looks
at today's winners. A mediocre strategy looks profitable purely from this.

**Anything validated in WS3 before this exists is plumbing-only, not evidence.**
This workstream is what upgrades WS3's output from "the mechanism works" to
"the result might be real."

## Goal

Reconstruct "top-N by trailing volume **as of each date**," including symbols
that later died, so backtests never get to peek at the future composition of
the universe.

## Build

- `src/universe/` — from raw kline data, compute rolling volume (e.g. trailing
  30 days) per symbol per date.
- Emit a **daily membership table**: for each date, which symbols are "in" the
  top-N as of that date, using only information available up to that date.
- Handle listing/delisting explicitly: a symbol is present in the universe until
  it actually delists or its data ends, then drops out. It must not retroactively
  vanish from earlier history just because it's gone today, and it must not
  appear in dates before it was listed.
- Backtests must draw their universe **as-of the simulated date** — never from
  the current top-30 list in `config/settings.py`. That config list becomes a
  *seed/reference* set for what to download, not the backtest universe itself.

## What this defends against

Survivorship and selection bias.

## Decision gate

- Query the universe as-of a date in the past (e.g. early 2022) and confirm it
  returns symbols that have since delisted or gone to zero — not just symbols
  still in today's top-30.
- Spot-check explicitly: **does the 2022 membership include coins that are now
  effectively worthless or delisted?** If the answer is no, the reconstruction
  is still wrong — it's just reproducing today's list with extra steps.
- Confirm a symbol that listed partway through history (e.g. one of the 2022/2023
  listings already in the data, like APT or ARB) does not appear in the universe
  before its actual listing date.

## Stop condition

Build this only. Do not start WS5. Show the decision-gate output — specifically
the 2022 spot-check result — then stop.

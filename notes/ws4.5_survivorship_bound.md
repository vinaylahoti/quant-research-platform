# WS4.5 — Bounded survivorship mitigation (scoped, not exhaustive)

> Standalone spec. Implement only what's in this file.
> Depends on: WS4 (point-in-time universe ranking) — done, logic confirmed correct.
> This file exists because WS4's ranking is correct, but its INPUT universe was
> too narrow to prove the thing WS4 was built to prove. This closes that gap
> to a deliberately bounded, "good enough" degree — not to perfect completeness.

---

## Why this exists (context, do not re-litigate)

WS4's as-of ranking logic was verified correct — it derives membership purely
from historical volume in the feature store, with no anchoring to
`config/settings.py`. But the 2022-06-01 vs latest-date turnover check showed
**zero symbols dropped out** — every 2022 symbol is still present today. That's
not a WS4 bug. It's because the feature store only ever contained the 30
symbols chosen by looking at TODAY's volume (Phase 1), so no historically-died
coin could ever appear in the ranking, regardless of how correct the ranking
math is.

**Decision (made deliberately, not by default):** there is no clean, complete,
machine-readable catalog of every USDT-M perp that ever existed on Binance.
Building one is an open-ended research problem with no guaranteed end state.
Rather than chase full completeness, this workstream adds a **small, bounded**
set of known historically-significant delisted symbols and stops there. The
result will be measurably better than before, explicitly **not** claimed to be
exhaustive, and documented as a known, permanent limitation.

---

## Goal

Add a short, fixed list of delisted symbols to the data layer so the WS4
turnover check shows real, genuine dropout — not zero — without attempting to
reconstruct the full historical universe.

## Build

1. **Candidate list (fixed, do not expand without discussion):**
   - `LUNAUSDT` — collapsed May 2022, was a top-10 perp by volume before that.
   - `FTTUSDT` — FTX token, delisted/collapsed November 2022, was actively traded.
   - (Optional third, only if trivially available: `SRMUSDT` — Serum, FTX-ecosystem,
     also collapsed in the same period. Confirmed retrievable via the same archive
     pattern. Include only if it doesn't meaningfully add scope/time.)

2. **Probe before downloading:** for each candidate symbol, confirm via HTTP HEAD
   (not a full download) that monthly 1m kline archives exist on
   `data.binance.vision` for the relevant historical window (roughly 2021-01
   through the symbol's collapse date). This was already partially confirmed —
   reuse that finding rather than re-probing from scratch.

3. **Download:** extend `download.py` (Phase 1) or run a small standalone script
   to pull klines (skip `metrics`/funding for these — they collapsed before or
   near when Binance's metrics dataset starts, so funding/OI history is likely
   sparse or nonexistent; klines alone are enough to fix the ranking-input gap).

4. **Feature store:** build parquet for these symbols the same way as the
   existing 30 (reuse `featurestore.py`, no new logic needed).

5. **Re-run WS4's as-of ranking** including the new symbols in the candidate pool.

## What this defends against — and what it explicitly does NOT defend against

**Does fix:** the most egregious, well-known cases of survivorship bias in this
specific dataset — a backtest can no longer be silently blind to the two most
famous crypto collapses of the period it covers.

**Does NOT fix:** the general case. Smaller delisted symbols, less famous
collapses, and anything outside this fixed list remain invisible to the
universe. This is a deliberate, bounded improvement — not a solved problem.

## Decision gate

- Re-run the WS4 turnover check (2022-06-01 vs latest date, by symbol name).
- Confirm `LUNAUSDT` (and `FTTUSDT`, if its collapse date falls within the
  comparison window) now appears in the 2022 list and is correctly absent from
  the latest-date list.
- Confirm turnover is no longer "none dropped" — at least one real, genuine
  dropout must now appear.

## Required documentation (do this regardless of the gate result)

Add a clearly-labeled section to `README.md` or `notes/ARCHITECTURE_REVIEW.md`:

> **Known limitation — universe completeness.** The point-in-time universe
> includes the current top-30 actively-traded symbols plus a small fixed set of
> historically significant delisted symbols (LUNA, FTT). It is NOT a complete
> historical reconstruction of every symbol that ever traded. Smaller delisted
> coins are not represented. Backtest results should be read with this in mind —
> survivorship bias is reduced, not eliminated.

This note must exist before any signal work uses WS4's universe output. Do not
let this limitation be discovered later by surprise.

## Stop condition

Build this only. Do not start WS5. Show the decision-gate output (the updated
turnover list with real dropout) and confirm the documentation was added, then
stop.

"""
Long-horizon momentum screen — does extending WS6's lookback/hold show edge?

WS6 used: 4h timeframe, 20-bar lookback (~3.3 days), 1h hold. → null.

This checks: same signal logic (trailing-return sign), same universe, same costs,
but at horizons where the carry simulation accidentally caught multi-month trends:
  - (20d lookback, 20d hold)  — roughly monthly rebalance
  - (60d lookback, 20d hold)  — quarterly signal, monthly rebalance
  - (90d lookback, 30d hold)  — 3-month trend, monthly-ish rebalance
  - (180d lookback, 30d hold) — semi-annual trend, monthly rebalance

Critical discipline (from research principles):
  Screen on signal-vs-control GAP, not absolute return.
  A trend in the market (2022 crash, 2023 rally) will lift both signal AND
  random control. Only the gap reveals whether the signal has directional content
  beyond random participation in the same underlying move.

Execution model:
  - Vol-targeted sizing (WS5 parameters: target_risk=1%, max_lev=5x, min_lev=0.1x)
  - Round-trip cost at each rebalance: 12 bps × leverage (taker 4bps + slippage 2bps, both sides)
  - No intrabar SL/TP — at 20-30 day holds this is the correct primitive
  - P&L = signal × forward_return × leverage − round_trip_cost

Signal: trailing lookback-day close-to-close return > 0 → long (+1), < 0 → short (-1), == 0 → flat (0)
Control: random ±1 assignment, seeded deterministically from symbol + year + combo.

Annual structure: each calendar year is reported separately (natural OOS — no data leakage
between years when we report each independently, and no optimization is done across years).

Run:
    $env:PYTHONPATH = '.'
    py -3 notes/long_horizon_momentum.py
"""

from __future__ import annotations

import hashlib
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.data.featurestore import FeatureStore
from src.validation.metrics import compute_sharpe_ratio
from config.settings import SYMBOLS

# ── Parameters (locked before running) ───────────────────────────────────────
COMBINATIONS: list[tuple[int, int]] = [
    (20,  20),   # roughly monthly lookback and hold
    (60,  20),   # quarterly signal, monthly hold
    (90,  30),   # 3-month trend, 30-day hold
    (180, 30),   # semi-annual trend, 30-day hold
]
TARGET_RISK_PCT   = 0.01    # 1% daily risk target
MAX_LEVERAGE      = 5.0
MIN_LEVERAGE      = 0.1
VOL_LOOKBACK      = 20      # trailing days for realized vol sizing
TAKER_FEE_BPS     = 4.0    # per side
SLIPPAGE_BPS      = 2.0    # per side
ROUND_TRIP_BPS    = 2 * (TAKER_FEE_BPS + SLIPPAGE_BPS)    # 12 bps
RANDOM_SEED_BASE  = 20260629
YEARS             = [2022, 2023, 2024, 2025]
START_WARMUP      = "2021-01-01"   # warmup so earliest bars have full lookback
END               = "2025-12-31"

store = FeatureStore()
_closes_cache: dict[str, pd.Series] = {}


def _closes(symbol: str) -> pd.Series:
    if symbol not in _closes_cache:
        df = store.load(symbol, start=START_WARMUP, end=END, timeframe="1d")
        _closes_cache[symbol] = df["close"]
    return _closes_cache[symbol]


def _realized_vol(daily_ret: pd.Series, t: pd.Timestamp) -> float:
    window = daily_ret.loc[:t].iloc[-VOL_LOOKBACK:]
    if len(window) < 5:
        return 0.0
    return float(window.std(ddof=0))


def _leverage(daily_ret: pd.Series, t: pd.Timestamp) -> float:
    vol = _realized_vol(daily_ret, t)
    if vol <= 0:
        return MAX_LEVERAGE
    return float(np.clip(TARGET_RISK_PCT / vol, MIN_LEVERAGE, MAX_LEVERAGE))


def _random_signal(seed_material: str) -> int:
    h = int(hashlib.sha256(seed_material.encode()).hexdigest()[:16], 16)
    rng = np.random.default_rng(h % (2**32))
    return int(rng.choice([-1, 1]))


@dataclass
class TradeResult:
    signal:       int     # +1 long, -1 short, 0 flat
    ctrl_signal:  int
    forward_ret:  float   # raw price return over hold period
    leverage:     float
    sig_gross:    float   # signal × forward_ret × leverage
    ctrl_gross:   float
    cost:         float   # round-trip cost (negative)
    sig_net:      float
    ctrl_net:     float


def simulate_symbol_year(
    symbol: str,
    year: int,
    lookback: int,
    hold: int,
) -> list[TradeResult]:
    """
    Simulate one symbol's rebalances within a calendar year.

    Each "trade" is generated at the start of each hold-period window:
    entry at close of day t, exit at close of day t+hold.
    """
    closes = _closes(symbol)
    daily_ret = closes.pct_change().dropna()

    # Study boundary: signal generated on any day in [year-01-01, year-12-31]
    # that has a full lookback and a full hold period ahead of it.
    yr_start = pd.Timestamp(f"{year}-01-01", tz="UTC")
    yr_end   = pd.Timestamp(f"{year}-12-31", tz="UTC")

    # All daily timestamps available for this symbol within the year
    available = closes.index
    # Timezone-aware comparison
    if available.tzinfo is None:
        available = available.tz_localize("UTC")

    year_dates = available[(available >= yr_start) & (available <= yr_end)]
    if len(year_dates) < hold + 5:
        return []

    results: list[TradeResult] = []

    # Step through non-overlapping hold-period windows
    i = 0
    while i < len(year_dates) - hold:
        entry_ts = year_dates[i]
        exit_ts  = year_dates[min(i + hold, len(year_dates) - 1)]

        # Need full lookback before entry
        loc = closes.index.get_loc(entry_ts)
        if loc < lookback:
            i += hold
            continue

        # Signal: trailing lookback return ending at entry bar (bar close = known at entry)
        past_close = float(closes.iloc[loc])
        ref_close  = float(closes.iloc[loc - lookback])
        if ref_close == 0.0:
            i += hold
            continue
        trailing_ret = (past_close / ref_close) - 1.0
        sig = 1 if trailing_ret > 0.0 else (-1 if trailing_ret < 0.0 else 0)

        # Random control: seeded per (symbol, year, lookback, hold, i)
        seed_str = f"{RANDOM_SEED_BASE}:{symbol}:{year}:{lookback}:{hold}:{i}"
        ctrl = _random_signal(seed_str)

        # Forward return (exit close / entry close - 1)
        entry_price = float(closes.loc[entry_ts])
        exit_price  = float(closes.loc[exit_ts])
        if entry_price == 0.0:
            i += hold
            continue
        fwd_ret = (exit_price / entry_price) - 1.0

        # Vol-targeting leverage at entry
        if entry_ts in daily_ret.index:
            lev = _leverage(daily_ret, entry_ts)
        else:
            lev = MIN_LEVERAGE

        # Gross: signal × fwd_ret × leverage (long +1 earns positive fwd_ret)
        sig_gross  = sig  * fwd_ret * lev
        ctrl_gross = ctrl * fwd_ret * lev

        # Cost: round-trip per rebalance (regardless of direction)
        cost = -(ROUND_TRIP_BPS / 10_000.0) * lev

        results.append(TradeResult(
            signal=sig, ctrl_signal=ctrl,
            forward_ret=fwd_ret, leverage=lev,
            sig_gross=sig_gross, ctrl_gross=ctrl_gross,
            cost=cost,
            sig_net=sig_gross + cost,
            ctrl_net=ctrl_gross + cost,
        ))

        i += hold   # non-overlapping windows

    return results


def run_combo(lookback: int, hold: int) -> dict[int, dict]:
    """Run one (lookback, hold) combo across all symbols and years."""
    annual: dict[int, dict] = {}

    for year in YEARS:
        sig_rets:  list[float] = []
        ctrl_rets: list[float] = []
        trade_count = 0
        sym_count   = 0

        for symbol in SYMBOLS:
            try:
                trades = simulate_symbol_year(symbol, year, lookback, hold)
            except Exception:
                continue
            if not trades:
                continue
            sym_count += 1
            trade_count += len(trades)
            sig_rets.extend(t.sig_net  for t in trades)
            ctrl_rets.extend(t.ctrl_net for t in trades)

        if not sig_rets:
            continue

        sig_total  = sum(sig_rets)
        ctrl_total = sum(ctrl_rets)
        gap        = sig_total - ctrl_total
        sig_sharpe = compute_sharpe_ratio(sig_rets)
        ctrl_sharpe= compute_sharpe_ratio(ctrl_rets)

        annual[year] = {
            "sym_count":   sym_count,
            "trade_count": trade_count,
            "sig_total":   sig_total,
            "ctrl_total":  ctrl_total,
            "gap":         gap,
            "sig_sharpe":  sig_sharpe,
            "ctrl_sharpe": ctrl_sharpe,
        }

    return annual


def print_results(all_results: list[tuple[int, int, dict[int, dict]]]) -> None:
    print("=" * 100)
    print("LONG-HORIZON MOMENTUM  |  Signal vs Random Control  |  2022-2025")
    print(f"Universe: {len(SYMBOLS)} USDT-M symbols  |  Sizing: vol-target 1%/day risk, max 5x lev")
    print(f"Costs: {ROUND_TRIP_BPS}bps RT per rebalance (taker {TAKER_FEE_BPS}bps + slippage {SLIPPAGE_BPS}bps, both sides)")
    print(f"Signal: trailing close-to-close return sign (long if >0, short if <0)")
    print(f"Control: random +/-1, seeded deterministically per (symbol,year,combo,position)")
    print()

    # Per-combo table
    for lookback, hold, annual in all_results:
        trades_per_yr = sum(a["trade_count"] for a in annual.values()) / max(len(annual), 1)
        print(f"  Combo [{lookback}d lookback / {hold}d hold]  "
              f"avg {trades_per_yr:.0f} trades/yr across universe")
        print(f"  {'Year':<6} {'Syms':>5} {'Trd':>5} "
              f"{'Sig%':>8} {'Ctl%':>8} {'Gap%':>8}  "
              f"{'SigSh':>7} {'CtlSh':>7}  Verdict")
        print(f"  {'-'*85}")

        gaps = []
        for year in YEARS:
            if year not in annual:
                print(f"  {year:<6}  (no data)")
                continue
            a = annual[year]
            sig_pct  = a["sig_total"]  * 100
            ctrl_pct = a["ctrl_total"] * 100
            gap_pct  = a["gap"]        * 100
            gaps.append(gap_pct)
            verdict = "signal > ctl" if gap_pct > 0 else "signal < ctl"
            print(f"  {year:<6} {a['sym_count']:>5} {a['trade_count']:>5} "
                  f"{sig_pct:>+8.1f}% {ctrl_pct:>+8.1f}% {gap_pct:>+8.1f}%  "
                  f"{a['sig_sharpe']:>7.3f} {a['ctrl_sharpe']:>7.3f}  {verdict}")

        # Summary for this combo
        if gaps:
            n_pos = sum(1 for g in gaps if g > 0)
            avg_gap = sum(gaps) / len(gaps)
            consistent = n_pos == len(gaps)
            print(f"  {'':6} {'':5} {'':5} "
                  f"{'':8}  {'':8}  avg gap {avg_gap:>+.1f}%  "
                  f"[gap positive {n_pos}/{len(gaps)} years]  "
                  f"{'CONSISTENT' if consistent else 'INCONSISTENT'}")
        print()

    # ── Cross-combo verdict ───────────────────────────────────────────────────
    print("=" * 100)
    print("CROSS-COMBO SUMMARY: Does longer horizon produce consistent signal edge over control?")
    print()
    print(f"  {'Combo':<22} {'Avg gap%':>10} {'+gap yrs':>9}  {'Consistent?':>12}  Interpretation")
    print(f"  {'-'*90}")

    any_consistent = False
    for lookback, hold, annual in all_results:
        gaps = [a["gap"] * 100 for a in annual.values()]
        if not gaps:
            continue
        n_pos   = sum(1 for g in gaps if g > 0)
        avg_gap = sum(gaps) / len(gaps)
        consistent = n_pos == len(gaps)
        if consistent:
            any_consistent = True
        interp = "possible edge" if consistent and avg_gap > 0.5 else (
                 "weak/inconsistent" if avg_gap > 0 else "no edge")
        print(f"  [{lookback:>3}d lkbk / {hold:>2}d hold]  {avg_gap:>+10.1f}%  {n_pos:>4}/{len(gaps)}   "
              f"{'YES' if consistent else 'NO':>12}  {interp}")

    print()
    if any_consistent:
        print("  >> At least one combo shows consistent positive signal-vs-control gap.")
        print("     This is a non-null signal. Compare: WS6 (4h/20bar/1h) showed null at all horizons tested.")
        print("     Implication: longer-horizon trend-following may be the more buildable direction.")
        print("     Caution: 4 years is a short sample at monthly rebalance frequency.")
        print("     Next step: walk-forward validation through WS3 harness before any deployment claim.")
    else:
        print("  >> No combo shows a consistent positive signal-vs-control gap across all four years.")
        print("     The absolute returns that looked good in some years were driven by the underlying")
        print("     market trend (2022 crash, 2023-24 rally), not by the momentum signal's ability")
        print("     to correctly identify direction. Control captured the same moves.")
        print("     This is the same null as WS6, just at longer horizons. The carry result was")
        print("     a regime artifact, not a repeatable trend-following edge.")
        print("     Implication: strengthens the case that funding carry needs the spot hedge,")
        print("     not that long-horizon momentum is the alternative path.")

    print("=" * 100)

    # ── Year-level regime check ───────────────────────────────────────────────
    print()
    print("REGIME CONTEXT: What fraction of the universe the signal correctly directioned each year")
    print("(If signal edge is real, should be >50% hit rate on correct direction in most years)")
    print()
    # Use 90d/30d as representative combo
    rep = next((a for lb, h, a in all_results if lb == 90 and h == 30), None)
    if rep:
        print(f"  [90d lookback / 30d hold]  — direction hit rate across universe")
        for year in YEARS:
            if year not in rep:
                continue
            # Re-run to get direction stats
            correct = 0
            total   = 0
            for symbol in SYMBOLS:
                try:
                    trades = simulate_symbol_year(symbol, year, 90, 30)
                except Exception:
                    continue
                for t in trades:
                    if t.signal == 0:
                        continue
                    total += 1
                    # Correct if signal × forward_ret > 0
                    if t.signal * t.forward_ret > 0:
                        correct += 1
            hit_rate = correct / total if total > 0 else float("nan")
            print(f"  {year}: {correct}/{total} correct direction  ({hit_rate:.1%} hit rate)  "
                  f"{'> 50% edge possible' if hit_rate > 0.52 else '~= 50% no edge'}")


if __name__ == "__main__":
    print("Loading daily closes (cached)...")

    # Pre-warm close cache for all symbols
    for sym in SYMBOLS:
        try:
            _closes(sym)
        except Exception:
            pass

    print(f"Loaded {len(_closes_cache)} symbols. Running {len(COMBINATIONS)} combos × {len(YEARS)} years...\n")

    all_results: list[tuple[int, int, dict[int, dict]]] = []
    for lookback, hold in COMBINATIONS:
        annual = run_combo(lookback, hold)
        all_results.append((lookback, hold, annual))
        print(f"  [{lookback}d/{hold}d] done")

    print()
    print_results(all_results)

"""
Carry combined P&L: funding income + directional price exposure, 2022-2025.

The arithmetic check in funding_arithmetic.py measured funding income only.
This script measures what you ACTUALLY earn by holding an unhedged short perp
when funding is positive (and an unhedged long when funding is negative).

The signal: 7-day rolling mean funding rate > 0  → hold short
            7-day rolling mean funding rate <= 0 → hold long (or flat — tested both)

P&L decomposition per position day:
  directional_pnl  = -(close[t] / close[t-1] - 1) × leverage   (for short)
  funding_income   = sum(funding_rates settled on day t) × leverage  (sign: positive when collected)
  daily_net        = directional_pnl + funding_income

Costs applied at each regime flip (open + close = round trip):
  taker_fee  = 2 × 4 bps × leverage
  slippage   = 2 × 2 bps × leverage
  round_trip = 2 × (4 + 2) bps × leverage = 12 bps × leverage

Sizing: vol-targeted. Same rule as WS5:
  leverage = target_risk_pct / realized_vol_20d
  capped: [0.1, 5.0]

This uses the actual klines and funding data — no synthetic fills.
Run after the funding store and klines featurestore are built.

Usage:
    $env:PYTHONPATH = '.'
    py -3 notes/carry_combined_pnl.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.data.featurestore import FeatureStore
from src.data.funding_rate import load_funding, FUNDING_STORE_DIR
from config.settings import SYMBOLS

# ── Parameters (locked before running, never changed to manufacture results) ──
REGIME_LOOKBACK_DAYS     = 7        # rolling window for mean funding regime
REGIME_THRESHOLD         = 0.0      # > threshold → short regime
TARGET_TRADE_RISK_PCT    = 0.01     # 1% daily risk target (WS5 default)
MAX_LEVERAGE             = 5.0
MIN_LEVERAGE             = 0.1
VOL_LOOKBACK_DAYS        = 20       # trailing daily return std for sizing
TAKER_FEE_BPS            = 4.0     # Binance standard taker, per side
SLIPPAGE_BPS             = 2.0     # WS5 model, per side
ROUND_TRIP_BPS           = 2 * (TAKER_FEE_BPS + SLIPPAGE_BPS)   # 12 bps
START                    = "2022-01-01"
END                      = "2025-12-31"
YEARS                    = [2022, 2023, 2024, 2025]

# Minimum trading days for a symbol to be included
MIN_DAYS = 200

store = FeatureStore()


def _daily_closes(symbol: str) -> pd.Series:
    """Load daily close prices for one symbol from the klines featurestore."""
    df = store.load(symbol, start="2021-07-01", end=END, timeframe="1d")
    return df["close"].rename(symbol)


def _daily_funding(symbol: str) -> pd.Series:
    """
    Daily funding income (absolute, not signed): sum of |rate| for each day,
    aggregated from the 3 settlements at 00:00, 08:00, 16:00 UTC.

    We return the SIGNED sum (positive = longs paying shorts = we earn if short).
    Sign: if rate > 0, being short earns it; if rate < 0, being short pays it.
    """
    try:
        fr = load_funding(symbol, "2021-07-01", END)
    except FileNotFoundError:
        return pd.Series(dtype=float)

    # Aggregate to daily signed sum
    daily = fr["last_funding_rate"].resample("1D").sum()
    return daily


def _size_leverage(daily_returns: pd.Series, t: pd.Timestamp) -> float:
    """Vol-targeted leverage at date t using trailing 20-day realized vol."""
    lookback = daily_returns.loc[:t].iloc[-VOL_LOOKBACK_DAYS:]
    if len(lookback) < 5:
        return MIN_LEVERAGE
    vol = float(lookback.std(ddof=0))
    if vol <= 0:
        return MAX_LEVERAGE
    lev = TARGET_TRADE_RISK_PCT / vol
    return float(np.clip(lev, MIN_LEVERAGE, MAX_LEVERAGE))


def simulate_symbol(symbol: str) -> pd.DataFrame | None:
    """
    Simulate unhedged funding carry for one symbol over START→END.

    Returns a daily DataFrame with columns:
        regime          (+1=short, -1=long)
        leverage        (vol-targeted)
        daily_ret       (raw underlying daily return)
        dir_pnl         (directional P&L: regime × daily_ret × leverage)
        funding_signed  (signed daily funding rate sum: positive = shorts earn)
        funding_pnl     (what we collect: regime × funding_signed × leverage,
                         because if regime=-1/long and rate<0, we earn |rate|)
        cost_pnl        (round-trip cost deducted on regime-flip days only)
        net_pnl         (dir_pnl + funding_pnl + cost_pnl)
        regime_flip     (True on days regime changed)
    """
    try:
        closes = _daily_closes(symbol)
    except Exception:
        return None

    funding_daily = _daily_funding(symbol)
    if funding_daily.empty:
        return None

    # Trim to study window; need warmup for regime lookback
    closes = closes.loc["2021-07-01":END]
    if len(closes) < MIN_DAYS + VOL_LOOKBACK_DAYS + REGIME_LOOKBACK_DAYS:
        return None

    daily_ret = closes.pct_change().dropna()

    # Build regime series: rolling REGIME_LOOKBACK_DAYS mean of daily funding sum
    # Align funding to same calendar index as closes
    funding_aligned = funding_daily.reindex(closes.index).fillna(0.0)
    rolling_mean = funding_aligned.rolling(f"{REGIME_LOOKBACK_DAYS}D", min_periods=1).mean()

    # +1 = short (longs paying us), -1 = long (shorts paying us), after lookback
    regime = rolling_mean.apply(lambda x: 1 if x > REGIME_THRESHOLD else -1)

    # Build result, clipped to study window
    idx = daily_ret.loc[START:END].index
    if len(idx) < MIN_DAYS:
        return None

    rows = []
    prev_regime = None

    for date in idx:
        reg = int(regime.loc[date])
        lev = _size_leverage(daily_ret, date)
        dr  = float(daily_ret.loc[date])
        fs  = float(funding_aligned.loc[date]) if date in funding_aligned.index else 0.0

        # Directional P&L: short = earn on down moves, lose on up moves
        dir_pnl = -reg * dr * lev   # reg=+1 short → earn -dr; reg=-1 long → earn dr

        # Funding P&L: if regime=short (+1) and rate>0, we earn fs×lev
        #              if regime=long  (-1) and rate<0, we earn |fs|×lev = -fs×lev
        # Unified: regime × funding_signed × leverage
        funding_pnl = reg * fs * lev

        # Cost: round trip applied on the day regime flips (entry new + exit old)
        flip = prev_regime is not None and reg != prev_regime
        cost_pnl = -ROUND_TRIP_BPS / 10_000.0 * lev if flip else 0.0

        rows.append({
            "date":           date,
            "regime":         reg,
            "leverage":       lev,
            "daily_ret":      dr,
            "dir_pnl":        dir_pnl,
            "funding_signed": fs,
            "funding_pnl":    funding_pnl,
            "cost_pnl":       cost_pnl,
            "net_pnl":        dir_pnl + funding_pnl + cost_pnl,
            "regime_flip":    flip,
        })
        prev_regime = reg

    return pd.DataFrame(rows).set_index("date")


def annual_breakdown(df: pd.DataFrame) -> dict[int, dict]:
    out = {}
    for yr in YEARS:
        sub = df[df.index.year == yr]
        if len(sub) < 20:
            continue
        n_flips    = int(sub["regime_flip"].sum())
        pct_short  = float((sub["regime"] == 1).mean())
        avg_lev    = float(sub["leverage"].mean())
        dir_total  = float(sub["dir_pnl"].sum())
        fund_total = float(sub["funding_pnl"].sum())
        cost_total = float(sub["cost_pnl"].sum())
        net_total  = float(sub["net_pnl"].sum())
        out[yr] = {
            "n_days":       len(sub),
            "n_flips":      n_flips,
            "pct_short":    pct_short,
            "avg_leverage": avg_lev,
            "dir_pnl":      dir_total,
            "funding_pnl":  fund_total,
            "cost_pnl":     cost_total,
            "net_pnl":      net_total,
        }
    return out


def run() -> None:
    print(f"Carry combined P&L simulation  |  {START} -> {END}")
    print(f"Parameters: regime_lookback={REGIME_LOOKBACK_DAYS}d, "
          f"vol_sizing={VOL_LOOKBACK_DAYS}d trailing, "
          f"max_lev={MAX_LEVERAGE}x, RT_cost={ROUND_TRIP_BPS}bps/flip")
    print()

    all_annual: dict[str, dict[int, dict]] = {}

    for symbol in SYMBOLS:
        if not (FUNDING_STORE_DIR / f"symbol={symbol}").exists():
            continue
        df = simulate_symbol(symbol)
        if df is None:
            print(f"  {symbol}: skipped (insufficient data)")
            continue
        all_annual[symbol] = annual_breakdown(df)

    # ── Per-symbol annual table ────────────────────────────────────────────────
    print("=" * 110)
    print(f"{'Symbol':<16} "
          + "  ".join(f"{'─── ' + str(y) + ' ───':^24}" for y in YEARS))
    header2 = f"{'':16} " + "  ".join(
        f"{'dir%':>6} {'fund%':>6} {'net%':>6} {'lev':>4}" for _ in YEARS
    )
    print(header2)
    print("-" * 110)

    universe_by_year: dict[int, list[float]] = {y: [] for y in YEARS}

    for symbol, annual in sorted(all_annual.items(), key=lambda kv: kv[0]):
        row = f"{symbol:<16}"
        for yr in YEARS:
            if yr not in annual:
                row += f"  {'n/a':>6} {'':>6} {'':>6} {'':>4}"
                continue
            a = annual[yr]
            dir_pct  = a["dir_pnl"]  * 100
            fund_pct = a["funding_pnl"] * 100
            net_pct  = a["net_pnl"]  * 100
            lev      = a["avg_leverage"]
            row += f"  {dir_pct:>+6.1f} {fund_pct:>+6.1f} {net_pct:>+6.1f} {lev:>4.1f}"
            universe_by_year[yr].append(net_pct)
        print(row)

    # ── Cross-symbol annual median ─────────────────────────────────────────────
    print("-" * 110)
    median_row = f"{'MEDIAN':<16}"
    for yr in YEARS:
        vals = universe_by_year[yr]
        if vals:
            med = float(np.median(vals))
            median_row += f"  {'':>6} {'':>6} {med:>+6.1f} {'':>4}"
        else:
            median_row += f"  {'':>6} {'':>6} {'n/a':>6} {'':>4}"
    print(median_row)

    # ── Year-by-year regime context ───────────────────────────────────────────
    print()
    print("=" * 110)
    print("REGIME + CONTEXT (median across symbols)")
    print(f"  {'Year':<6} {'Symbols':>8} {'Median net%':>12} {'Med dir%':>10} "
          f"{'Med fund%':>10} {'BTC price chg':>14}")

    # BTC directional as reference
    btc_df = simulate_symbol("BTCUSDT")
    btc_annual = annual_breakdown(btc_df) if btc_df is not None else {}

    # Also get raw BTC price change per year for context
    try:
        btc_closes = _daily_closes("BTCUSDT")
    except Exception:
        btc_closes = pd.Series(dtype=float)

    for yr in YEARS:
        n = len(universe_by_year[yr])
        net_vals = universe_by_year[yr]
        med_net = float(np.median(net_vals)) if net_vals else float("nan")

        # Reconstruct median dir and fund from per-symbol data
        dir_vals  = [all_annual[s][yr]["dir_pnl"] * 100 for s in all_annual if yr in all_annual[s]]
        fund_vals = [all_annual[s][yr]["funding_pnl"] * 100 for s in all_annual if yr in all_annual[s]]
        med_dir   = float(np.median(dir_vals))  if dir_vals  else float("nan")
        med_fund  = float(np.median(fund_vals)) if fund_vals else float("nan")

        # BTC raw price change (not levered, just direction reference)
        if not btc_closes.empty:
            yr_closes = btc_closes[btc_closes.index.year == yr]
            if len(yr_closes) >= 2:
                btc_chg = (yr_closes.iloc[-1] / yr_closes.iloc[0] - 1) * 100
                btc_str = f"{btc_chg:>+.1f}%"
            else:
                btc_str = "n/a"
        else:
            btc_str = "n/a"

        print(f"  {yr:<6} {n:>8} {med_net:>+11.1f}% {med_dir:>+9.1f}% "
              f"{med_fund:>+9.1f}% {btc_str:>14}")

    # ── BTC deep-dive ─────────────────────────────────────────────────────────
    print()
    print("=" * 110)
    print("BTC DEEP-DIVE (largest, most liquid — shows directional exposure risk most clearly)")
    if btc_df is not None:
        print(f"  {'Year':<6} {'Days':>5} {'Flips':>6} {'%Short':>7} "
              f"{'AvgLev':>7} {'Dir%':>8} {'Fund%':>8} {'Cost%':>7} {'Net%':>8}")
        for yr in YEARS:
            if yr not in btc_annual:
                continue
            a = btc_annual[yr]
            print(f"  {yr:<6} {a['n_days']:>5} {a['n_flips']:>6} "
                  f"{a['pct_short']:>7.1%} {a['avg_leverage']:>7.2f} "
                  f"{a['dir_pnl']*100:>+8.1f}% {a['funding_pnl']*100:>+8.1f}% "
                  f"{a['cost_pnl']*100:>+7.2f}% {a['net_pnl']*100:>+8.1f}%")

    # ── Verdict ───────────────────────────────────────────────────────────────
    print()
    print("=" * 110)
    print("VERDICT: Is unhedged short-perp carry viable without a spot hedge?")
    print()

    all_net_vals = [v for yr_vals in universe_by_year.values() for v in yr_vals]
    positive_years = {
        yr: float(np.median(v)) > 0
        for yr, v in universe_by_year.items()
        if v
    }
    n_pos = sum(positive_years.values())
    n_total = len(positive_years)

    print(f"  Universe-median net return is positive in {n_pos}/{n_total} calendar years.")
    for yr, pos in sorted(positive_years.items()):
        med = float(np.median(universe_by_year[yr]))
        tag = "positive" if pos else "NEGATIVE"
        print(f"    {yr}: {med:>+.1f}%  [{tag}]")

    print()
    if n_pos == n_total:
        print("  >> Net positive in ALL years — directional exposure does NOT consistently")
        print("     wipe out funding income at this leverage and regime filter.")
        print("     However: check per-year variance. If some years have large positive")
        print("     directional wins (bull market shorts blow up) that outweigh funding,")
        print("     the result is fragile and year-dependent, not structural.")
    elif n_pos >= n_total // 2:
        print("  >> Net positive in MOST years, but NEGATIVE in some.")
        print("     Unhedged carry is not reliably viable without the spot hedge.")
        print("     The directional exposure creates unacceptable year-dependent risk.")
        print("     Recommendation: carry strategy needs spot hedge infrastructure,")
        print("     OR restrict to symbols/periods where directional exposure is small.")
    else:
        print("  >> Net NEGATIVE in most or all years.")
        print("     The directional exposure from the unhedged perp completely dominates")
        print("     and wipes out the funding income. A spot hedge is NOT optional —")
        print("     it is the entire thesis. Cannot build this without spot data.")
    print("=" * 110)


if __name__ == "__main__":
    run()

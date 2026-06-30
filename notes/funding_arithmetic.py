"""
Step 1 arithmetic: characterize the funding-carry premium on raw data.

What this answers (from RESEARCH_DIRECTION_V2.md §8 and §9 Step 1):
  - Distribution of funding rate per symbol (mean, std, skew, pct positive)
  - Raw annualized premium from harvesting the "paid" side (no signal — pure carry)
  - Whether that premium survives REAL Binance costs with margin

Run after the funding store is built:
    python notes/funding_arithmetic.py

Binance real-cost structure (taker, no API-maker discount):
    Taker fee:   0.04% per side  → 0.08% round trip
    Slippage:    ~0.02% per side → 0.04% round trip  (from WS5 model: 2 bps)
    Total:       ~0.12% round trip per open+close

Funding carry does NOT require frequent rebalancing.  A static long-USDT-M position
(short perp, long spot/cash) turns over ~once per holding period, not 3x/day.
The relevant cost question is: does MEAN daily funding × 365 >> round-trip cost × rebalance_frequency?
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

# Allow running from the notes/ directory or repo root
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.data.funding_rate import load_funding, FUNDING_STORE_DIR
from config.settings import SYMBOLS

# ── Cost constants ────────────────────────────────────────────────────────────
TAKER_FEE_PCT = 0.04 / 100          # 0.04% per side (Binance standard)
SLIPPAGE_PCT  = 2 / 10_000          # 2 bps per side (WS5 model)
ROUND_TRIP_COST_PCT = 2 * (TAKER_FEE_PCT + SLIPPAGE_PCT)   # open + close

# Funding settles 3x/day → 3 × 365 = 1095 intervals/year
SETTLEMENTS_PER_YEAR = 3 * 365

# Study window
START = "2022-01-01"
END   = "2025-12-31"

# Minimum periods for a symbol to be included in cross-symbol summary
MIN_PERIODS = 100


def _load_symbol(symbol: str) -> pd.DataFrame | None:
    try:
        df = load_funding(symbol, START, END)
        if len(df) < MIN_PERIODS:
            return None
        return df
    except FileNotFoundError:
        return None


def characterize_symbol(symbol: str, df: pd.DataFrame) -> dict:
    r = df["last_funding_rate"].dropna()

    mean_rate   = float(r.mean())
    std_rate    = float(r.std())
    median_rate = float(r.median())
    skew        = float(r.skew())
    pct_pos     = float((r > 0).mean())
    pct_neg     = float((r < 0).mean())
    pct_strongly_pos = float((r >  0.0001).mean())   # > 1 bp (strongly long-pays)
    pct_strongly_neg = float((r < -0.0001).mean())   # < -1 bp (strongly short-pays)

    # --- Raw carry harvest ---
    # Strategy: collect whichever side is "paid" each period.
    # If funding > 0: short the perp (longs pay you)  → you earn +rate
    # If funding < 0: long the perp (shorts pay you)  → you earn +|rate|
    # This is the gross carry available BEFORE costs.
    # We assume we can always switch sides — this is the theoretical ceiling.
    gross_per_period = r.abs()
    mean_gross_per_period = float(gross_per_period.mean())

    # Annualize: 3 settlements/day × 365
    annual_gross_pct = mean_gross_per_period * SETTLEMENTS_PER_YEAR * 100

    # --- Directional-only carry (stay long-biased, collect when positive) ---
    # Simpler version: only collect when longs are paying (rate > 0), sit flat otherwise.
    # This answers: is the funding premium structurally positive (long-biased market)?
    long_biased_per_period = r.clip(lower=0)   # earn rate when positive, 0 otherwise
    annual_long_biased_pct = float(long_biased_per_period.mean()) * SETTLEMENTS_PER_YEAR * 100

    return {
        "symbol":                symbol,
        "n_periods":             len(r),
        "date_start":            str(df.index.min().date()),
        "date_end":              str(df.index.max().date()),
        "mean_rate":             mean_rate,
        "std_rate":              std_rate,
        "median_rate":           median_rate,
        "skew":                  skew,
        "pct_positive":          pct_pos,
        "pct_negative":          pct_neg,
        "pct_strongly_positive": pct_strongly_pos,
        "pct_strongly_negative": pct_strongly_neg,
        "annual_gross_carry_pct": annual_gross_pct,
        "annual_long_biased_carry_pct": annual_long_biased_pct,
    }


def print_report(rows: list[dict]) -> None:
    df = pd.DataFrame(rows).sort_values("annual_gross_carry_pct", ascending=False)

    # ── Per-symbol table ──────────────────────────────────────────────────────
    print("\n" + "=" * 90)
    print("FUNDING RATE CHARACTERIZATION  |  2022-01-01 -> 2025-12-31  |  Binance USDT-M")
    print("=" * 90)
    print(f"\n{'Symbol':<16} {'N':>5} {'Mean rate':>10} {'Std':>9} {'Skew':>6} "
          f"{'%Pos':>6} {'%StrongPos':>11} {'AnnGross%':>10} {'AnnLongBias%':>13}")
    print("-" * 90)
    for r in df.to_dict("records"):
        print(
            f"{r['symbol']:<16} {r['n_periods']:>5} "
            f"{r['mean_rate']:>10.6f} {r['std_rate']:>9.6f} {r['skew']:>6.2f} "
            f"{r['pct_positive']:>6.1%} {r['pct_strongly_positive']:>11.1%} "
            f"{r['annual_gross_carry_pct']:>10.2f}% {r['annual_long_biased_carry_pct']:>12.2f}%"
        )

    # ── Cross-symbol summary ──────────────────────────────────────────────────
    gross = df["annual_gross_carry_pct"]
    lb    = df["annual_long_biased_carry_pct"]
    print("\n" + "=" * 90)
    print("CROSS-SYMBOL SUMMARY (all available symbols)")
    print(f"  Gross carry (always harvest paid side):")
    print(f"    Median across symbols:  {gross.median():.2f}%/yr")
    print(f"    Mean across symbols:    {gross.mean():.2f}%/yr")
    print(f"    Min / Max:              {gross.min():.2f}% / {gross.max():.2f}%")
    print(f"  Long-biased carry (collect when positive, sit flat otherwise):")
    print(f"    Median across symbols:  {lb.median():.2f}%/yr")
    print(f"    Mean across symbols:    {lb.mean():.2f}%/yr")

    # ── Cost hurdle ───────────────────────────────────────────────────────────
    print("\n" + "=" * 90)
    print("REAL COST HURDLE ANALYSIS")
    print(f"  Round-trip cost (taker fee + slippage, open+close):")
    print(f"    Taker fee:   {TAKER_FEE_PCT*100:.2f}% per side  →  {TAKER_FEE_PCT*2*100:.2f}% round trip")
    print(f"    Slippage:    {SLIPPAGE_PCT*100:.2f}% per side  →  {SLIPPAGE_PCT*2*100:.2f}% round trip")
    print(f"    Total RT:    {ROUND_TRIP_COST_PCT*100:.4f}%")
    print()

    # Break-even: how many rebalances/year can the carry support?
    median_annual_gross = gross.median()
    max_rebalances = median_annual_gross / (ROUND_TRIP_COST_PCT * 100)
    print(f"  At median gross carry ({median_annual_gross:.2f}%/yr):")
    print(f"    Max rebalances/yr before costs eat all carry: {max_rebalances:.1f}")
    print(f"    → If rebalancing {SETTLEMENTS_PER_YEAR}x/day (switching every period): "
          f"costs = {ROUND_TRIP_COST_PCT*100 * SETTLEMENTS_PER_YEAR:.1f}%/yr  ← NOT viable")

    for holds in [1, 7, 30, 90, 365]:
        rebal_per_yr = 365 / holds
        cost_per_yr  = rebal_per_yr * ROUND_TRIP_COST_PCT * 100
        net          = median_annual_gross - cost_per_yr
        verdict      = "✓ POSITIVE" if net > 0 else "✗ negative"
        print(f"    Hold {holds:>3}d  →  {rebal_per_yr:>6.1f} rebal/yr  "
              f"cost={cost_per_yr:>6.2f}%/yr  net={net:>+7.2f}%/yr  {verdict}")

    # ── Persistence check ─────────────────────────────────────────────────────
    print("\n" + "=" * 90)
    print("REGIME PERSISTENCE  |  Is the premium stable across years?")
    print(f"  {'Symbol':<16} {'2022':>8} {'2023':>8} {'2024':>8} {'2025':>8}")
    print(f"  {'-'*16} {'(ann%)':>8} {'(ann%)':>8} {'(ann%)':>8} {'(ann%)':>8}")

    yearly_rows = []
    for r in df.to_dict("records"):
        sym = r["symbol"]
        row = {"symbol": sym}
        try:
            raw = load_funding(sym, "2022-01-01", "2025-12-31")
            for yr in [2022, 2023, 2024, 2025]:
                yr_data = raw[raw.index.year == yr]["last_funding_rate"].dropna()
                if len(yr_data) >= 30:
                    row[str(yr)] = yr_data.abs().mean() * SETTLEMENTS_PER_YEAR * 100
                else:
                    row[str(yr)] = float("nan")
        except Exception:
            for yr in [2022, 2023, 2024, 2025]:
                row[str(yr)] = float("nan")
        yearly_rows.append(row)

    for row in sorted(yearly_rows, key=lambda x: x.get("2022", 0) or 0, reverse=True):
        vals = []
        for yr in ["2022", "2023", "2024", "2025"]:
            v = row.get(yr, float("nan"))
            vals.append(f"{v:>7.2f}%" if not np.isnan(v) else "     n/a")
        print(f"  {row['symbol']:<16} {'  '.join(vals)}")

    print("\n" + "=" * 90)
    print("VERDICT")
    net_1yr_hold  = median_annual_gross - ROUND_TRIP_COST_PCT * 100  # 1 trade/yr
    net_30d_hold  = median_annual_gross - (365/30) * ROUND_TRIP_COST_PCT * 100
    net_7d_hold   = median_annual_gross - (365/7)  * ROUND_TRIP_COST_PCT * 100

    print(f"  Median gross carry across universe: {median_annual_gross:.2f}%/yr (before costs)")
    print(f"  Net carry at 1 rebalance/yr:  {net_1yr_hold:+.2f}%/yr")
    print(f"  Net carry at monthly rebal:   {net_30d_hold:+.2f}%/yr")
    print(f"  Net carry at weekly rebal:    {net_7d_hold:+.2f}%/yr")

    if net_30d_hold > 2.0:
        print("\n  >> PROCEED: premium clears costs with margin at monthly rebalancing.")
        print("     Name the counterparty: leveraged longs (retail) pay funding to carry holders.")
    elif net_1yr_hold > 2.0:
        print("\n  >> MARGINAL: premium clears costs only with very long holds (≥1yr rebal).")
        print("     Thin margin — decay and capacity risk likely to eat remaining edge.")
    else:
        print("\n  >> STOP: premium does not clear real costs with margin under any rebal schedule.")
        print("     Per standing rule: do not build a strategy on this.")
    print("=" * 90)


if __name__ == "__main__":
    print("Loading funding rate data from parquet store...")

    available = [
        s for s in SYMBOLS
        if (FUNDING_STORE_DIR / f"symbol={s}").exists()
    ]
    if not available:
        print("ERROR: no funding-rate parquet found. Run:")
        print("  python src/data/funding_rate.py")
        sys.exit(1)

    print(f"Found {len(available)} symbols in store.")

    rows = []
    for sym in available:
        df = _load_symbol(sym)
        if df is None:
            print(f"  {sym}: insufficient data, skipping")
            continue
        rows.append(characterize_symbol(sym, df))

    if not rows:
        print("No data loaded.")
        sys.exit(1)

    print_report(rows)

"""
BREAK-EVEN HURDLE CALCULATOR
============================================================
What it answers:
  At YOUR fee tier + leverage + trades-per-session, what is the
  minimum gross edge each trade must produce just to NOT bleed --
  and what edge do you need to actually hit your +10% session goal?

Why it matters:
  "Many trades over hours" maximizes turnover cost. This file shows
  you, in hard numbers, the bar your signals have to clear BEFORE
  any profit is yours. If the bar is higher than what your signals
  realistically produce, the plan is unsurvivable -- better to know
  in 5 minutes than after 100 paper trades.

How to run:
  python hurdle.py
  (No installs needed -- pure standard library.)

How to use:
  Edit the numbers in CONFIG below to match your real setup,
  then re-run. Everything else is computed for you.
============================================================
"""

# --------------------------- CONFIG ---------------------------
# Edit these to your real numbers, then re-run.

LEVERAGE                = 5      # your config: 5x

# Binance USDT-M perp fees, as % of NOTIONAL, per side.
# Standard (VIP 0): taker 0.05%, maker 0.02%. With BNB pay -> ~10% off.
TAKER_FEE_PCT           = 0.05
MAKER_FEE_PCT           = 0.02
ENTRY_IS_MAKER          = False  # market order in  = taker (False)
EXIT_IS_MAKER           = False  # stop/market out  = taker (False)

# Slippage you realistically lose each side, as % of notional.
# Top-30 liquid symbols ~0.01-0.03%. Be honest / slightly pessimistic.
SLIPPAGE_PCT_PER_SIDE   = 0.02

# Funding only bites if a position is open across a funding stamp
# (Binance funds every 8h = 480 min). Short holds rarely cross one.
AVG_HOLD_MINUTES        = 20     # your 20-min time stop, roughly
AVG_FUNDING_PCT_8H      = 0.01   # typical |funding| per 8h, % of notional

# Session plan
TRADES_PER_SESSION          = 40     # round-trips you expect to fire
TARGET_SESSION_RETURN_PCT   = 10     # your +10% goal, as % of equity/margin
# --------------------------------------------------------------


def pct(x):
    return f"{x:.4f}%"


def main():
    # ---- cost of ONE round-trip, expressed as % of NOTIONAL ----
    entry_fee = MAKER_FEE_PCT if ENTRY_IS_MAKER else TAKER_FEE_PCT
    exit_fee  = MAKER_FEE_PCT if EXIT_IS_MAKER  else TAKER_FEE_PCT
    fee_notional = entry_fee + exit_fee

    slip_notional = 2 * SLIPPAGE_PCT_PER_SIDE

    # expected funding: fraction of an 8h window the trade is open
    funding_fraction = AVG_HOLD_MINUTES / 480.0
    funding_notional = funding_fraction * AVG_FUNDING_PCT_8H

    cost_notional = fee_notional + slip_notional + funding_notional

    # A price move of X% changes notional by X%; gain on MARGIN = X% * leverage.
    # So break-even price move == cost_notional. Cost on margin == cost_notional * leverage.
    breakeven_price_move = cost_notional
    cost_margin = cost_notional * LEVERAGE

    # ---- session view (everything as % of MARGIN / equity) ----
    session_drag_margin = cost_margin * TRADES_PER_SESSION
    required_gross_margin = TARGET_SESSION_RETURN_PCT + session_drag_margin
    required_gross_per_trade_margin = required_gross_margin / TRADES_PER_SESSION
    required_price_move_per_trade = required_gross_per_trade_margin / LEVERAGE

    # ---- report ----
    line = "=" * 60
    print(line)
    print("BREAK-EVEN HURDLE  |  Binance USDT-M perps")
    print(line)
    print(f"  Leverage                 : {LEVERAGE}x")
    print(f"  Entry / Exit             : "
          f"{'maker' if ENTRY_IS_MAKER else 'taker'} / "
          f"{'maker' if EXIT_IS_MAKER else 'taker'}")
    print(f"  Avg hold                 : {AVG_HOLD_MINUTES} min")
    print(f"  Trades / session         : {TRADES_PER_SESSION}")
    print(f"  Session target           : +{TARGET_SESSION_RETURN_PCT}% of equity")
    print(line)
    print("COST OF ONE ROUND-TRIP (as % of notional)")
    print(f"  Fees                     : {pct(fee_notional)}")
    print(f"  Slippage                 : {pct(slip_notional)}")
    print(f"  Funding (expected)       : {pct(funding_notional)}")
    print(f"  -> total cost / trade    : {pct(cost_notional)}  of notional")
    print(line)
    print("WHAT THAT MEANS")
    print(f"  Break-even price move    : {pct(breakeven_price_move)}  "
          f"(price must move this far just to cover one trade)")
    print(f"  Cost / trade on margin   : {pct(cost_margin)}  of your equity")
    print(line)
    print("SESSION MATH (as % of equity)")
    print(f"  Total drag, {TRADES_PER_SESSION} trades  : {pct(session_drag_margin)}")
    print(f"  Gross needed for +{TARGET_SESSION_RETURN_PCT}%   : {pct(required_gross_margin)}  "
          f"(target + drag)")
    print(f"  => gross / trade needed  : {pct(required_gross_per_trade_margin)} on margin")
    print(f"  => avg winning move      : {pct(required_price_move_per_trade)} price, EVERY trade")
    print(line)

    # ---- plain-English verdict ----
    drag_share = session_drag_margin / required_gross_margin * 100
    print("VERDICT")
    print(f"  Of every dollar of gross profit, ~{drag_share:.0f}% is eaten")
    print(f"  by costs before it reaches your +{TARGET_SESSION_RETURN_PCT}% target.")
    if required_price_move_per_trade > 0.5:
        print(f"  Each trade must catch a ~{required_price_move_per_trade:.2f}% price move")
        print("  ON AVERAGE, net of losers. That is a demanding edge at this")
        print("  turnover -- consider fewer, higher-conviction trades, or maker entries.")
    else:
        print(f"  Each trade needs ~{required_price_move_per_trade:.2f}% avg net move.")
        print("  Plausible IF your signals genuinely produce that edge after losers.")
    print("  Reminder: this is the hurdle, not a promise. Only a backtest")
    print("  showing profit factor > 1.5 over 100+ trades tells you it's real.")
    print(line)


if __name__ == "__main__":
    main()

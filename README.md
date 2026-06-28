# Quant Bot — Build Spec & Roadmap

A Binance USDT-M perpetual futures **paper** trading system.
Built slowly, in phases. Each phase is a self-contained task you can hand
to a coding agent (Claude Code / Codex). Build them **in order** — do not
skip ahead, because each phase validates an assumption the next one needs.

---

## The honest north star (read this first)

We are **not** chasing "+10% today." That's a market outcome we don't control.
We are building a system and then **measuring the distribution of its results.**

The only number that tells us the system is real:
> **Profit factor > 1.5 across 100+ closed paper trades.**

Until that holds, nothing goes live. Ever. No exceptions.

Costs are the enemy. Every phase must respect the break-even hurdle
(see `tools/hurdle.py`). High turnover bleeds; the calculator proves it.

---

## Tech decisions (already made — don't re-litigate)

- **Data source:** `data.binance.vision` (free, no API key), `futures/um`.
- **Base resolution:** 1-minute klines. Resample up; never store ticks.
- **Storage:** Parquet + zstd, partitioned `symbol/year`. Target ~1–2 GB total.
- **Research engine:** `vectorbt` (open-source) — fast "does this signal have edge?" triage.
- **Paper engine:** `freqtrade` dry-run — correct fees/funding/PnL accounting, live prices, no money.
- **No LLM in the trading decision loop.** Signals stay deterministic math.
- **Hosting:** local for all research + early paper. Railway only in Phase 5,
  when the paper loop needs to run 24/7.

---

## Project layout (agent: create this as you go)

```
quant-bot/
├── README.md              <- this file (the spec)
├── requirements.txt
├── tools/
│   └── hurdle.py          <- DONE. break-even calculator. runs today.
├── config/
│   └── settings.py        <- symbols, leverage, risk params (Phase 1)
├── src/
│   ├── data/              <- downloader + parquet feature store (Phase 1)
│   ├── signals/           <- the 9 signal engines (Phase 2)
│   ├── backtest/          <- session-distribution harness (Phase 3)
│   └── paper/             <- freqtrade strategy + dry-run wiring (Phase 4)
├── data/                  <- (gitignored) downloaded parquet lives here
└── notes/
```

---

## ROADMAP

### Phase 0 — Break-even reality check  ✅ DONE
`tools/hurdle.py`. Run it, tune `TRADES_PER_SESSION` until the drag is sane.
**Decision gate:** pick a trades/session target where costs eat < ~40% of gross.

### Phase 1 — Data layer
Goal: 5 years of clean 1m klines for the top-30 USDT-M symbols, on disk as Parquet.
Tasks for the agent:
1. `config/settings.py` — list of symbols, date range, leverage, SL/TP/time-stop.
2. `src/data/download.py` — use `binance_historical_data` (`asset_class="um"`,
   `data_type="klines"`, `data_frequency="1m"`). Also pull `metrics` (funding + OI).
3. `src/data/featurestore.py` — load raw → keep OHLCV + quote_volume +
   taker_buy_base/quote → write Parquet zstd partitioned by symbol/year.
   Provide a `load(symbol, start, end, timeframe)` helper that resamples.
**Decision gate:** can load any symbol/timeframe into a DataFrame in < 1 second.

### Phase 2 — Port the 9 signal engines
Goal: each signal is a pure function `f(df) -> entries/exits` (deterministic).
Tasks:
1. One file per engine in `src/signals/`. Pure, testable, no I/O.
2. A combiner that merges the 9 into a single long/short signal with the
   confidence scaling already in the design.
**Decision gate:** same input always gives same output (determinism test passes).

### Phase 3 — Backtest the distribution (NOT a single P&L)
Goal: run the signals over 5 years in `vectorbt`, output the SHAPE of results.
Tasks:
1. `src/backtest/run.py` — apply fees (from hurdle.py), 5x, SL/TP/time-stop.
2. Report: profit factor, win rate, expectancy, max drawdown,
   **distribution of session returns**, and P(a session hits +10%).
**Decision gate:** profit factor > 1.5 on out-of-sample data, or go back to Phase 2.

### Phase 4 — Paper engine (freqtrade dry-run)
Goal: signals run live against real Binance prices, paper money, correct accounting.
Tasks:
1. Port the combined signal into a freqtrade `IStrategy` class.
2. Configure dry-run with the validated risk params.
3. Log every trade so we can recompute profit factor on REAL paper fills.
**Decision gate:** 100+ paper trades, profit factor still > 1.5.

### Phase 5 — Always-on (Railway)
Only now. Deploy the dry-run loop to Railway so it runs for days unattended,
plus a small dashboard. Same code as Phase 4 — no rewrite.

---

## How to drive this with a coding agent

Point the agent at this repo and give it ONE phase at a time. Example prompt:

> "Read README.md. Implement **Phase 1** only. Create the files listed under
>  Phase 1, keep everything deterministic, write a quick test that proves the
>  Decision gate, and stop. Do not start Phase 2."

Review the output, run the decision-gate test, then unlock the next phase.
Going one phase at a time is how someone who 'isn't good with code' stays
in control: you're steering by reading outputs, not writing lines.

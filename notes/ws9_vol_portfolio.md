# WS9 — Vol-targeted portfolio paper trading

> Standalone spec. Implement only what's in this file.
> Depends on: WS1-WS5 done and verified, WS4 point-in-time universe operational.
> This is NOT a backtest screen. It is a paper-trading deployment.
> No new data required. No unresolved research question blocks this.

---

## What this is and what it is not

**Is:** a continuously-running system that sizes positions across the real WS4
point-in-time universe using only vol-targeted sizing — no directional signal.
The system places and tracks paper orders, logs every decision, and alerts on
unexpected silence. The question being answered is operational: does the system
run reliably without intervention, and does sizing track realized vol the way
WS5 predicts?

**Is not:** a backtest (WS5 is already a backtest harness — this is a
live-clock system running in real time). Not a test of signal edge. Not a
funding carry trade. Not any continuation of WS6/WS7/WS8 signals.

---

## Must / must-not (from RESEARCH_DIRECTION_V2.md §9b — required, not advisory)

**Must:**
- Vol-targeted sizing only, across the real WS4 point-in-time universe.
- Log every trade decision: exact sizing inputs at decision time, intended vs.
  actual paper fill price and timestamp, latency from decision to order
  placement, any error or retry. This is the only way to eventually do a
  backtest-vs-live reconciliation study.
- Real exchange fees wired into this execution path — verify explicitly, do not
  assume it carries over from whatever script fees were first added to.
- A dead-man's switch / alerting mechanism: the system must flag if logging or
  trading stops unexpectedly. A silent multi-hour outage that goes unnoticed is
  a bigger failure than a red week.

**Must not:**
- Use any directional signal (WS6/WS7/WS8 are confirmed null — do not carry
  them forward).
- Touch real capital in any form.
- Use the unhedged carry trade (confirmed to lose 2/4 years — disproven, closed
  direction).
- Suppress or skip alerting because it seems inconvenient to wire in first.

---

## Pre-flight checklist (all items must be checked before switching on)

1. **Fee confirmation:** grep or read `src/execution/` and confirm exchange fees
   (taker ~4 bps/side on Binance) are applied in the code path this system
   calls. Slippage-only would make the system ~3.5x optimistic on costs. Note
   the exact line where fees are charged in the run log.
2. **Dead-man's switch:** a working alert mechanism (e.g. email/webhook on
   silence or exception) must be confirmed functional before first run.
3. **WS4 universe as-of:** confirm `PointInTimeUniverse.as_of(today)` returns
   the correct symbol set for today's date; log the symbol list on each
   rebalance.
4. **Paper-only guard:** confirm no live order submission path exists or is
   reachable; a comment or explicit assertion in the code is sufficient but must
   be present.

---

## Goal

Run a vol-targeted portfolio in paper mode, continuously and without manual
intervention, for at least three weeks. Demonstrate that:

1. The system operates reliably (no silent outages, alerting works).
2. Realized position sizing tracks the vol-targeted sizes predicted by WS5's
   model.
3. Per-trade logs are complete enough to support a future backtest-vs-live
   reconciliation study.

Not the goal: show positive P&L. Three weeks is too short to judge magnitude.

---

## Build

### Layer 1 — Scheduler / main loop (`src/paper_trading/scheduler.py`)

A clock-driven loop that fires at each rebalance interval (start: 1h bars,
same cadence as the existing execution model). On each tick:
1. Get today's point-in-time universe via `WS4.PointInTimeUniverse.as_of(now)`.
2. Load recent OHLCV bars sufficient for the vol estimate (same window as WS5's
   vol targeting — document the exact look-back used).
3. Call the sizing layer (Layer 2) for each symbol.
4. Record decisions and paper fills (Layer 3).
5. If the dead-man's heartbeat is overdue, fire alert before anything else.

No directional signal enters this loop. Sizing is the only logic.

**Error policy within a tick — decided here, not at runtime:**

| Failure type | Action |
|---|---|
| Transient (timeout, HTTP 5xx, network drop) | Retry with exponential backoff: 2s → 4s → 8s, max 3 attempts within the same tick |
| Retries exhausted | Skip the tick: write a full log row with `actual_fill_price = NULL` and `error` populated; write the heartbeat (system is alive); continue to next tick |
| Unrecoverable (DB write failure, assertion error, corrupted state) | Log the error, send alert immediately via Layer 4, do NOT write heartbeat, halt — manual restart required |

Rationale for skip-not-halt on transient failures: halting requires manual
restart, which turns every brief connectivity blip into a manual intervention
event and makes the dead-man's switch fire on recoverable errors. Skip-and-log
means the system self-heals on transient issues and the gaps are visible in the
trade log without action needed. The heartbeat is only skipped for
unrecoverables, so the dead-man's switch catches extended outages (e.g. a retry
loop that keeps failing every tick for two hours) without false-firing on a
single bad tick.

### Layer 2 — Sizing (`src/paper_trading/sizer.py`)

Thin wrapper around WS5's existing vol-targeted sizing. Inputs: symbol, recent
OHLCV, target vol. Output: target notional position size and direction (flat if
vol estimate is unreliable). No new sizing logic — reuse `src/execution/`
directly.

Document the exact parameter values used (target annual vol, max leverage cap,
lookback window) in this file. These are locked before the first run and do not
change to manufacture better-looking results.

### Layer 3 — Paper fill and trade logger (`src/paper_trading/logger.py`)

On every sizing decision, write one record to a SQLite log (separate from
WS1's research log — this is operational data, not experiment data) containing:

| Field | Content |
|---|---|
| `timestamp_decision` | UTC timestamp when the sizing decision was made |
| `timestamp_fill` | UTC timestamp when the paper fill was recorded |
| `symbol` | Symbol |
| `vol_estimate` | Realized vol estimate used for sizing |
| `target_size_notional` | Intended notional from vol-targeting |
| `intended_fill_price` | Mid/close price at decision time |
| `actual_fill_price` | Paper fill price (mid + slippage model) |
| `slippage_bps` | Applied slippage in bps |
| `fees_bps` | Applied exchange fees in bps |
| `latency_ms` | Wall-clock ms from decision to fill record |
| `error` | Any error or retry message, or NULL |
| `universe_snapshot` | JSON list of all symbols in universe at this tick |

Every row is appended, never updated. Schema must be versioned (a `schema_version`
column) so future changes are detectable.

### Layer 4 — Dead-man's switch (`src/paper_trading/heartbeat.py`)

A separate thread (not process — simpler, no IPC needed for this scale) that:
- Wakes every `2 × rebalance_interval` seconds and checks whether the main loop
  wrote a heartbeat file within that window.
- Fires an alert if no heartbeat is found, or if any uncaught exception escapes
  the main loop's top-level handler.

**Alerting mechanism: Gmail SMTP to vinaylahoti0@gmail.com via Python's
`smtplib`.** This works unattended on Windows without any paid service. Enable
an App Password on the Gmail account (not the account password — Google requires
this for SMTP from scripts). Store the credentials in a `.env` file, never in
source. The email subject line must include the system name and UTC timestamp so
it's unambiguous in a notification preview without opening it.

Confirmation procedure before first run: manually kill the heartbeat write (e.g.
comment it out for one interval), confirm an alert email arrives at the Gmail
address, then restore. This step is not optional — the gate does not pass
without a confirmed receipt.

### Layer 5 — Daily reconciliation report

A script (`src/paper_trading/report.py`) that reads the trade log and produces
a daily summary: number of symbols traded, average realized vol vs. targeted
vol, paper P&L by symbol, fill-vs-intended slippage distribution. This is not
a dashboard — a plain text or CSV output that gets logged to a file is
sufficient.

---

## Decision gate

The system passes this gate when ALL of the following are true:

1. **Pre-flight checklist** (all four items above) confirmed and documented.
2. **Dead-man's switch** demonstrated: manually block a heartbeat, confirm alert
   fires within the expected window.
3. **First 48-hour run** completes: no manual intervention, no silent gaps in
   the trade log, universe snapshot logged on every tick.
4. **Sizing sanity check:** for exactly 5 symbols (chosen to cover edge cases —
   see below), run both code paths on identical historical OHLCV inputs and diff
   the outputs. Tolerance is `1e-9` relative error — both paths are deterministic
   given the same inputs, so any larger gap is a code-path divergence, not
   floating-point noise. All 5 must pass; 4/5 does not count.

   The 5 symbols to use:
   - One with high realized vol (stress-tests the leverage cap).
   - One with low realized vol (tests the minimum-size floor if any).
   - One near the leverage cap on the test date.
   - One that was in the WS4 universe on the chosen historical date but is NOT
     in today's live universe (tests that the as-of lookup is being used in the
     live path, not today's membership).
   - One normal mid-vol case.

   Test procedure:
   ```python
   ohlcv = load_bars(symbol, start=..., end=...)  # same slice for both paths
   live_size = sizer.compute_target_size(symbol, ohlcv, target_vol=TARGET_VOL)
   ws5_size  = ws5_reference_size(symbol, ohlcv, target_vol=TARGET_VOL)
   rel_err   = abs(live_size - ws5_size) / max(abs(ws5_size), 1e-8)
   assert rel_err < 1e-9, f"{symbol}: live={live_size}, ws5={ws5_size}, rel_err={rel_err}"
   ```

   If any assertion fails, stop — this is a code-path divergence, not a tuning
   problem. Fix it before any paper-trading data is collected.
5. **Fee confirmation logged:** the run log entry from pre-flight item 1 is
   present.

A system that runs but doesn't log everything specified in Layer 3 has not
passed this gate. A system that passes the gate but has no alerting wired up
has not passed this gate.

---

## Success criteria (weeks 1–3)

These are not pass/fail gates for the system — they are the diagnostic
questions the paper-trading period is designed to answer:

- Did the system run continuously without manual restart? (Yes/No — the most
  important single question.)
- Did realized vol-targeting match WS5 predictions? (Compare logged
  `vol_estimate` distributions to backtest estimates on the same dates.)
- What does the fill-vs-intended slippage distribution look like? Is WS5's
  slippage model (2 bps/side) still optimistic once tested against real
  mid-prices?
- Were there any symbols where the universe-as-of lookup differed from what
  naive "current universe" would have returned? (Survivorship check.)

A red paper P&L week with all systems operational is success. A green week with
a two-hour silent gap that went unnoticed is a failure.

---

## Stop condition

Build Layers 1–4 only. Show: the pre-flight checklist items confirmed (not
claimed — show the actual code lines), the dead-man's switch test result, and
the first 48-hour run log summary. Do not extend scope to a dashboard,
notification broker, or order management system. The decision about Track B
(carry hedge) is made independently and does not depend on WS9's outcome.

If the sizing sanity check (gate item 4) fails to reconcile with WS5's backtest
output, stop — this is a code-path divergence bug, not a tuning problem, and it
must be fixed before any paper-trading data can be trusted.

---

## What comes after (not in scope here)

- **Track B** (delta-neutral funding carry) is a parallel, independent effort —
  see RESEARCH_DIRECTION_V2.md §9. It does not depend on WS9 and is not blocked
  by it.
- **Backtest-vs-live reconciliation** becomes possible once ~30 days of trade
  logs exist with full intended/actual fill data. That study is the payoff for
  the logging discipline specified here.
- **Adding a directional signal** to this system is only considered after both:
  (a) Track B resolves with a clear CONTINUE or STOP verdict, and (b) a signal
  that survives the screening harness has been found. There are currently zero
  surviving signals. Do not skip ahead.

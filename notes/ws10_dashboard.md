# WS10 — Paper trading dashboard

> Standalone spec. Implement only what's in this file.
> Depends on: WS9 (paper trading system) running and proven on Railway.
> Build this AFTER the 48-hour run completes — not during. Adding new code
> mid-run muddies whether any issue found is a real operational finding or a
> side effect of a change just made.

---

## Design philosophy (read first)

This is not a trading-platform showcase. It's the one screen checked twice a
day for ten seconds to answer: **is it alive, is it doing what I expect, is
anything red.** Resist charts-for-the-sake-of-charts and excess KPIs. One
chart (equity curve) earns its place; everything else is a number or a plain
table. The system's own discipline (no signal, no prediction, just sizing)
should be mirrored in the dashboard's restraint.

A single early week of paper P&L means almost nothing statistically (per
WS9's own success criteria — the bar was "ran reliably," not "made money").
The dashboard must not let a green or red number feel more meaningful than it
is — show context (days running, trade count) alongside any P&L figure, never
P&L alone.

## What it shows, in priority order

1. **Status line, top of screen, unmissable.** Last heartbeat timestamp,
   computed "alive" / "STALE" state, in an obvious color. This is checked
   first every single time — no scrolling, no interpretation required.
2. **Portfolio value: current vs. starting, in $ and %.** Directly below it,
   in smaller text: days running, total trades, so the number always carries
   its own context rather than standing alone.
3. **Equity curve** — one line, paper portfolio value over time. The only
   chart in the dashboard. Annotate it with vertical markers for any
   portfolio-size change (see the sizing control below) so a discontinuity is
   self-explained, not mysterious.
4. **Current positions table** — symbol, size, leverage, unrealized P&L. Plain
   table, sortable by column, no decoration.
5. **Recent events feed** — skips, errors, restarts, heartbeat alerts, in
   plain English generated from the structured log fields already being
   written (e.g. "Skipped TACUSDT: 55% move flagged as suspicious" instead of
   a raw traceback). Last ~50 events, newest first.
6. **Portfolio size control** (see below for the constraint).

## The portfolio-size control — guardrailed, not free-text

`PAPER_PORTFOLIO_USD` is currently a locked constant for a deliberate reason:
unguarded changes mid-run break the ability to cleanly compare performance
before/after, the same trap as loosening a signal threshold to manufacture a
better number.

**Required behavior:**
- The dashboard MAY allow changing this value.
- Every change is timestamped, logged as a real event into the same
  per-trade/event log WS9 already writes (not a separate untracked field),
  and appears in the Recent Events feed.
- The equity curve gets a vertical marker at the moment of any change.
- The dashboard should display the change as what it is — "portfolio size
  changed from $X to $Y on [date]" — never silently absorbed into the curve.

Do not implement free-text editing with no record. The control's value is
flexibility for the human; the requirement is that no change is ever silent.

## Build

- **Read-only against the existing trade log/heartbeat files** (read from the
  same SQLite DB and heartbeat file WS9 already writes to the Railway Volume)
  — do not duplicate data or build a second source of truth.
- **The only WRITE path the dashboard needs is the portfolio-size change
  event** described above — append-only, same DB.
- Simple web frontend — a lightweight Flask/FastAPI read endpoint plus a
  single HTML page is sufficient; this does not need a heavy frontend
  framework for something this restrained in scope.
- Deploy as a small separate Railway service (or a route on the existing
  service, agent's call) reading the same attached Volume — do not require a
  second copy of the trade data.
- No authentication is in scope for v1 unless the agent flags a real exposure
  concern (e.g. if Railway gives this a public URL by default) — if so, stop
  and ask before deciding on auth, don't silently add or skip it.

## Decision gate

- Dashboard loads and shows real data from the actual running WS9 system —
  not mock/sample data.
- Status line correctly reflects "STALE" if the heartbeat file's age exceeds
  the same threshold WS9's own dead-man's switch uses (don't reimplement the
  threshold logic — reuse or import the same constant).
- A test portfolio-size change is made through the dashboard and confirmed to
  appear as a logged event AND as a marker on the equity curve — not silently.
- Confirm what happens if the trade log is empty or the system has just
  started (e.g. day one) — the dashboard should degrade gracefully, not error.

## Stop condition

Build this only. Don't add features beyond what's listed — no extra charts,
no alerting duplication (WS9 already alerts via Resend), no historical
backtest comparison overlay (that's a real, separate, future idea — not v1).
Show the decision-gate results, then stop.

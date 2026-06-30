"""
WS10 paper-trading dashboard — read-only Flask app.

Reads from the same SQLite DB and heartbeat file that the WS9 scheduler
writes to the Railway Volume. The only write action is logging a portfolio-
size change event (append-only, same DB).

Start with:
    python -m src.paper_trading.dashboard

Environment variables (same set as the scheduler):
    PAPER_TRADING_DATA_DIR  — path to the Railway Volume mount (default: data/)
    DASHBOARD_PORT          — port to listen on (default: 8080)
"""

from __future__ import annotations

import datetime
import json
import os
import sqlite3
import time
from pathlib import Path

from flask import Flask, jsonify, render_template_string, request

from src.paper_trading.config import (
    HEARTBEAT_PATH,
    HEARTBEAT_TIMEOUT_MULTIPLIER,
    LOG_DB_PATH,
    PAPER_PORTFOLIO_USD,
    REBALANCE_INTERVAL_SECONDS,
)

app = Flask(__name__)

_STALE_THRESHOLD_SECONDS = REBALANCE_INTERVAL_SECONDS * HEARTBEAT_TIMEOUT_MULTIPLIER

# ── DB helpers ────────────────────────────────────────────────────────────────

_EVENTS_DDL = """
CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TEXT    NOT NULL,
    event_type  TEXT    NOT NULL,
    detail      TEXT    NOT NULL
)
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(LOG_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_schema() -> None:
    LOG_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        # trades table may already exist from WS9; events table is new for WS10
        conn.execute(_EVENTS_DDL)


def _now_utc() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


# ── Heartbeat ─────────────────────────────────────────────────────────────────

def _heartbeat_status() -> dict:
    try:
        written_at = float(HEARTBEAT_PATH.read_text().strip())
        age_s = time.time() - written_at
        ts = datetime.datetime.fromtimestamp(written_at, tz=datetime.timezone.utc)
        return {
            "last_heartbeat": ts.isoformat(),
            "age_seconds": round(age_s, 1),
            "alive": age_s <= _STALE_THRESHOLD_SECONDS,
        }
    except (FileNotFoundError, ValueError):
        return {
            "last_heartbeat": None,
            "age_seconds": None,
            "alive": False,
        }


# ── Trade data ────────────────────────────────────────────────────────────────

def _portfolio_size_at(ts_iso: str) -> float:
    """Return PAPER_PORTFOLIO_USD effective at ts_iso, accounting for logged changes."""
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT detail FROM events
            WHERE event_type = 'portfolio_size_change' AND timestamp <= ?
            ORDER BY timestamp DESC LIMIT 1
            """,
            (ts_iso,),
        ).fetchone()
    if row:
        detail = json.loads(row["detail"])
        return float(detail["new_value"])
    return PAPER_PORTFOLIO_USD


def _current_portfolio_size() -> float:
    return _portfolio_size_at(_now_utc())


def _equity_curve() -> list[dict]:
    """
    Per-tick portfolio value, reconstructed from the trade log.

    Each tick is the latest timestamp_decision in a group of rows that share
    a universe_snapshot. Portfolio value = sum of target_size_notional for
    that tick (null-fills add 0). We use the configured PAPER_PORTFOLIO_USD
    at the time of each tick (accounting for any portfolio-size change events).
    """
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT timestamp_decision,
                   SUM(COALESCE(target_size_notional, 0)) AS tick_notional
            FROM trades
            GROUP BY timestamp_decision
            ORDER BY timestamp_decision ASC
            """
        ).fetchall()

    # Fetch all portfolio-size change events once
    with _connect() as conn:
        size_events = conn.execute(
            """
            SELECT timestamp, detail FROM events
            WHERE event_type = 'portfolio_size_change'
            ORDER BY timestamp ASC
            """
        ).fetchall()

    size_timeline = [
        (row["timestamp"], json.loads(row["detail"])["new_value"])
        for row in size_events
    ]

    def portfolio_size_at(ts: str) -> float:
        val = PAPER_PORTFOLIO_USD
        for evt_ts, new_val in size_timeline:
            if evt_ts <= ts:
                val = new_val
            else:
                break
        return val

    curve = []
    for row in rows:
        ts = row["timestamp_decision"]
        pf_size = portfolio_size_at(ts)
        tick_notional = row["tick_notional"] or 0.0
        # P&L = notional deployed - portfolio size (negative = under-deployed,
        # which happens when symbols are skipped). We track total deployed
        # notional as the portfolio value proxy.
        curve.append({"timestamp": ts, "value": round(tick_notional, 2), "portfolio_size": round(pf_size, 2)})

    return curve


def _current_positions() -> list[dict]:
    """Latest sizing decision for each symbol in the most recent tick."""
    with _connect() as conn:
        # Most recent timestamp_decision
        latest = conn.execute(
            "SELECT MAX(timestamp_decision) as ts FROM trades"
        ).fetchone()
        if not latest or not latest["ts"]:
            return []
        latest_ts = latest["ts"]

        rows = conn.execute(
            """
            SELECT symbol, vol_estimate, target_size_notional,
                   actual_fill_price, error, latency_ms
            FROM trades
            WHERE timestamp_decision = ?
            ORDER BY symbol
            """,
            (latest_ts,),
        ).fetchall()

    pf_size = _current_portfolio_size()
    positions = []
    for row in rows:
        notional = row["target_size_notional"]
        price = row["actual_fill_price"]
        leverage = None
        if notional and pf_size:
            leverage = round(notional / (pf_size / max(1, len(rows))), 2)
        positions.append({
            "symbol": row["symbol"],
            "vol_estimate": round(row["vol_estimate"], 6) if row["vol_estimate"] else None,
            "target_size_notional": round(notional, 2) if notional else None,
            "actual_fill_price": round(price, 6) if price else None,
            "leverage": leverage,
            "error": row["error"],
            "latency_ms": round(row["latency_ms"], 1) if row["latency_ms"] else None,
        })
    return positions


def _recent_events(limit: int = 50) -> list[dict]:
    """
    Combined feed from trades (errors/skips) and events (portfolio changes),
    newest first, in plain English.
    """
    with _connect() as conn:
        trade_rows = conn.execute(
            """
            SELECT timestamp_decision as ts, symbol, error
            FROM trades
            WHERE error IS NOT NULL
            ORDER BY id DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()

        event_rows = conn.execute(
            """
            SELECT timestamp as ts, event_type, detail
            FROM events
            ORDER BY id DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()

    feed = []
    for row in trade_rows:
        feed.append({
            "timestamp": row["ts"],
            "message": f"Skipped {row['symbol']}: {row['error']}",
            "kind": "skip",
        })
    for row in event_rows:
        detail = json.loads(row["detail"])
        if row["event_type"] == "portfolio_size_change":
            msg = (
                f"Portfolio size changed from ${detail['old_value']:,.0f} "
                f"to ${detail['new_value']:,.0f}"
            )
        else:
            msg = f"{row['event_type']}: {row['detail']}"
        feed.append({
            "timestamp": row["ts"],
            "message": msg,
            "kind": row["event_type"],
        })

    feed.sort(key=lambda x: x["timestamp"], reverse=True)
    return feed[:limit]


def _summary_stats() -> dict:
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) as total_trades,
                   MIN(timestamp_decision) as first_ts,
                   MAX(timestamp_decision) as last_ts
            FROM trades
            """
        ).fetchone()

    total = row["total_trades"] or 0
    first_ts = row["first_ts"]
    last_ts = row["last_ts"]
    days_running = None
    if first_ts and last_ts:
        try:
            t0 = datetime.datetime.fromisoformat(first_ts)
            t1 = datetime.datetime.fromisoformat(last_ts)
            days_running = round((t1 - t0).total_seconds() / 86400, 1)
        except ValueError:
            pass

    return {
        "total_trades": total,
        "first_tick": first_ts,
        "last_tick": last_ts,
        "days_running": days_running,
    }


# ── API routes ────────────────────────────────────────────────────────────────

@app.get("/api/status")
def api_status():
    stats = _summary_stats()
    hb = _heartbeat_status()
    curve = _equity_curve()
    current_pf = _current_portfolio_size()
    latest_value = curve[-1]["value"] if curve else current_pf
    pnl_usd = round(latest_value - current_pf, 2)
    pnl_pct = round((latest_value / current_pf - 1) * 100, 3) if current_pf else 0.0
    return jsonify({
        "heartbeat": hb,
        "portfolio": {
            "current_size_usd": current_pf,
            "latest_deployed_notional": latest_value,
            "pnl_usd": pnl_usd,
            "pnl_pct": pnl_pct,
        },
        "stats": stats,
        "stale_threshold_seconds": _STALE_THRESHOLD_SECONDS,
    })


@app.get("/api/equity")
def api_equity():
    return jsonify(_equity_curve())


@app.get("/api/positions")
def api_positions():
    return jsonify(_current_positions())


@app.get("/api/events")
def api_events():
    return jsonify(_recent_events())


@app.post("/api/portfolio_size")
def api_set_portfolio_size():
    data = request.get_json(force=True)
    try:
        new_value = float(data["value"])
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "missing or invalid 'value'"}), 400
    if new_value <= 0:
        return jsonify({"error": "value must be > 0"}), 400

    old_value = _current_portfolio_size()
    detail = json.dumps({"old_value": old_value, "new_value": new_value})
    with _connect() as conn:
        conn.execute(
            "INSERT INTO events (timestamp, event_type, detail) VALUES (?,?,?)",
            (_now_utc(), "portfolio_size_change", detail),
        )

    return jsonify({"ok": True, "old_value": old_value, "new_value": new_value})


@app.get("/")
def index():
    return render_template_string(_HTML)


# ── HTML ──────────────────────────────────────────────────────────────────────

_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>WS9 Paper Trading</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
  :root {
    --bg: #0f1117;
    --card: #1a1d27;
    --border: #2a2d3a;
    --text: #e2e8f0;
    --muted: #8892a4;
    --green: #22c55e;
    --red: #ef4444;
    --yellow: #eab308;
    --blue: #3b82f6;
    --accent: #6366f1;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text); font-family: 'SF Mono', 'Fira Code', monospace; font-size: 13px; padding: 20px; }
  h2 { font-size: 11px; text-transform: uppercase; letter-spacing: .1em; color: var(--muted); margin-bottom: 12px; }
  .grid { display: grid; gap: 16px; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); }
  .card { background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 16px; }
  .card.wide { grid-column: 1 / -1; }
  .status-line { font-size: 20px; font-weight: 700; margin-bottom: 4px; }
  .status-meta { color: var(--muted); font-size: 11px; }
  .alive { color: var(--green); }
  .stale { color: var(--red); }
  .pnl { font-size: 28px; font-weight: 700; }
  .pnl-meta { color: var(--muted); font-size: 11px; margin-top: 4px; }
  .pos-up { color: var(--green); }
  .pos-dn { color: var(--red); }
  table { width: 100%; border-collapse: collapse; }
  th { text-align: left; color: var(--muted); font-weight: normal; padding: 4px 8px; border-bottom: 1px solid var(--border); cursor: pointer; user-select: none; }
  th:hover { color: var(--text); }
  td { padding: 5px 8px; border-bottom: 1px solid var(--border); }
  tr:last-child td { border-bottom: none; }
  .err { color: var(--red); }
  .event-item { padding: 6px 0; border-bottom: 1px solid var(--border); }
  .event-item:last-child { border-bottom: none; }
  .event-ts { color: var(--muted); font-size: 11px; }
  .event-msg { margin-top: 2px; }
  .kind-skip { color: var(--yellow); }
  .kind-portfolio_size_change { color: var(--blue); }
  .ctrl { display: flex; gap: 8px; align-items: center; margin-top: 12px; }
  input[type=number] { background: var(--bg); border: 1px solid var(--border); color: var(--text); padding: 6px 10px; border-radius: 4px; font-family: inherit; font-size: 13px; width: 140px; }
  button { background: var(--accent); color: #fff; border: none; padding: 6px 14px; border-radius: 4px; font-family: inherit; font-size: 13px; cursor: pointer; }
  button:hover { opacity: .85; }
  .confirm-msg { margin-top: 8px; font-size: 11px; color: var(--green); min-height: 16px; }
  #chart-container { height: 220px; }
  .refresh-ts { font-size: 10px; color: var(--muted); text-align: right; margin-bottom: 8px; }
</style>
</head>
<body>

<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;">
  <div style="font-size:16px;font-weight:700;">WS9 Paper Trading</div>
  <div class="refresh-ts" id="refresh-ts"></div>
</div>

<div class="grid">

  <!-- 1. Status line -->
  <div class="card">
    <h2>System Status</h2>
    <div class="status-line" id="status-text">--</div>
    <div class="status-meta" id="status-meta"></div>
  </div>

  <!-- 2. Portfolio value -->
  <div class="card">
    <h2>Paper Portfolio</h2>
    <div class="pnl" id="pnl-value">--</div>
    <div class="pnl-meta" id="pnl-meta"></div>
  </div>

  <!-- 3. Equity curve -->
  <div class="card wide">
    <h2>Equity Curve (deployed notional over time)</h2>
    <div id="chart-container"><canvas id="equity-chart"></canvas></div>
  </div>

  <!-- 4. Current positions -->
  <div class="card wide">
    <h2>Current Positions <span id="pos-ts" style="color:var(--muted);font-weight:normal;font-size:10px;"></span></h2>
    <table id="pos-table">
      <thead>
        <tr>
          <th onclick="sortTable('pos-table',0)">Symbol</th>
          <th onclick="sortTable('pos-table',1)">Notional ($)</th>
          <th onclick="sortTable('pos-table',2)">Leverage</th>
          <th onclick="sortTable('pos-table',3)">Vol Estimate</th>
          <th onclick="sortTable('pos-table',4)">Fill Price</th>
          <th onclick="sortTable('pos-table',5)">Latency (ms)</th>
          <th onclick="sortTable('pos-table',6)">Status</th>
        </tr>
      </thead>
      <tbody id="pos-body"></tbody>
    </table>
  </div>

  <!-- 5. Recent events -->
  <div class="card wide">
    <h2>Recent Events (last 50)</h2>
    <div id="events-feed"></div>
  </div>

  <!-- 6. Portfolio size control -->
  <div class="card">
    <h2>Portfolio Size Control</h2>
    <div style="color:var(--muted);font-size:11px;margin-bottom:8px;">
      Every change is timestamped and logged as an event. It will appear in
      the Recent Events feed and as a marker on the equity curve.
    </div>
    <div>Current: <strong id="pf-size-current">--</strong></div>
    <div class="ctrl">
      <input type="number" id="pf-size-input" min="100" step="100" placeholder="New value ($)">
      <button onclick="changePortfolioSize()">Change</button>
    </div>
    <div class="confirm-msg" id="pf-confirm"></div>
  </div>

</div>

<script>
let equityChart = null;
let sortDir = {};

function fmt(v, digits=2) {
  if (v == null) return '--';
  return Number(v).toFixed(digits);
}

function fmtUsd(v) {
  if (v == null) return '--';
  return '$' + Number(v).toLocaleString('en-US', {minimumFractionDigits:2, maximumFractionDigits:2});
}

async function loadStatus() {
  const r = await fetch('/api/status');
  const d = await r.json();

  // Status line
  const hb = d.heartbeat;
  const stEl = document.getElementById('status-text');
  const metaEl = document.getElementById('status-meta');
  if (hb.alive) {
    stEl.textContent = 'ALIVE';
    stEl.className = 'status-line alive';
  } else {
    stEl.textContent = 'STALE';
    stEl.className = 'status-line stale';
  }
  const ageStr = hb.age_seconds != null ? `${fmt(hb.age_seconds/60,1)} min ago` : 'never';
  metaEl.textContent = `Last heartbeat: ${hb.last_heartbeat || 'none'} (${ageStr}) | threshold: ${d.stale_threshold_seconds/3600}h`;

  // Portfolio value
  const pf = d.portfolio;
  const stats = d.stats;
  const pnlEl = document.getElementById('pnl-value');
  const pnlMetaEl = document.getElementById('pnl-meta');
  const pnlSign = pf.pnl_usd >= 0 ? '+' : '';
  pnlEl.textContent = fmtUsd(pf.latest_deployed_notional);
  pnlEl.className = 'pnl ' + (pf.pnl_usd >= 0 ? 'pos-up' : 'pos-dn');
  pnlMetaEl.innerHTML =
    `Started at ${fmtUsd(pf.current_size_usd)} &nbsp;|&nbsp; ` +
    `P&amp;L: ${pnlSign}${fmtUsd(pf.pnl_usd)} (${pnlSign}${fmt(pf.pnl_pct,3)}%)<br>` +
    `${stats.days_running != null ? stats.days_running + ' days running' : 'day 1'} &nbsp;|&nbsp; ` +
    `${stats.total_trades.toLocaleString()} trade decisions logged`;

  document.getElementById('pf-size-current').textContent = fmtUsd(pf.current_size_usd);
}

async function loadEquity() {
  const r = await fetch('/api/equity');
  const data = await r.json();

  if (!data.length) {
    document.querySelector('#chart-container').innerHTML =
      '<div style="color:var(--muted);padding:60px 0;text-align:center;">No data yet -- system just started</div>';
    return;
  }

  const labels = data.map(d => d.timestamp.slice(0,16).replace('T',' '));
  const values = data.map(d => d.value);

  // Vertical markers for portfolio size changes
  const evResp = await fetch('/api/events');
  const evData = await evResp.json();
  const sizeChanges = evData.filter(e => e.kind === 'portfolio_size_change');

  const annotations = {};
  sizeChanges.forEach((ev, i) => {
    const label = labels.find(l => ev.timestamp.startsWith(l.replace(' ','T')));
    if (!label) return;
    const idx = labels.indexOf(label);
    annotations['sc' + i] = {
      type: 'line',
      xMin: idx, xMax: idx,
      borderColor: 'rgba(99,102,241,0.8)',
      borderWidth: 2,
      borderDash: [4,4],
      label: { content: 'size change', enabled: true, position: 'start', color: '#6366f1', font: {size: 9} }
    };
  });

  const ctx = document.getElementById('equity-chart');
  if (equityChart) equityChart.destroy();
  equityChart = new Chart(ctx, {
    type: 'line',
    data: {
      labels,
      datasets: [{
        label: 'Deployed Notional ($)',
        data: values,
        borderColor: '#3b82f6',
        backgroundColor: 'rgba(59,130,246,0.08)',
        fill: true,
        tension: 0.2,
        pointRadius: data.length > 50 ? 0 : 3,
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      scales: {
        x: { ticks: { color: '#8892a4', maxTicksLimit: 8, font: {size: 10} }, grid: { color: '#1e2130' } },
        y: { ticks: { color: '#8892a4', font: {size: 10} }, grid: { color: '#1e2130' } }
      },
      plugins: {
        legend: { labels: { color: '#8892a4', font: {size: 10} } },
        ...(Object.keys(annotations).length ? {annotation: {annotations}} : {})
      }
    }
  });
}

async function loadPositions() {
  const r = await fetch('/api/positions');
  const data = await r.json();
  const tbody = document.getElementById('pos-body');
  tbody.innerHTML = '';

  if (!data.length) {
    tbody.innerHTML = '<tr><td colspan="7" style="color:var(--muted);text-align:center;padding:20px;">No positions yet</td></tr>';
    return;
  }

  data.forEach(row => {
    const tr = document.createElement('tr');
    tr.innerHTML = [
      row.symbol,
      row.target_size_notional != null ? fmtUsd(row.target_size_notional) : '--',
      row.leverage != null ? fmt(row.leverage) + 'x' : '--',
      row.vol_estimate != null ? (row.vol_estimate * 100).toFixed(4) + '%' : '--',
      row.actual_fill_price != null ? fmt(row.actual_fill_price, 4) : '--',
      row.latency_ms != null ? fmt(row.latency_ms, 0) : '--',
      row.error ? `<span class="err">SKIP</span>` : `<span style="color:var(--green)">OK</span>`,
    ].map(v => `<td>${v}</td>`).join('');
    tbody.appendChild(tr);
  });
}

async function loadEvents() {
  const r = await fetch('/api/events');
  const data = await r.json();
  const feed = document.getElementById('events-feed');
  feed.innerHTML = '';

  if (!data.length) {
    feed.innerHTML = '<div style="color:var(--muted);padding:12px 0;">No events yet</div>';
    return;
  }

  data.forEach(ev => {
    const div = document.createElement('div');
    div.className = 'event-item';
    div.innerHTML =
      `<div class="event-ts">${ev.timestamp}</div>` +
      `<div class="event-msg kind-${ev.kind}">${ev.message}</div>`;
    feed.appendChild(div);
  });
}

function sortTable(tableId, col) {
  const tbody = document.getElementById(tableId.replace('pos-table', 'pos-body'));
  const rows = Array.from(tbody.querySelectorAll('tr'));
  const key = tableId + ':' + col;
  sortDir[key] = !sortDir[key];
  rows.sort((a, b) => {
    const av = a.cells[col]?.textContent.trim() || '';
    const bv = b.cells[col]?.textContent.trim() || '';
    const an = parseFloat(av.replace(/[$,%x]/g,''));
    const bn = parseFloat(bv.replace(/[$,%x]/g,''));
    const cmp = isNaN(an) || isNaN(bn) ? av.localeCompare(bv) : an - bn;
    return sortDir[key] ? cmp : -cmp;
  });
  rows.forEach(r => tbody.appendChild(r));
}

async function changePortfolioSize() {
  const input = document.getElementById('pf-size-input');
  const confirm = document.getElementById('pf-confirm');
  const val = parseFloat(input.value);
  if (!val || val <= 0) { confirm.textContent = 'Enter a valid value > 0'; confirm.style.color = 'var(--red)'; return; }
  try {
    const r = await fetch('/api/portfolio_size', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({value: val}),
    });
    const d = await r.json();
    if (d.ok) {
      confirm.textContent = `Changed from $${d.old_value.toLocaleString()} to $${d.new_value.toLocaleString()} -- logged.`;
      confirm.style.color = 'var(--green)';
      input.value = '';
      await refresh();
    } else {
      confirm.textContent = d.error || 'Unknown error';
      confirm.style.color = 'var(--red)';
    }
  } catch(e) {
    confirm.textContent = String(e);
    confirm.style.color = 'var(--red)';
  }
}

async function refresh() {
  await Promise.all([loadStatus(), loadEquity(), loadPositions(), loadEvents()]);
  document.getElementById('refresh-ts').textContent =
    'Refreshed ' + new Date().toISOString().slice(0,19).replace('T',' ') + ' UTC';
}

refresh();
setInterval(refresh, 60_000);
</script>
</body>
</html>"""


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    _ensure_schema()
    port = int(os.environ.get("DASHBOARD_PORT", "8080"))
    print(f"[dashboard] Listening on http://0.0.0.0:{port}", flush=True)
    app.run(host="0.0.0.0", port=port, debug=False)


if __name__ == "__main__":
    main()

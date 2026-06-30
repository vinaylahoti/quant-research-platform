"""
Daily reconciliation report for WS9 paper trading.

Reads data/paper_trading.db and writes a plain CSV summary to
data/paper_trading_reports/YYYY-MM-DD.csv.

Run manually or via cron:
    py -m src.paper_trading.report
"""

from __future__ import annotations

import csv
import datetime
import json
import sqlite3
from pathlib import Path

from src.paper_trading.config import LOG_DB_PATH, REPORT_DIR


def _load_trades(since: datetime.date) -> list[dict]:
    if not LOG_DB_PATH.exists():
        return []
    with sqlite3.connect(LOG_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM trades WHERE timestamp_decision >= ? ORDER BY id",
            (since.isoformat(),),
        ).fetchall()
    return [dict(r) for r in rows]


def _summarise(trades: list[dict], report_date: datetime.date) -> list[dict]:
    """Aggregate per-symbol stats for the report date."""
    by_symbol: dict[str, dict] = {}

    for t in trades:
        # Only include rows from the report date.
        ts = t["timestamp_decision"][:10]  # YYYY-MM-DD prefix
        if ts != report_date.isoformat():
            continue

        sym = t["symbol"]
        if sym not in by_symbol:
            by_symbol[sym] = {
                "symbol": sym,
                "ticks": 0,
                "skipped": 0,
                "vol_estimates": [],
                "target_notionals": [],
                "slippage_vs_intended_bps": [],
            }
        s = by_symbol[sym]
        s["ticks"] += 1

        if t["error"] is not None:
            s["skipped"] += 1
            continue

        if t["vol_estimate"] is not None:
            s["vol_estimates"].append(t["vol_estimate"])
        if t["target_size_notional"] is not None:
            s["target_notionals"].append(t["target_size_notional"])

        intended = t["intended_fill_price"]
        actual = t["actual_fill_price"]
        if intended and actual and intended > 0:
            realized_slip = (actual / intended - 1.0) * 10_000
            s["slippage_vs_intended_bps"].append(realized_slip)

    rows = []
    for sym, s in sorted(by_symbol.items()):
        vols = s["vol_estimates"]
        notionals = s["target_notionals"]
        slips = s["slippage_vs_intended_bps"]
        rows.append({
            "date": report_date.isoformat(),
            "symbol": sym,
            "ticks": s["ticks"],
            "skipped": s["skipped"],
            "avg_vol_estimate": round(sum(vols) / len(vols), 6) if vols else None,
            "avg_target_notional_usd": round(sum(notionals) / len(notionals), 2) if notionals else None,
            "avg_slippage_bps": round(sum(slips) / len(slips), 4) if slips else None,
        })
    return rows


def write_report(report_date: datetime.date | None = None) -> Path:
    if report_date is None:
        report_date = datetime.date.today()

    # Load from the start of the previous day to catch any boundary trades.
    since = report_date - datetime.timedelta(days=1)
    trades = _load_trades(since)
    rows = _summarise(trades, report_date)

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = REPORT_DIR / f"{report_date.isoformat()}.csv"

    if not rows:
        out_path.write_text(f"# No trades on {report_date.isoformat()}\n")
        print(f"[report] No trades for {report_date} — wrote empty report to {out_path}")
        return out_path

    fieldnames = list(rows[0].keys())
    with out_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"[report] Wrote {len(rows)} symbol rows to {out_path}")
    return out_path


if __name__ == "__main__":
    write_report()

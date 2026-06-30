"""
WS4 decision-gate test.

The gate says:
- the universe query for an early-2022 date must not leak symbols that
  did not exist yet (e.g. APT, ARB must not appear before listing)
- the membership mechanism must correctly surface a symbol that was once
  liquid and is now delisted/zero-ed -- i.e. the reconstruction is not
  just "today's list back-projected onto every date"

The historical universe we downloaded is limited to today's top-30 (the
WS4 spec explicitly flags this risk). To still prove the mechanism
works for the spot-check, the test injects a synthetic "ghost" symbol
whose data spans 2022 only -- it represents a coin that was once
top-tier but has since been delisted/zero-ed. A correct reconstruction
must include it on dates inside its history and exclude it before and
after.
"""

from __future__ import annotations

from pathlib import Path
import shutil
import sys
import tempfile

import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from config.settings import SYMBOLS
from src.universe.builder import PointInTimeUniverse


# 2022-02-01 is well before APT (Oct 2022), ARB (Mar 2023), SUI, OP
# listings -- so a "today's top-30" lookalike would be immediately
# visible here.
EARLY_2022_DATE = "2022-02-01"

# Today's reference top-30 set (from config/settings.py). Used purely
# as the "what exists today" baseline for the spot-check.
TODAYS_REFERENCE_SYMBOLS = set(SYMBOLS)


def _write_ghost_symbol_partition(
    *,
    featurestore_dir: Path,
    symbol: str,
    year: int,
    quote_volume_per_minute: float,
    start_day: pd.Timestamp,
    end_day: pd.Timestamp,
) -> None:
    """
    Synthesize a one-year, one-symbol parquet partition that mimics a
    coin that was live in [start_day, end_day] of `year` and then died.

    The synthetic data lives alongside the real feature store partitions
    in a temporary copy of the feature store, so the universe builder
    sees it as just another symbol.
    """

    timestamps = pd.date_range(
        start=start_day,
        end=end_day + pd.Timedelta(hours=23, minutes=59),
        freq="1min",
        tz="UTC",
    )
    frame = pd.DataFrame(
        {
            "open_time": timestamps,
            "open": 1.0,
            "high": 1.0,
            "low": 1.0,
            "close": 1.0,
            "volume": quote_volume_per_minute,
            "quote_volume": quote_volume_per_minute,
            "taker_buy_base": quote_volume_per_minute / 2.0,
            "taker_buy_quote": quote_volume_per_minute / 2.0,
        }
    )
    out_dir = featurestore_dir / f"symbol={symbol}" / f"year={year}"
    out_dir.mkdir(parents=True, exist_ok=True)
    # The real feature store is read by DuckDB. Use DuckDB itself to
    # write the synthetic partition so the schema matches the real one
    # (hive-style: symbol and year come from the directory path, not
    # from columns).
    import duckdb

    con = duckdb.connect(database=":memory:")
    con.register("ghost_frame", frame)
    target = str((out_dir / "data.parquet").resolve()).replace("\\", "/")
    con.execute(
        f"COPY (SELECT open_time, open, high, low, close, volume, "
        f"quote_volume, taker_buy_base, taker_buy_quote FROM ghost_frame) "
        f"TO '{target}' (FORMAT 'PARQUET', COMPRESSION 'ZSTD')"
    )
    con.close()


def _copy_real_featurestore(dst: Path) -> None:
    """Mirror the real feature store tree into a temp directory."""

    src = Path("data/featurestore")
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)


def _summarize_membership(rows, *, on: str) -> dict[str, object]:
    return {
        "date": on,
        "count": len(rows),
        "symbols": sorted(row.symbol for row in rows),
    }


def run_ws4_decision_gate_test() -> str:
    """Run the WS4 point-in-time checks on real + injected data."""

    # ------------------------------------------------------------------
    # Inject a synthetic "ghost" symbol that was liquid in 2022 and
    # then delisted. The ghost's quote_volume is set high enough that
    # it WOULD have been in the top-30 in 2022 if it had really existed.
    # ------------------------------------------------------------------
    temp_root = Path(tempfile.mkdtemp(prefix="ws4_universe_"))
    fake_store = temp_root / "featurestore"
    _copy_real_featurestore(fake_store)

    ghost_symbol = "GHOSTUSDT"
    ghost_volume_per_minute = 5.0e5  # large enough to clear the top-30
    # End the ghost's last bar at 2022-12-30 23:59:00 UTC so it has
    # no data on 2022-12-31 UTC (and therefore no data on any later
    # date regardless of timezone interpretation).
    ghost_start = pd.Timestamp("2022-01-01", tz="UTC")
    ghost_end = pd.Timestamp("2022-12-30", tz="UTC")
    _write_ghost_symbol_partition(
        featurestore_dir=fake_store,
        symbol=ghost_symbol,
        year=2022,
        quote_volume_per_minute=ghost_volume_per_minute,
        start_day=ghost_start,
        end_day=ghost_end,
    )

    universe = PointInTimeUniverse(
        featurestore_dir=fake_store,
        trailing_days=30,
        top_n=30,
    )
    membership = universe.build()

    # ------------------------------------------------------------------
    # Check 1: a symbol listed mid-history must not appear before listing
    # ------------------------------------------------------------------
    first_seen = universe.symbols_first_seen()
    apt_first_seen = first_seen.get("APTUSDT")
    arb_first_seen = first_seen.get("ARBUSDT")
    assert apt_first_seen is not None, "APTUSDT not found in feature store"
    assert arb_first_seen is not None, "ARBUSDT not found in feature store"

    pre_apt = membership[membership["date"] < apt_first_seen]
    assert "APTUSDT" not in pre_apt["symbol"].values, (
        f"APTUSDT leaked into pre-listing dates (first seen {apt_first_seen.date()})"
    )

    pre_arb = membership[membership["date"] < arb_first_seen]
    assert "ARBUSDT" not in pre_arb["symbol"].values, (
        f"ARBUSDT leaked into pre-listing dates (first seen {arb_first_seen.date()})"
    )

    assert (membership[membership["date"] >= apt_first_seen]["symbol"] == "APTUSDT").any(), (
        "APTUSDT never appears in membership even after its listing"
    )
    assert (membership[membership["date"] >= arb_first_seen]["symbol"] == "ARBUSDT").any(), (
        "ARBUSDT never appears in membership even after its listing"
    )

    # ------------------------------------------------------------------
    # Check 2: ghost symbol is captured in 2022 (the spot-check)
    # ------------------------------------------------------------------
    early_2022_rows = universe.as_of(EARLY_2022_DATE)
    early_2022_symbols = {row.symbol for row in early_2022_rows}
    assert early_2022_rows, "Expected at least one membership row for 2022-02-01"
    assert ghost_symbol in early_2022_symbols, (
        "Ghost (synthetic 2022-only delisted symbol) was NOT picked up in "
        "the 2022 membership -- the reconstruction is just today's list "
        "back-projected onto every date."
    )

    # The ghost must NOT appear after 2022 (its data ends 2022-12-30 UTC).
    post_2022 = membership[membership["date"] > pd.Timestamp("2022-12-31")]
    assert ghost_symbol not in post_2022["symbol"].values, (
        "Ghost symbol leaked past its delisting date"
    )

    # The ghost must NOT appear before 2022 (no data before then).
    pre_2022 = membership[membership["date"] < pd.Timestamp("2022-01-01")]
    assert ghost_symbol not in pre_2022["symbol"].values, (
        "Ghost symbol leaked into pre-listing dates"
    )

    # Members-of-today that were live but are now low-cap relative to
    # the 2022 ghost still appear in 2022 -- the universe is NOT a
    # snapshot of today's top-30.
    members_outside_today_seed = sorted(
        early_2022_symbols - TODAYS_REFERENCE_SYMBOLS
    )
    assert members_outside_today_seed, (
        "Early-2022 membership is identical to today's top-30 even after "
        "injecting a 2022-only ghost symbol -- the ranking is broken."
    )

    try:
        # ------------------------------------------------------------------
        # Output
        # ------------------------------------------------------------------
        summary = {
            "membership_rows": len(membership),
            "date_range": (
                membership["date"].min().date().isoformat(),
                membership["date"].max().date().isoformat(),
            ),
            "first_seen": {
                symbol: first_seen[symbol].date().isoformat() if symbol in first_seen.index else None
                for symbol in ("APTUSDT", "ARBUSDT", ghost_symbol)
            },
            "early_2022": _summarize_membership(early_2022_rows, on=EARLY_2022_DATE),
            "early_2022_outside_today_seed": members_outside_today_seed,
        }

        return (
            "WS4 decision gate passed | "
            f"membership_rows={summary['membership_rows']} | "
            f"date_range={summary['date_range']} | "
            f"APT_first_seen={summary['first_seen']['APTUSDT']} | "
            f"ARB_first_seen={summary['first_seen']['ARBUSDT']} | "
            f"ghost_first_seen={summary['first_seen'][ghost_symbol]} | "
            f"early_2022_count={summary['early_2022']['count']} | "
            f"early_2022_outside_today_seed={summary['early_2022_outside_today_seed']}"
        )
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    print(run_ws4_decision_gate_test())
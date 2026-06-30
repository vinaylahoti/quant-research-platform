"""
WS4 -- Point-in-time / dynamic universe.

Why this exists
---------------
"Top-N by volume" selected today and then backtested over 5 years bakes in
survivorship bias: today's list is built from coins that survived and grew.
Coins that died (delisted, went to zero) are invisible to a backtest that
only looks at today's winners. A mediocre strategy looks profitable purely
from that bias.

What this module does
---------------------
For every calendar day in the feature store, compute each symbol's
trailing 30-day quote volume, rank the symbols that are *actually live*
as of that date, and emit the top-N. "Live" means the symbol has at
least one bar in the feature store on or before the date in question.

This means:

* A symbol only enters the universe after its first observed bar --
  it cannot appear in dates before its listing.
* A symbol stays in the universe through its last observed bar -- it
  does not retroactively vanish from earlier history just because its
  data ends today.
* The membership table is reproducible: same parquet input -> same
  membership table -> same backtest universe.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterable

import pandas as pd

from src.data.featurestore import FEATURE_STORE_DIR
from src.data.query import QueryEngine


# Trailing window for the volume ranking, expressed in calendar days.
# 30 days is long enough to be stable, short enough to react when a
# coin's liquidity dries up or a new coin ramps up.
DEFAULT_TRAILING_VOLUME_DAYS = 30

# Default size of the top-N membership slice.
DEFAULT_TOP_N = 30


@dataclass(frozen=True)
class UniverseMembership:
    """One row of the daily membership table."""

    date: pd.Timestamp
    symbol: str
    rank: int
    trailing_volume: float

    def as_dict(self) -> dict[str, object]:
        return {
            "date": self.date.date().isoformat(),
            "symbol": self.symbol,
            "rank": self.rank,
            "trailing_volume": round(self.trailing_volume, 4),
        }


class PointInTimeUniverse:
    """
    Build a daily top-N membership table that respects each symbol's
    actual first/last observation date.

    The membership table is computed once and then queried as-of any
    historical date via `as_of()`. That is the only API a backtest
    should use to ask "which symbols were in the top-N on date X?".
    """

    def __init__(
        self,
        *,
        featurestore_dir: Path = FEATURE_STORE_DIR,
        trailing_days: int = DEFAULT_TRAILING_VOLUME_DAYS,
        top_n: int = DEFAULT_TOP_N,
    ) -> None:
        if trailing_days < 1:
            raise ValueError("trailing_days must be >= 1")
        if top_n < 1:
            raise ValueError("top_n must be >= 1")

        self.trailing_days = trailing_days
        self.top_n = top_n
        self.engine = QueryEngine(featurestore_dir=featurestore_dir)
        self._membership: pd.DataFrame | None = None

    # ------------------------------------------------------------------
    # Build step
    # ------------------------------------------------------------------
    def build(self) -> pd.DataFrame:
        """
        Compute the full daily top-N membership table.

        The table has one row per (date, symbol) where the symbol is in
        the top-N on that date. Columns:

            date, symbol, rank, trailing_volume
        """
        daily = self._daily_quote_volume()
        symbol_lifetimes = self._symbol_lifetimes(daily)

        # Trailing rolling sum per symbol. We work per-symbol so the
        # window only looks at one symbol's own history.
        trailing = (
            daily.sort_values(["symbol", "date"])
            .set_index("date")
            .groupby("symbol")["quote_volume"]
            .rolling(window=f"{self.trailing_days}D", min_periods=1)
            .sum()
            .reset_index()
        )
        trailing = trailing.rename(columns={"quote_volume": "trailing_volume"})

        trailing = trailing.merge(symbol_lifetimes, on="symbol", how="left")
        trailing = trailing[
            (trailing["date"] >= trailing["first_seen"])
            & (trailing["date"] <= trailing["last_seen"])
        ]

        # Rank live symbols by trailing volume on each date.
        trailing = trailing.sort_values(["date", "trailing_volume", "symbol"], ascending=[True, False, True])
        trailing["rank"] = (
            trailing.groupby("date").cumcount() + 1
        )

        # Keep only the top-N membership slots.
        membership = trailing[trailing["rank"] <= self.top_n].copy()
        membership = membership[["date", "symbol", "rank", "trailing_volume"]].sort_values(
            ["date", "rank"]
        ).reset_index(drop=True)

        self._membership = membership
        return membership

    # ------------------------------------------------------------------
    # Query step
    # ------------------------------------------------------------------
    def as_of(self, on: str | date | pd.Timestamp) -> list[UniverseMembership]:
        """
        Return the top-N membership that was in force on `on`.

        A backtest calling this with the simulated date gets exactly the
        universe an investor at that date would have seen, not today's
        winners.
        """
        if self._membership is None:
            self.build()

        assert self._membership is not None
        target = pd.Timestamp(on).normalize()
        rows = self._membership[self._membership["date"] == target]
        if rows.empty:
            return []

        return [
            UniverseMembership(
                date=row["date"],
                symbol=row["symbol"],
                rank=int(row["rank"]),
                trailing_volume=float(row["trailing_volume"]),
            )
            for _, row in rows.iterrows()
        ]

    def membership_table(self) -> pd.DataFrame:
        """Return the full membership DataFrame (rebuilds if needed)."""

        if self._membership is None:
            self.build()
        assert self._membership is not None
        return self._membership.copy()

    def symbols_first_seen(self) -> pd.Series:
        """First calendar date each symbol appears in the feature store."""

        daily = self._daily_quote_volume()
        return self._symbol_lifetimes(daily).set_index("symbol")["first_seen"].sort_index()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _daily_quote_volume(self) -> pd.DataFrame:
        """Aggregate 1-minute bars into daily quote_volume per symbol."""

        query = """
            SELECT
                symbol,
                CAST(open_time AS DATE) AS date,
                SUM(quote_volume) AS quote_volume
            FROM klines
            GROUP BY symbol, CAST(open_time AS DATE)
            ORDER BY symbol, date
        """
        df = self.engine.sql(query)
        df["date"] = pd.to_datetime(df["date"])
        df["quote_volume"] = df["quote_volume"].astype(float)
        return df

    @staticmethod
    def _symbol_lifetimes(daily: pd.DataFrame) -> pd.DataFrame:
        """First and last calendar dates each symbol produced a bar."""

        return daily.groupby("symbol", as_index=False).agg(
            first_seen=("date", "min"),
            last_seen=("date", "max"),
        )


def build_universe(
    *,
    featurestore_dir: Path = FEATURE_STORE_DIR,
    trailing_days: int = DEFAULT_TRAILING_VOLUME_DAYS,
    top_n: int = DEFAULT_TOP_N,
) -> PointInTimeUniverse:
    """Convenience constructor that builds the membership table up front."""

    universe = PointInTimeUniverse(
        featurestore_dir=featurestore_dir,
        trailing_days=trailing_days,
        top_n=top_n,
    )
    universe.build()
    return universe
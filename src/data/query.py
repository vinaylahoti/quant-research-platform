"""
DUCKDB QUERY LAYER
============================================================
What this is:
  A thin query layer on top of the parquet feature store you
  already built. It does NOT create a new copy of your data.
  DuckDB reads the .parquet files in data/featurestore/ directly,
  on demand, every time you run a query -- there's nothing to
  "load" or "sync." Your parquet files stay the single source
  of truth from WS2.

Why it exists:
  featurestore.load() is built for "give me ONE symbol's data."
  That's the right tool for backtesting one symbol at a time.
  But WS3 (walk-forward validation) and WS4 (point-in-time
  universe) both need to ask questions ACROSS all 30 symbols at
  once -- e.g. "rank every symbol by trailing volume, for every
  date in history." Doing that by looping through 30 separate
  pandas DataFrames in Python is slow and awkward. A single SQL
  query across all parquet files at once is the natural fit.

How to run this file directly (a quick demo / sanity check):
  python query.py

How to use it from other code:
  from src.data.query import QueryEngine
  qe = QueryEngine()
  df = qe.sql("SELECT symbol, date_trunc('day', timestamp) AS day, "
              "       sum(quote_volume) AS day_volume "
              "FROM klines GROUP BY 1, 2 ORDER BY 2, 3 DESC")

Install (one-time):
  pip install duckdb
============================================================
"""

from __future__ import annotations

from pathlib import Path

import duckdb


# Points at the SAME folder featurestore.py already writes to.
# No new path, no new copy of the data.
FEATURESTORE_DIR = Path(__file__).resolve().parents[2] / "data" / "featurestore"


class QueryEngine:
    """
    Thin wrapper around an in-memory DuckDB connection that exposes
    your existing parquet feature store as a SQL-queryable table
    called `klines`.

    Nothing is copied or imported into DuckDB's own storage -- the
    `klines` name is a VIEW. Every query reads the .parquet files
    on disk fresh. Delete or update a parquet file and the next
    query reflects that immediately, with zero sync step.
    """

    def __init__(self, featurestore_dir: Path = FEATURESTORE_DIR):
        self.featurestore_dir = featurestore_dir
        self.con = duckdb.connect(database=":memory:")
        self._register_views()

    def _register_views(self) -> None:
        # Match only klines hive partitions (symbol=X/year=Y/data.parquet).
        # The featurestore root also contains funding_rate/ subdirectories with
        # a different schema; the explicit pattern avoids picking those up.
        glob_path = str(self.featurestore_dir / "symbol=*" / "year=*" / "data.parquet")

        # filename=True asks DuckDB to add a column containing each
        # row's source filename. Your parquet files are presumably
        # named/partitioned by symbol (e.g. BTCUSDT/2023.parquet) --
        # if featurestore.py exposes symbol as a partition column or
        # encodes it in the filename, adjust the SELECT below to
        # parse it out into a clean `symbol` column. As a starting
        # point this assumes symbol is already a column in the
        # parquet files themselves (most featurestore implementations
        # add it during the build step). If it's not, see the
        # fallback note in `_register_views_fallback` below.
        self.con.execute(f"""
            CREATE OR REPLACE VIEW klines AS
            SELECT *
            FROM read_parquet('{glob_path}', hive_partitioning = true)
        """)

    def sql(self, query: str):
        """Run a raw SQL query against the `klines` view, return a pandas DataFrame."""
        return self.con.execute(query).df()

    def symbols(self):
        """Quick sanity check: list distinct symbols visible to DuckDB right now."""
        return self.sql("SELECT DISTINCT symbol FROM klines ORDER BY symbol")

    def row_count(self):
        """Quick sanity check: total rows DuckDB can see across all parquet files."""
        return self.sql("SELECT count(*) AS rows FROM klines")


def _demo():
    qe = QueryEngine()

    print("=" * 60)
    print("DuckDB query layer -- sanity check")
    print("=" * 60)

    try:
        syms = qe.symbols()
        print(f"Distinct symbols visible: {len(syms)}")
        print(syms.to_string(index=False))
    except Exception as e:
        print(f"Could not list symbols -- check that 'symbol' is a column")
        print(f"or partition key in your parquet files. Error: {e}")
        return

    try:
        rows = qe.row_count()
        print(f"\nTotal rows across all parquet files: {rows.iloc[0]['rows']:,}")
    except Exception as e:
        print(f"Row count failed: {e}")

    # Example of the kind of cross-symbol query WS4 will need:
    # trailing volume ranking, across ALL symbols, for the most
    # recent date available -- this is the building block for
    # "top-N by volume as of date X" used in point-in-time universe
    # reconstruction.
    print("\n" + "=" * 60)
    print("Example: top symbols by total quote_volume (all-time)")
    print("=" * 60)
    try:
        top = qe.sql("""
            SELECT symbol, sum(quote_volume) AS total_quote_volume
            FROM klines
            GROUP BY symbol
            ORDER BY total_quote_volume DESC
            LIMIT 10
        """)
        print(top.to_string(index=False))
    except Exception as e:
        print(f"Example query failed: {e}")


if __name__ == "__main__":
    _demo()

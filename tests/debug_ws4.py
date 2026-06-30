"""Debug ghost leak."""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

import duckdb
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.universe.builder import PointInTimeUniverse


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="ws4_dbg5_"))
    fake_store = temp_root / "featurestore"
    src = Path("data/featurestore")
    shutil.copytree(src, fake_store)

    ghost_symbol = "GHOSTUSDT"
    ghost_volume = 5.0e5
    ghost_start = pd.Timestamp("2022-01-01", tz="UTC")
    ghost_end = pd.Timestamp("2022-12-30 23:59:00", tz="UTC")
    timestamps = pd.date_range(start=ghost_start, end=ghost_end, freq="1min", tz="UTC")
    frame = pd.DataFrame(
        {
            "open_time": timestamps,
            "open": 1.0,
            "high": 1.0,
            "low": 1.0,
            "close": 1.0,
            "volume": ghost_volume,
            "quote_volume": ghost_volume,
            "taker_buy_base": ghost_volume / 2.0,
            "taker_buy_quote": ghost_volume / 2.0,
        }
    )
    out_dir = fake_store / f"symbol={ghost_symbol}" / "year=2022"
    out_dir.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(database=":memory:")
    con.register("ghost_frame", frame)
    target = str((out_dir / "data.parquet").resolve()).replace("\\", "/")
    con.execute(
        f"COPY (SELECT open_time, open, high, low, close, volume, "
        f"quote_volume, taker_buy_base, taker_buy_quote FROM ghost_frame) "
        f"TO '{target}' (FORMAT 'PARQUET', COMPRESSION 'ZSTD')"
    )
    con.close()

    u = PointInTimeUniverse(featurestore_dir=fake_store, trailing_days=30, top_n=30)
    m = u.build()

    post_2022 = m[m["date"] > pd.Timestamp("2022-12-31")]
    print("post 2022-12-31 rows:", len(post_2022))
    print("post_2022 contains ghost:", ghost_symbol in post_2022["symbol"].values)
    print("membership date dtype:", m["date"].dtype)

    # What dates does ghost appear in?
    ghost_rows = m[m["symbol"] == ghost_symbol]
    print("ghost last 5 dates:", sorted(ghost_rows["date"].astype(str).tolist())[-5:])
    print("ghost first 3 dates:", sorted(ghost_rows["date"].astype(str).tolist())[:3])

    # Show 2023-01-01 row if any
    jan1 = m[m["date"] == pd.Timestamp("2023-01-01")]
    print("2023-01-01 row count:", len(jan1))
    print("2023-01-01 has ghost:", ghost_symbol in jan1["symbol"].values)
    if not jan1.empty:
        print("2023-01-01 symbols:", sorted(jan1["symbol"].tolist()))

    shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    main()
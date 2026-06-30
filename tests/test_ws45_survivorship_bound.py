"""WS4.5 decision-gate test for bounded survivorship mitigation."""

from __future__ import annotations

from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.universe.builder import PointInTimeUniverse


LUNA_DATE = "2022-05-01"
FTT_DATE = "2022-06-01"


def run_ws45_decision_gate_test() -> str:
    universe = PointInTimeUniverse()
    membership = universe.membership_table()
    latest_date = membership["date"].max().strftime("%Y-%m-%d")

    luna_rows = universe.as_of(LUNA_DATE)
    ftt_rows = universe.as_of(FTT_DATE)
    latest_rows = universe.as_of(latest_date)
    luna_symbols = {row.symbol for row in luna_rows}
    ftt_symbols = {row.symbol for row in ftt_rows}
    latest_symbols = {row.symbol for row in latest_rows}
    dropped_from_luna_date = sorted(luna_symbols - latest_symbols)
    dropped_from_ftt_date = sorted(ftt_symbols - latest_symbols)
    added_since_ftt_date = sorted(latest_symbols - ftt_symbols)

    assert "LUNAUSDT" in luna_symbols, "LUNAUSDT must appear in the pre-collapse 2022 universe"
    assert "LUNAUSDT" not in latest_symbols, "LUNAUSDT must be absent from the latest universe"
    assert "FTTUSDT" in ftt_symbols, "FTTUSDT must appear in the 2022-06-01 universe"
    assert "FTTUSDT" not in latest_symbols, "FTTUSDT must be absent from the latest universe"
    assert dropped_from_luna_date, "Expected at least one real dropout from pre-collapse 2022 to latest date"
    assert dropped_from_ftt_date, "Expected at least one real dropout from 2022-06-01 to latest date"

    luna_ranked = [row.symbol for row in luna_rows]
    ftt_ranked = [row.symbol for row in ftt_rows]
    latest_ranked = [row.symbol for row in latest_rows]

    return "\n".join(
        [
            "WS4.5 decision gate passed",
            f"luna_pre_collapse_date={LUNA_DATE}",
            f"ftt_comparison_date={FTT_DATE}",
            f"latest_date={latest_date}",
            f"luna_date_count={len(luna_ranked)}",
            f"ftt_date_count={len(ftt_ranked)}",
            f"latest_count={len(latest_ranked)}",
            f"dropped_luna_date_to_latest={dropped_from_luna_date}",
            f"dropped_ftt_date_to_latest={dropped_from_ftt_date}",
            f"added_latest_not_in_ftt_date={added_since_ftt_date}",
            f"luna_date_top30={luna_ranked}",
            f"ftt_date_top30={ftt_ranked}",
            f"latest_top30={latest_ranked}",
        ]
    )


def test_ws45_real_delisted_symbols_create_turnover() -> None:
    output = run_ws45_decision_gate_test()
    assert "LUNAUSDT" in output
    assert "FTTUSDT" in output


if __name__ == "__main__":
    print(run_ws45_decision_gate_test())

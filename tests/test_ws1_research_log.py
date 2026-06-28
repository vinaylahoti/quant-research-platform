"""
WS1 decision-gate test.

The gate says:
- logging an experiment auto-writes a row
- we can answer "how many configurations have I tried?" with one query
"""

from __future__ import annotations

from pathlib import Path
import shutil
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.research_log.metadata import compute_data_snapshot_id, resolve_git_commit
from src.research_log.store import ResearchLog, how_many_trials, log_experiment


def run_ws1_decision_gate_test() -> str:
    """Write a couple of experiments and prove the trial counter works."""

    temp_root = Path("tests/.tmp_ws1")
    if temp_root.exists():
        shutil.rmtree(temp_root)
    temp_root.mkdir(parents=True, exist_ok=True)

    db_path = temp_root / "research_log.db"
    sample_snapshot_path = temp_root / "snapshot_marker.txt"
    sample_snapshot_path.write_text("phase1-featurestore-snapshot", encoding="utf-8")

    git_commit = resolve_git_commit()
    data_snapshot_id = compute_data_snapshot_id(paths=[sample_snapshot_path])
    universe_definition = "top30-usdtm-fixed-v1"

    first_trial = log_experiment(
        git_commit=git_commit,
        data_snapshot_id=data_snapshot_id,
        universe_definition=universe_definition,
        params={"signal": "plumbing-check", "timeframe": "15m"},
        metrics={"profit_factor": 1.23, "max_drawdown_pct": 4.2},
        db_path=db_path,
    )
    second_trial = log_experiment(
        git_commit=git_commit,
        data_snapshot_id=data_snapshot_id,
        universe_definition=universe_definition,
        params={"signal": "plumbing-check", "timeframe": "1h"},
        metrics={"profit_factor": 1.11, "max_drawdown_pct": 5.0},
        db_path=db_path,
    )

    log = ResearchLog(db_path=db_path)
    rows = log.fetch_all()
    trial_count = how_many_trials(db_path=db_path)

    assert first_trial == 1, f"Expected first trial number to be 1, got {first_trial}"
    assert second_trial == 2, f"Expected second trial number to be 2, got {second_trial}"
    assert trial_count == 2, f"Expected 2 total trials, got {trial_count}"
    assert len(rows) == 2, f"Expected 2 persisted rows, got {len(rows)}"

    return (
        "WS1 decision gate passed | "
        f"trial_numbers={[row['trial_number'] for row in rows]} | "
        f"trial_count={trial_count} | "
        f"git_commit={rows[0]['git_commit']} | "
        f"data_snapshot_id={rows[0]['data_snapshot_id'][:12]}"
    )


if __name__ == "__main__":
    print(run_ws1_decision_gate_test())

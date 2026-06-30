"""WS6 one-signal end-to-end decision-gate proof."""

from __future__ import annotations

from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.signals.ws6_one_signal import run_ws6_one_signal_decision_gate


def test_ws6_one_signal_decision_gate() -> None:
    report = run_ws6_one_signal_decision_gate()
    assert report["determinism"]["passed"]
    assert report["pipeline_end_to_end"]
    assert not report["manual_patching_between_stages"]
    assert report["new_trials_logged"] == 2
    assert report["trial_count_after"] >= report["trial_count_before"] + 2
    assert not report["random_control"]["clears_deflated_pf_gt_1_5"]


if __name__ == "__main__":
    result = run_ws6_one_signal_decision_gate()
    print("WS6 one-signal decision gate passed")
    print(f"signal_definition={result['signal_definition']}")
    print(f"determinism={result['determinism']}")
    print(f"pipeline_end_to_end={result['pipeline_end_to_end']}")
    print(f"manual_patching_between_stages={result['manual_patching_between_stages']}")
    print(f"data_source={result['data_source']}")
    print(f"universe_source={result['universe_source']}")
    print(f"research_log={result['research_log']}")
    print(f"symbols_used={result['symbols_used']}")
    print(f"load_diagnostics={result['load_diagnostics']}")
    print(f"sample_count={result['sample_count']}")
    print(f"date_range={result['date_range']}")
    print(f"random_control={result['random_control']}")
    print(f"momentum={result['momentum']}")
    print(f"trial_count_before={result['trial_count_before']}")
    print(f"trial_count_after={result['trial_count_after']}")
    print(f"new_trials_logged={result['new_trials_logged']}")
    print(f"cleared_deflated_pf_bar={result['cleared_deflated_pf_bar']}")

"""
Dead-man's switch confirmation test.

Part 1: sends a direct test alert via SMTP to confirm credentials work.
Part 2: starts the HeartbeatThread with a 15-second timeout (instead of 2h),
        does NOT write a heartbeat, waits for the thread to fire the alert,
        then confirms it fired and exits.

Run with:
    py scripts/test_heartbeat.py

Expected output:
    [part1] Sending direct test alert...
    [part1] SMTP send returned without error - check inbox now
    [part2] Starting heartbeat thread with 15s timeout (no heartbeat will be written)
    [part2] Waiting up to 40s for watchdog to fire...
    [part2] Alert fired at <timestamp>
    [part2] PASS - dead-man's switch confirmed working

After this test passes, delete this script's output from your inbox if desired.
"""

from __future__ import annotations

import sys
import time
import threading
import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# ------------------------------------------------------------------ Part 1
print("[part1] Sending direct test alert...")

from src.paper_trading.heartbeat import send_alert

send_alert(
    subject="TEST - direct SMTP confirmation",
    body=(
        f"This is a direct test of the WS9 dead-man's switch SMTP path.\n"
        f"Sent at UTC: {datetime.datetime.utcnow().isoformat()}\n\n"
        "If you see this email the SMTP credentials are correct.\n"
        "Part 2 of the test will send a second alert via the watchdog thread."
    ),
)
print("[part1] SMTP send returned without error - check inbox now")
print()

# ------------------------------------------------------------------ Part 2
print("[part2] Starting heartbeat thread with 15s timeout (no heartbeat will be written)")

import src.paper_trading.heartbeat as hb_module

# Patch the module-level timeout for this test only.
original_timeout = hb_module._TIMEOUT_SECONDS
hb_module._TIMEOUT_SECONDS = 15

alert_fired_at: list[str] = []
original_send = hb_module.send_alert

def _capturing_send(subject: str, body: str) -> None:
    ts = datetime.datetime.utcnow().isoformat()
    alert_fired_at.append(ts)
    print(f"[part2] Alert fired at {ts}")
    print(f"[part2]   subject: {subject}")
    original_send(subject, body)

hb_module.send_alert = _capturing_send

thread = hb_module.HeartbeatThread()
thread.start()

print("[part2] Waiting up to 40s for watchdog to fire...")
deadline = time.time() + 40
while time.time() < deadline:
    if alert_fired_at:
        break
    time.sleep(1)

thread.stop()
# Give the daemon thread up to 20s to finish its in-flight SMTP send before
# the main thread exits and kills it. Without this, sys.exit() races the
# smtplib call and the connection gets torn down before send_message() returns.
thread.join(timeout=20)
hb_module._TIMEOUT_SECONDS = original_timeout
hb_module.send_alert = original_send

if alert_fired_at:
    print("[part2] PASS - dead-man's switch confirmed working")
    sys.exit(0)
else:
    print("[part2] FAIL - watchdog did not fire within 40s")
    sys.exit(1)

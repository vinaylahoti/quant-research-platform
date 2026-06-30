"""
Dead-man's switch for WS9 paper trading.

Two alert triggers:
  1. The main loop goes silent for > HEARTBEAT_TIMEOUT_MULTIPLIER x REBALANCE_INTERVAL_SECONDS.
  2. An uncaught exception escapes the main loop's top-level handler.

Alerting mechanism: Resend HTTP API (https://resend.com).
Railway blocks outbound SMTP (ports 465/587), so raw smtplib cannot be used
from a deployed container. Resend sends email over HTTPS, which Railway allows.

Required environment variables:
  RESEND_API_KEY   - Resend API key (set in Railway Variables, never committed)
  ALERT_TO_EMAIL   - destination address for all alerts

The from address is onboarding@resend.dev (Resend's shared sender, works on
the free tier without domain verification). To use your own domain as sender,
verify it in the Resend dashboard and set ALERT_FROM_EMAIL accordingly.

Confirmation procedure (required before first live run):
  1. Start the scheduler with write_heartbeat() commented out.
  2. Confirm an alert email arrives at ALERT_TO_EMAIL within one timeout window.
  3. Restore write_heartbeat() and redeploy.
"""

from __future__ import annotations

import datetime
import os
import threading
import time

import requests

from src.paper_trading.config import (
    HEARTBEAT_PATH,
    HEARTBEAT_TIMEOUT_MULTIPLIER,
    REBALANCE_INTERVAL_SECONDS,
)

_TIMEOUT_SECONDS = REBALANCE_INTERVAL_SECONDS * HEARTBEAT_TIMEOUT_MULTIPLIER

_RESEND_API_URL = "https://api.resend.com/emails"
_DEFAULT_FROM = "onboarding@resend.dev"


def _load_env() -> tuple[str, str, str]:
    """Read RESEND_API_KEY, ALERT_TO_EMAIL, ALERT_FROM_EMAIL from environment."""
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    api_key = os.environ.get("RESEND_API_KEY", "")
    recipient = os.environ.get("ALERT_TO_EMAIL", "")
    sender = os.environ.get("ALERT_FROM_EMAIL", _DEFAULT_FROM)
    return api_key, recipient, sender


def send_alert(subject: str, body: str) -> None:
    """
    Send an email alert via Resend's HTTP API.

    Logs success or failure to stdout. Never raises — a failed alert must not
    crash the scheduler or the heartbeat thread.
    """
    api_key, recipient, sender = _load_env()

    if not api_key or not recipient:
        print(
            f"[heartbeat] ALERT (Resend not configured): {subject}\n{body}",
            flush=True,
        )
        return

    payload = {
        "from": sender,
        "to": [recipient],
        "subject": f"[WS9 paper-trading] {subject}",
        "text": body,
    }

    try:
        resp = requests.post(
            _RESEND_API_URL,
            json=payload,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=15,
        )
        resp.raise_for_status()
        email_id = resp.json().get("id", "unknown")
        print(
            f"[heartbeat] Alert email sent to {recipient} via Resend "
            f"(id={email_id}): {subject}",
            flush=True,
        )
    except Exception as exc:
        print(f"[heartbeat] FAILED to send alert email: {repr(exc)}", flush=True)


def write_heartbeat() -> None:
    """Called by the main loop on every successful or gracefully-skipped tick."""
    HEARTBEAT_PATH.parent.mkdir(parents=True, exist_ok=True)
    HEARTBEAT_PATH.write_text(str(time.time()))


def _last_heartbeat_age() -> float:
    """Seconds since the last heartbeat write, or infinity if no file exists."""
    try:
        written_at = float(HEARTBEAT_PATH.read_text().strip())
        return time.time() - written_at
    except (FileNotFoundError, ValueError):
        return float("inf")


class HeartbeatThread(threading.Thread):
    """
    Background thread that fires an alert if the main loop goes silent.

    Start with start(); stop by setting _stop_event before joining.
    """

    def __init__(self) -> None:
        super().__init__(daemon=True, name="heartbeat-watchdog")
        self._stop_event = threading.Event()

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        # Wait one full timeout before the first check so startup doesn't
        # immediately trigger a false alert.
        self._stop_event.wait(timeout=_TIMEOUT_SECONDS)

        while not self._stop_event.is_set():
            age = _last_heartbeat_age()
            if age > _TIMEOUT_SECONDS:
                send_alert(
                    subject=f"SILENT for {age/3600:.1f}h - check immediately",
                    body=(
                        f"No heartbeat received for {age:.0f}s "
                        f"(threshold {_TIMEOUT_SECONDS}s).\n"
                        f"UTC: {datetime.datetime.now(datetime.timezone.utc).isoformat()}\n"
                        "The paper-trading scheduler may have crashed or stalled."
                    ),
                )
            self._stop_event.wait(timeout=_TIMEOUT_SECONDS)

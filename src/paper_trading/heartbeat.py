"""
Dead-man's switch for WS9 paper trading.

Two alert triggers:
  1. The main loop goes silent for > 2 × REBALANCE_INTERVAL_SECONDS.
  2. An uncaught exception escapes the main loop's top-level handler.

Alerting mechanism: Gmail SMTP to the address in ALERT_TO_EMAIL (.env).
Requires GMAIL_APP_PASSWORD set in .env (an App Password, not the account
password — create one at myaccount.google.com → Security → App passwords).

Confirmation procedure (required before first live run):
  1. Start the heartbeat thread.
  2. Comment out write_heartbeat() in the scheduler for one interval.
  3. Confirm an alert email arrives.
  4. Restore write_heartbeat() and restart.
"""

from __future__ import annotations

import os
import smtplib
import threading
import time
from email.message import EmailMessage
from pathlib import Path

from src.paper_trading.config import (
    HEARTBEAT_PATH,
    HEARTBEAT_TIMEOUT_MULTIPLIER,
    REBALANCE_INTERVAL_SECONDS,
)

_TIMEOUT_SECONDS = REBALANCE_INTERVAL_SECONDS * HEARTBEAT_TIMEOUT_MULTIPLIER


def _load_env() -> tuple[str, str, str]:
    """Read GMAIL_SENDER, GMAIL_APP_PASSWORD, ALERT_TO_EMAIL from environment."""
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass  # dotenv optional; caller can set env vars directly

    sender = os.environ.get("GMAIL_SENDER", "")
    password = os.environ.get("GMAIL_APP_PASSWORD", "")
    recipient = os.environ.get("ALERT_TO_EMAIL", "")
    return sender, password, recipient


def send_alert(subject: str, body: str) -> None:
    """Send an email alert via Gmail SMTP SSL. Logs to stderr if it fails."""
    sender, password, recipient = _load_env()
    if not all([sender, password, recipient]):
        print(
            f"[heartbeat] ALERT (email not configured): {subject}\n{body}",
            flush=True,
        )
        return

    msg = EmailMessage()
    msg["Subject"] = f"[WS9 paper-trading] {subject}"
    msg["From"] = sender
    msg["To"] = recipient
    msg.set_content(body)

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=15) as smtp:
            smtp.login(sender, password)
            smtp.send_message(msg)
        print(f"[heartbeat] Alert email sent to {recipient}: {subject}", flush=True)
    except Exception as exc:
        # repr() avoids any non-ASCII in the exception message hitting cp1252.
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
                import datetime
                send_alert(
                    subject=f"SILENT for {age/3600:.1f}h - check immediately",
                    body=(
                        f"No heartbeat received for {age:.0f}s "
                        f"(threshold {_TIMEOUT_SECONDS}s).\n"
                        f"UTC: {datetime.datetime.utcnow().isoformat()}\n"
                        "The paper-trading scheduler may have crashed or stalled."
                    ),
                )
            self._stop_event.wait(timeout=_TIMEOUT_SECONDS)

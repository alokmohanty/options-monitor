"""
Simple in-process API call counter.

Tracks Gemini API calls made today (resets at midnight IST).
Thread-safe for use from asyncio + thread executor.
"""

import threading
from datetime import date
from zoneinfo import ZoneInfo

from options_monitor import config

_lock = threading.Lock()
_today: date = date.today()
_calls_today: int = 0
_calls_total: int = 0  # since process started


def _ist_today() -> date:
    from datetime import datetime
    return datetime.now(ZoneInfo(config.MonitorConfig.timezone)).date()


def increment() -> None:
    """Call this every time a Gemini API request is made."""
    global _today, _calls_today, _calls_total
    with _lock:
        today = _ist_today()
        if today != _today:
            _today = today
            _calls_today = 0
        _calls_today += 1
        _calls_total += 1


def stats() -> tuple[int, int]:
    """Return (calls_today, calls_total_since_start)."""
    global _today, _calls_today
    with _lock:
        today = _ist_today()
        if today != _today:
            _today = today
            _calls_today = 0
        return _calls_today, _calls_total


def footer() -> str:
    """Short footer string to append to Discord messages."""
    today, total = stats()
    return f"-# 🤖 API calls today: **{today}** | since start: **{total}**"

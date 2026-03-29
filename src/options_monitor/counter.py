"""
In-process API call counter — persisted to disk so counts survive restarts.

Tracks actual HTTP requests made to the Gemini API by hooking into httpx.
This counts every real network round-trip including AFC (Automatic Function Calling)
intermediate calls — so if one `send_message()` triggers 4 AFC loops, it counts as 4.

Counts are persisted to data/counter.json and reloaded on startup.
Counters reset at IST midnight (daily) and IST month boundary (monthly).
"""

import json
import threading
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from options_monitor import config

_lock = threading.Lock()
_hooked: bool = False

# Persist to data/counter.json (relative to project root)
_DATA_FILE = Path(__file__).resolve().parents[3] / "data" / "counter.json"


def _ist_now() -> datetime:
    return datetime.now(ZoneInfo(config.MonitorConfig.timezone))


def _load() -> dict:
    """Load persisted counts from disk, or return empty defaults."""
    try:
        if _DATA_FILE.exists():
            return json.loads(_DATA_FILE.read_text())
    except Exception:
        pass
    return {"today_date": "", "month_key": "", "calls_today": 0, "calls_month": 0}


def _save(state: dict) -> None:
    """Persist counts to disk."""
    try:
        _DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
        _DATA_FILE.write_text(json.dumps(state, indent=2))
    except Exception:
        pass


def _fresh_state() -> dict:
    now = _ist_now()
    return {
        "today_date": now.date().isoformat(),
        "month_key": f"{now.year}-{now.month:02d}",
        "calls_today": 0,
        "calls_month": 0,
    }


def _refresh(state: dict) -> dict:
    """Reset counters if day or month has rolled over. Returns updated state."""
    now = _ist_now()
    today = now.date().isoformat()
    month = f"{now.year}-{now.month:02d}"

    if state.get("today_date") != today:
        state["today_date"] = today
        state["calls_today"] = 0

    if state.get("month_key") != month:
        state["month_key"] = month
        state["calls_month"] = 0

    return state


def _on_http_response(response) -> None:
    """httpx event hook: called after every completed HTTP response."""
    try:
        url = str(response.request.url)
        if "generativelanguage.googleapis.com" in url and "generateContent" in url:
            with _lock:
                state = _load()
                state = _refresh(state)
                state["calls_today"] += 1
                state["calls_month"] += 1
                _save(state)
    except Exception:
        pass


def install_http_hook() -> None:
    """
    Monkey-patch httpx.Client and httpx.AsyncClient to inject our response hook.
    Call this once at startup before creating any genai.Client instances.
    """
    global _hooked
    if _hooked:
        return

    import httpx

    _orig_client_init = httpx.Client.__init__
    _orig_async_client_init = httpx.AsyncClient.__init__

    def _patched_client_init(self, *args, **kwargs):
        hooks = kwargs.setdefault("event_hooks", {})
        hooks.setdefault("response", []).append(_on_http_response)
        _orig_client_init(self, *args, **kwargs)

    def _patched_async_client_init(self, *args, **kwargs):
        hooks = kwargs.setdefault("event_hooks", {})
        hooks.setdefault("response", []).append(_on_http_response)
        _orig_async_client_init(self, *args, **kwargs)

    httpx.Client.__init__ = _patched_client_init
    httpx.AsyncClient.__init__ = _patched_async_client_init
    _hooked = True


def stats() -> tuple[int, int]:
    """Return (calls_today, calls_this_month) — persisted across restarts."""
    with _lock:
        state = _refresh(_load())
        return state["calls_today"], state["calls_month"]


def footer() -> str:
    """Short footer string to append to Discord messages."""
    today, month = stats()
    return f"-# 🌐 `{today}` API calls today · `{month}` this month"

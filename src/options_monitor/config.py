"""Configuration loader for options-monitor."""

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

# Load .env from the project root
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_PROJECT_ROOT / ".env")


def _load_yaml(path: Path) -> dict:
    with open(path, "r") as f:
        return yaml.safe_load(f) or {}


_raw: dict = _load_yaml(_PROJECT_ROOT / "config.yaml")


# ---------------------------------------------------------------------------
# Gemini
# ---------------------------------------------------------------------------
class GeminiConfig:
    model: str = _raw.get("gemini", {}).get("model", "gemini-2.5-pro")
    temperature: float = _raw.get("gemini", {}).get("temperature", 1.0)
    max_output_tokens: int = _raw.get("gemini", {}).get("max_output_tokens", 8192)
    api_key: str = os.environ.get("GEMINI_API_KEY", "")


# ---------------------------------------------------------------------------
# Discord
# ---------------------------------------------------------------------------
class DiscordConfig:
    command_prefix: str = _raw.get("discord", {}).get("command_prefix", "!")
    bot_token: str = os.environ.get("DISCORD_BOT_TOKEN", "")
    allowed_channel_ids: list[int] = [
        int(cid) for cid in _raw.get("discord", {}).get("allowed_channel_ids", [])
    ]


# ---------------------------------------------------------------------------
# Trading bot
# ---------------------------------------------------------------------------
class TradingBotConfig:
    root_path: str = _raw.get("trading_bot", {}).get("root_path", "/home/ubuntu/options-bot")
    monitor_root_path: str = _raw.get("trading_bot", {}).get(
        "monitor_root_path", "/home/ubuntu/options-monitor"
    )
    log_file: str = _raw.get("trading_bot", {}).get(
        "log_file", "/home/ubuntu/options-bot/logs/cron_output.log"
    )
    max_log_lines: int = _raw.get("trading_bot", {}).get("max_log_lines", 500)
    code_extensions: list[str] = _raw.get("trading_bot", {}).get(
        "code_extensions", [".py", ".yaml", ".yml", ".json", ".toml", ".txt", ".md"]
    )
    exclude_dirs: list[str] = _raw.get("trading_bot", {}).get(
        "exclude_dirs",
        ["__pycache__", ".git", ".venv", "venv", "node_modules", ".pytest_cache"],
    )


# ---------------------------------------------------------------------------
# Monitor (scheduled log checks + EOD summary)
# ---------------------------------------------------------------------------
class MonitorConfig:
    _m = _raw.get("monitor", {})
    enabled: bool = _m.get("enabled", True)
    timezone: str = _m.get("timezone", "Asia/Kolkata")
    trading_start: str = _m.get("trading_start", "09:15")
    trading_end: str = _m.get("trading_end", "15:30")
    check_interval_minutes: int = _m.get("check_interval_minutes", 3)
    check_log_lines: int = _m.get("check_log_lines", 100)
    eod_summary_time: str = _m.get("eod_summary_time", "15:35")
    eod_log_lines: int = _m.get("eod_log_lines", 2000)
    eod_model: str = _m.get("eod_model", _raw.get("gemini", {}).get("model", "gemini-3-flash-preview"))
    alert_on_ok: bool = _m.get("alert_on_ok", False)


def validate() -> None:
    """Raise ValueError if required secrets are missing."""
    missing = []
    if not GeminiConfig.api_key:
        missing.append("GEMINI_API_KEY")
    if not DiscordConfig.bot_token:
        missing.append("DISCORD_BOT_TOKEN")
    if missing:
        raise ValueError(
            f"Missing required environment variables: {', '.join(missing)}. "
            "Copy env.example to .env and fill in the values."
        )

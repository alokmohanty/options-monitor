"""
Automated monitor scheduler for the options trading bot.

Two jobs run as asyncio background tasks:

1. Periodic check (every N minutes during trading hours):
   - Reads the last `check_log_lines` lines from the log
   - Uses context injection (not tool calls) for a fast single-API-call analysis
   - Posts to Discord only if issues are found (or always if alert_on_ok=true)

2. End-of-day summary (at eod_summary_time IST):
   - Reads `eod_log_lines` lines covering the full trading day
   - Posts a structured summary: orders per user, P&L, issues
"""

import asyncio
import logging
from datetime import datetime, time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

import discord
from google import genai
from google.genai import types

from options_monitor import config

logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------
# Prompts (context injection: log text goes directly into prompt)
# -----------------------------------------------------------------------

_PERIODIC_PROMPT = """\
You are monitoring a live options trading bot. Below are the last {n} lines of its log \
from {timestamp} IST.

--- LOG START ---
{log_content}
--- LOG END ---

Instructions:
- Scan for errors, exceptions, warnings, failed orders, or any unusual activity.
- If everything looks normal, respond with exactly: OK
- Otherwise provide a short bullet-point summary of issues found (max 10 lines).
- Do NOT explain what you are doing, just give the result.
"""

_EOD_PROMPT = """\
You are analysing the end-of-day log of an options trading bot for {date} IST.

--- LOG START ---
{log_content}
--- LOG END ---

Provide an end-of-day summary with the following sections:
1. **Orders Summary**: Number of orders placed per user (buy/sell breakdown if visible).
2. **P&L Summary**: P&L per user if available in the log, otherwise state "not available in logs".
3. **Issues & Errors**: List any errors, exceptions, failed orders, or warnings encountered.
4. **Overall Status**: One-line verdict (e.g. "Smooth day", "Minor issues", "Critical errors").

Be concise. Use Discord markdown formatting.
"""


# -----------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------

def _now_ist() -> datetime:
    return datetime.now(ZoneInfo(config.MonitorConfig.timezone))


def _parse_hhmm(s: str) -> dtime:
    h, m = s.split(":")
    return dtime(int(h), int(m))


def _is_trading_hours() -> bool:
    now = _now_ist().time()
    start = _parse_hhmm(config.MonitorConfig.trading_start)
    end = _parse_hhmm(config.MonitorConfig.trading_end)
    return start <= now <= end


def _read_log_lines(n: int) -> str:
    log_path = Path(config.TradingBotConfig.log_file)
    if not log_path.exists():
        return f"(log file not found at {log_path})"
    with open(log_path, "r", errors="replace") as f:
        lines = f.readlines()
    return "".join(lines[-n:]) or "(log file is empty)"


def _gemini_one_shot(prompt: str) -> str:
    """Single stateless Gemini call — no history, no tools, just text in/out."""
    client = genai.Client(api_key=config.GeminiConfig.api_key)
    response = client.models.generate_content(
        model=config.GeminiConfig.model,
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0.2,  # low temp for factual/analytical tasks
            max_output_tokens=1024,
        ),
    )
    return response.text or "(no response)"


# -----------------------------------------------------------------------
# Jobs
# -----------------------------------------------------------------------

async def _periodic_check_job(channel: discord.TextChannel) -> None:
    """Check the log every N minutes during trading hours."""
    interval = config.MonitorConfig.check_interval_minutes * 60

    while True:
        await asyncio.sleep(interval)

        if not _is_trading_hours():
            continue

        try:
            n = config.MonitorConfig.check_log_lines
            log_content = _read_log_lines(n)
            now_str = _now_ist().strftime("%Y-%m-%d %H:%M")

            prompt = _PERIODIC_PROMPT.format(
                n=n, timestamp=now_str, log_content=log_content
            )

            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, _gemini_one_shot, prompt)

            is_ok = result.strip().upper() == "OK"
            logger.info("Periodic check at %s: %s", now_str, "OK" if is_ok else "ISSUES FOUND")

            if not is_ok or config.MonitorConfig.alert_on_ok:
                header = f"📊 **Bot Monitor** `{now_str} IST`"
                if is_ok:
                    await channel.send(f"{header}\n✅ Everything looks normal.")
                else:
                    await channel.send(f"{header}\n⚠️ Issues detected:\n{result}")

        except Exception as exc:
            logger.exception("Error in periodic check job: %s", exc)


async def _eod_summary_job(channel: discord.TextChannel) -> None:
    """Post an end-of-day summary at the configured time each trading day."""
    eod_time = _parse_hhmm(config.MonitorConfig.eod_summary_time)

    while True:
        now = _now_ist()
        # Calculate seconds until next eod_time
        target = now.replace(
            hour=eod_time.hour, minute=eod_time.minute, second=0, microsecond=0
        )
        if now.time() >= eod_time:
            # Already past today's EOD time — wait until tomorrow
            from datetime import timedelta
            target = target + timedelta(days=1)

        wait_seconds = (target - now).total_seconds()
        logger.info("EOD summary scheduled in %.0f minutes", wait_seconds / 60)
        await asyncio.sleep(wait_seconds)

        try:
            n = config.MonitorConfig.eod_log_lines
            log_content = _read_log_lines(n)
            today_str = _now_ist().strftime("%d %B %Y")

            prompt = _EOD_PROMPT.format(date=today_str, log_content=log_content)

            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, _gemini_one_shot, prompt)

            logger.info("EOD summary generated for %s", today_str)
            header = f"📋 **End-of-Day Summary** — {today_str}"
            await channel.send(f"{header}\n{result}")

        except Exception as exc:
            logger.exception("Error in EOD summary job: %s", exc)
            await channel.send(f"⚠️ Failed to generate EOD summary: {exc}")


# -----------------------------------------------------------------------
# Entry point called from the Discord bot
# -----------------------------------------------------------------------

async def start_scheduler(bot: discord.Client) -> None:
    """
    Start background monitoring tasks.
    Called after the bot is ready and connected to Discord.
    """
    if not config.MonitorConfig.enabled:
        logger.info("Monitor scheduler is disabled (monitor.enabled=false)")
        return

    # Determine the channel to post to
    channel_ids = config.DiscordConfig.allowed_channel_ids
    if not channel_ids:
        logger.warning("No allowed_channel_ids configured — scheduler cannot post. "
                       "Set discord.allowed_channel_ids in config.yaml.")
        return

    channel = bot.get_channel(channel_ids[0])
    if channel is None:
        logger.warning("Could not find Discord channel ID %s", channel_ids[0])
        return

    logger.info(
        "Starting monitor scheduler | channel: #%s | interval: %dm | EOD: %s IST",
        getattr(channel, "name", channel_ids[0]),
        config.MonitorConfig.check_interval_minutes,
        config.MonitorConfig.eod_summary_time,
    )

    asyncio.create_task(_periodic_check_job(channel))
    asyncio.create_task(_eod_summary_job(channel))

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
import json
import logging
import re
from datetime import datetime, time as dtime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import discord
from google import genai
from google.genai import types

from options_monitor import config
from options_monitor import counter
from options_monitor.tools import save_journal_entry, _find_bot_pids

logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------
# Prompts (context injection: log text goes directly into prompt)
# -----------------------------------------------------------------------

_PERIODIC_PROMPT = """\
You are monitoring a live options trading bot. Below are log lines from the last \
{n} minutes (captured at {timestamp} IST).

--- LOG START ---
{log_content}
--- LOG END ---

Instructions:
- Scan for errors, exceptions, warnings, failed orders, or any unusual activity.
- If everything looks normal, respond with exactly: OK
- Otherwise list each issue as a bullet point in this format:
  • <short description> — `<exact log line>`
- Include the exact log line (or relevant excerpt) as reference for each issue.
- Max 10 bullet points. Do NOT repeat issues from older entries.
- Do NOT explain what you are doing, just give the result.
"""

_EOD_PROMPT = """\
You are an expert options trading analyst reviewing the end-of-day log of an automated \
options trading bot for {date} (IST). Extract data ONLY for user: alokrm.

Trading windows configured for this bot:
  Inner band windows:
    09:15–09:30  BOTH directions
    09:30–10:00  SHORT only
    11:00–12:00  BOTH directions
  Outer band window:
    09:15–14:45  BOTH directions
  Square-off time: 14:57

--- LOG START ---
{log_content}
--- LOG END ---

Return a SINGLE valid JSON object (no markdown fences, no extra text) with exactly this schema:
{{
  "date": "{date_key}",

  "overview": "<2-3 sentences: your inference and perspective on the day — what market \
conditions drove the outcomes, whether the strategy aligned with the market, and what \
the result means in context. Go beyond facts; offer analysis.>",

  "market_context": {{
    "trend": "bullish" | "bearish" | "sideways" | "volatile" | "unknown",
    "volatility_perception": "high" | "normal" | "low" | "unknown",
    "notes": "<brief inference on market behaviour observed from the log>"
  }},

  "overall_status": "smooth" | "minor_issues" | "critical_errors",
  "profitable": true | false | null,
  "total_pnl": <float or null>,
  "total_trades": <int>,

  "trades": [
    {{
      "instrument":       "<e.g. NIFTY 24000 CE>",
      "type":             "call" | "put",
      "strategy":         "inner_band" | "inner_band_reversal" | "outer_band" | "unknown",
      "entry_time":       "<HH:MM IST or null>",
      "exit_time":        "<HH:MM IST or null>",
      "duration_minutes": <int or null>,
      "expiry":           "<YYYY-MM-DD or null>",
      "entry_price":      <float or null>,
      "exit_price":       <float or null>,
      "quantity_lots":    <int or null>,
      "exit_reason":      "sl_hit" | "target_hit" | "protection_50pct" | "protection_70pct" | "eod_exit" | "unknown",
      "pnl":              <float or null>,
      "pnl_pct":          <float or null>,
      "setup_quality":    "good" | "average" | "poor" | null,
      "trade_notes":      "<model inference: was the setup clean? did price action confirm? any anomaly?>"
    }}
  ],

  "skipped_entries": [
    {{
      "time":                  "<HH:MM IST>",
      "strategy":              "inner_band" | "inner_band_reversal" | "outer_band" | "unknown",
      "side":                  "Long" | "Short",
      "skip_reason":           "risk_reward" | "outside_window",
      "skip_detail":           "<brief reason from log, e.g. 'RR 1:0.8 below threshold' or 'signal at 10:15 outside inner band window'>",
      "nifty_close_at_signal": <float or null, Nifty CANDLE CLOSE price at signal time — NOT live spot>,
      "potential_target_pts":  <float or null, Nifty points from signal level to target>,
      "potential_sl_pts":      <float or null, Nifty points from signal level to SL>,
      "potential_pnl_pts":     <float or null, actual Nifty points outcome if known from log — positive if target hit, negative if SL would have been hit>
    }}
  ],

  "lessons_learned": "<model’s specific inference: what worked, what didn’t, and what to watch in future trades. Reference concrete observations from the log. If skipped entries would have been profitable, note that too.>",

  "issues": ["<issue 1>", "<issue 2>"],

  "confidence_score": null
}}

Rules:
- Only include trades and skipped entries belonging to alokrm.
- skipped_entries has TWO sources:
  (1) risk_reward: the bot calculated risk/reward and rejected the entry — look for log lines mentioning RR ratio, insufficient reward, skipping signal, etc.
  (2) outside_window: a signal occurred but the time fell outside the inner/outer band windows defined above.
- For nifty_close_at_signal: use the Nifty CANDLE CLOSE price logged at or just before the signal time. Do NOT use live Nifty spot from websocket.
- For potential_pnl_pts: if the log shows subsequent price levels, infer whether target or SL was hit and calculate the Nifty points outcome.
- If a field is not present in the logs, use null or an empty list/string.
- For 'overview' and 'lessons_learned', do NOT just restate facts — draw inferences.
- Do not include any text outside the JSON object.
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
    now_dt = _now_ist()
    if now_dt.weekday() >= 5:  # 5=Saturday, 6=Sunday
        return False
    now = now_dt.time()
    start = _parse_hhmm(config.MonitorConfig.trading_start)
    end = _parse_hhmm(config.MonitorConfig.trading_end)
    return start <= now <= end


# Patterns to match common log timestamp formats, e.g.:
#   2026-03-29 13:51:00  or  2026-03-29T13:51:00  or  [2026-03-29 13:51:00]
_TS_PATTERN = re.compile(
    r"(?:^|\[)(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})"
)


def _parse_line_ts(line: str) -> datetime | None:
    """Try to parse a timestamp from a log line. Returns None if not found."""
    m = _TS_PATTERN.search(line)
    if not m:
        return None
    try:
        naive = datetime.strptime(f"{m.group(1)} {m.group(2)}", "%Y-%m-%d %H:%M:%S")
        return naive.replace(tzinfo=ZoneInfo(config.MonitorConfig.timezone))
    except ValueError:
        return None


def _read_log_lines(n: int) -> str:
    """Read last n lines (used as a last-resort fallback)."""
    log_path = Path(config.TradingBotConfig.log_file)
    if not log_path.exists():
        return f"(log file not found at {log_path})"
    with open(log_path, "r", errors="replace") as f:
        lines = f.readlines()
    return "".join(lines[-n:]) or "(log file is empty)"


def _read_log_for_date(date_key: str) -> str:
    """Return all log lines whose timestamp matches `date_key` (YYYY-MM-DD).
    Falls back to _read_log_lines() with eod_log_lines if no date-matched lines found."""
    log_path = Path(config.TradingBotConfig.log_file)
    if not log_path.exists():
        return f"(log file not found at {log_path})"
    with open(log_path, "r", errors="replace") as f:
        all_lines = f.readlines()

    matched = [line for line in all_lines if date_key in line]

    if matched:
        logger.info("EOD log filter: found %d lines for %s", len(matched), date_key)
        return "".join(matched)

    # Fallback: no lines with date stamp found — use raw line count
    logger.warning("EOD log filter: no lines matched %s — falling back to last %d lines",
                   date_key, config.MonitorConfig.eod_log_lines)
    return _read_log_lines(config.MonitorConfig.eod_log_lines)


def _read_log_since(minutes: int) -> str:
    """Return log lines whose timestamp falls within the last `minutes` minutes.
    Falls back to last 200 lines if no timestamps can be parsed."""
    log_path = Path(config.TradingBotConfig.log_file)
    if not log_path.exists():
        return f"(log file not found at {log_path})"
    with open(log_path, "r", errors="replace") as f:
        all_lines = f.readlines()

    cutoff = _now_ist() - timedelta(minutes=minutes)

    # Walk backward from the end — collect lines within the window
    recent: list[str] = []
    last_ts: datetime | None = None
    for line in reversed(all_lines):
        ts = _parse_line_ts(line)
        if ts is not None:
            last_ts = ts
        # Use the most recently seen timestamp for this line
        if last_ts is not None and last_ts < cutoff:
            break  # older than our window, stop
        recent.append(line)

    if not recent:
        # No timestamps found — fall back to last 200 lines
        return "".join(all_lines[-200:]) or "(log file is empty)"

    recent.reverse()
    return "".join(recent)


def _gemini_one_shot(prompt: str, max_tokens: int = 1024, model: str | None = None) -> str:
    """Single stateless Gemini call — no history, no tools, just text in/out."""
    client = genai.Client(api_key=config.GeminiConfig.api_key)
    response = client.models.generate_content(
        model=model or config.GeminiConfig.model,
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0.2,  # low temp for factual/analytical tasks
            max_output_tokens=max_tokens,
        ),
    )
    return response.text or "(no response)"

def _format_eod_discord(entry: dict, today_str: str) -> str:
    """Format a structured journal entry dict into a Discord message."""
    status_emoji = {"smooth": "✅", "minor_issues": "⚠️", "critical_errors": "🚨"}.get(
        entry.get("overall_status", ""), "📊"
    )
    pnl = entry.get("total_pnl")
    pnl_str = f"**P&L:** `{'%.2f' % pnl}`" if pnl is not None else "**P&L:** not available"
    profit_emoji = ("⬆️" if entry.get("profitable") else "⬇️") if entry.get("profitable") is not None else "🟡"

    mctx = entry.get("market_context", {})
    trend = mctx.get("trend", "unknown")
    vol = mctx.get("volatility_perception", "normal")
    notes = mctx.get("notes", "")

    # Header section
    pnl_val = entry.get('total_pnl')
    pnl_display = f"`{pnl_val}`" if pnl_val is not None else "`0.00` (No Trades)"
    
    lines = [
        f"{entry.get('overview', '')}\n",
        f"🗓️ **EOD Trading Summary: {today_str}**",
        f"Overall Status: {'Technical Issues' if entry.get('issues') else 'Normal Operation'} | Profitable: {'✅' if (pnl_val or 0) > 0 else '❌'}",
        "\n---",
        "\n#### 📈 Financial Overview",
        f"Total PnL: Approx {pnl_display}",
        f"Total Trades: {entry.get('total_trades', 0)}",
        f"Market Trend: {trend.capitalize()} | Volatility: {vol.capitalize()}",
    ]

    # Trades Table
    lines.append("\n#### 📉 Trade Details")
    trades = entry.get("trades", [])
    if not trades:
        lines.append("_No trades executed today._")
    else:
        lines.append("| Instrument | Type | Strategy | Exit Reason | PnL | Note |")
        lines.append("| :--- | :--- | :--- | :--- | :--- | :--- |")
        for t in trades:
            inst = t.get("instrument", "?")
            t_type = t.get("type", "?").upper()
            strat = t.get("strategy", "").replace("_", " ")
            exit_r = t.get("exit_reason", "").replace("_", " ")
            pnl = t.get("pnl", 0)
            note = t.get("note", "") or t.get("setup_quality", "")
            lines.append(f"| {inst} | {t_type} | {strat} | {exit_r} | {pnl} | {note} |")

    # Issues section
    issues = entry.get("issues", [])
    if issues:
        lines.append("\n#### ⚠️ Issues & Observations")
        lines.extend(f"• {i}" for i in issues)

    # Lessons & Market Notes
    lines.append("\n#### 💡 Lessons & Observations")
    if entry.get("lessons_learned"):
        lines.append(entry.get("lessons_learned"))
    if notes:
        lines.append(f"\n_Market Note: {notes}_")

    # Skipped entries (optional technical block)
    skipped = entry.get("skipped_entries", [])
    if skipped:
        lines.append(f"\n\u23ed\ufe0f **Skipped Signals ({len(skipped)})**")
        # Keep this compact as it's secondary
        for s in skipped:
            lines.append(f"\u25ab\ufe0f `{s.get('time', '?')}` {s.get('side', '?')} {s.get('strategy', '')} | {s.get('skip_reason', '')}")

    lines.append("\n---")
    lines.append("\nSummary for Journal:")
    lines.append(f"> \"{entry.get('overview', '')}\"")

    return "\n".join(lines)


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

        if not _find_bot_pids():
            logger.info("Periodic check skipped — trading bot is not running")
            continue

        try:
            minutes = config.MonitorConfig.check_interval_minutes
            log_content = _read_log_since(minutes)
            now_str = _now_ist().strftime("%Y-%m-%d %H:%M")

            prompt = _PERIODIC_PROMPT.format(
                n=minutes, timestamp=now_str, log_content=log_content
            )

            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, _gemini_one_shot, prompt)

            is_ok = result.strip().upper() == "OK"
            logger.info("Periodic check at %s: %s", now_str, "OK" if is_ok else "ISSUES FOUND")

            if not is_ok or config.MonitorConfig.alert_on_ok:
                foot = counter.footer()
                if is_ok:
                    await channel.send(f"🟢 **Bot Monitor** `{now_str} IST`\n{foot}")
                else:
                    await channel.send(f"🟠 **Bot Monitor** `{now_str} IST`\n{result}\n{foot}")

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
            target = target + timedelta(days=1)

        wait_seconds = (target - now).total_seconds()
        logger.info("EOD summary scheduled in %.0f minutes", wait_seconds / 60)
        await asyncio.sleep(wait_seconds)

        try:
            # Skip weekends
            if _now_ist().weekday() >= 5:
                logger.info("EOD summary skipped — weekend")
                continue

            today_str = _now_ist().strftime("%d %B %Y")
            date_key = _now_ist().strftime("%Y-%m-%d")
            log_content = _read_log_for_date(date_key)

            prompt = _EOD_PROMPT.format(
                date=today_str, date_key=date_key, log_content=log_content
            )

            loop = asyncio.get_event_loop()
            raw = await loop.run_in_executor(
                None,
                lambda: _gemini_one_shot(
                    prompt,
                    max_tokens=8192,
                    model=config.MonitorConfig.eod_model,
                ),
            )

            # Parse structured JSON and persist to journal
            entry: dict = {}
            discord_body: str = ""
            try:
                # Strip accidental markdown fences if any
                clean = re.sub(r"^```[\w]*\n?", "", raw.strip(), flags=re.MULTILINE)
                clean = re.sub(r"```$", "", clean.strip())
                entry = json.loads(clean)
                save_journal_entry(date_key, entry)
                discord_body = _format_eod_discord(entry, today_str)
            except (json.JSONDecodeError, Exception) as parse_err:
                logger.warning("EOD JSON parse failed: %s", parse_err)
                # Fallback: alert with error, save raw under 'raw' key so journal isn't empty
                discord_body = (
                    f"⚠️ **EOD JSON parse failed** — model did not return valid JSON.\n"
                    f"Use `!retrigger-eod {date_key}` to retry.\n"
                    f"Parse error: `{parse_err}`\n\n"
                    f"Raw model output:\n{raw[:800]}"
                )
                save_journal_entry(date_key, {"date": date_key, "raw": raw})

            logger.info("EOD summary generated and saved for %s", date_key)
            header = f"📋 **End-of-Day Summary — alokrm** | {today_str}"
            foot = counter.footer()
            await channel.send(f"{header}\n{discord_body}\n{foot}")

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

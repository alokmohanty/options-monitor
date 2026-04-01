"""
Tools available to the Gemini agent for inspecting the trading bot.

Each function is exposed as a callable tool via the Gemini function-calling API.
"""

import os
import signal
import subprocess
from pathlib import Path

from options_monitor import config


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _safe_path(path: str) -> Path:
    """Resolve path and ensure it doesn't escape the bot root."""
    root = Path(config.TradingBotConfig.root_path).resolve()
    resolved = (root / path).resolve() if not Path(path).is_absolute() else Path(path).resolve()
    # Allow paths under root OR the explicitly configured log file
    log = Path(config.TradingBotConfig.log_file).resolve()
    if not (str(resolved).startswith(str(root)) or resolved == log):
        raise PermissionError(f"Access denied: path '{resolved}' is outside the allowed scope.")
    return resolved


# ---------------------------------------------------------------------------
# Tool functions
# ---------------------------------------------------------------------------

def read_log(last_n_lines: int = 1000, search_keyword: str = "") -> str:
    """
    Read the trading bot log file.

    Args:
        last_n_lines: Number of lines to read from the end of the log (default 1000, covers ~2 days).
        search_keyword: If provided, return only lines containing this keyword (case-insensitive).

    Returns:
        The requested log content as a string.
    """
    log_path = Path(config.TradingBotConfig.log_file)
    if not log_path.exists():
        return f"Log file not found at {log_path}"

    max_lines = min(last_n_lines, config.TradingBotConfig.max_log_lines)

    with open(log_path, "r", errors="replace") as f:
        lines = f.readlines()

    lines = lines[-max_lines:]

    if search_keyword:
        keyword_lower = search_keyword.lower()
        lines = [l for l in lines if keyword_lower in l.lower()]
        if not lines:
            return f"No lines found containing '{search_keyword}' in the last {max_lines} log lines."

    return "".join(lines) or "(log file is empty)"


def search_errors_in_log(last_n_lines: int = 500) -> str:
    """
    Scan the log file for error and exception lines.

    Args:
        last_n_lines: Number of lines from the end to inspect (default 500).

    Returns:
        All error/exception lines found, or a message if none are present.
    """
    error_keywords = ["error", "exception", "traceback", "critical", "fatal", "failed"]
    log_path = Path(config.TradingBotConfig.log_file)
    if not log_path.exists():
        return f"Log file not found at {log_path}"

    max_lines = min(last_n_lines, config.TradingBotConfig.max_log_lines)

    with open(log_path, "r", errors="replace") as f:
        lines = f.readlines()

    lines = lines[-max_lines:]
    error_lines = [l for l in lines if any(kw in l.lower() for kw in error_keywords)]

    if not error_lines:
        return f"No errors found in the last {max_lines} log lines."

    return f"Found {len(error_lines)} error-related lines:\n" + "".join(error_lines)


def list_bot_files(sub_path: str = "") -> str:
    """
    List files and directories inside the trading bot root (or a sub-path within it).

    Args:
        sub_path: Relative path within the bot root to list (default: list the root).

    Returns:
        A directory tree listing.
    """
    root = Path(config.TradingBotConfig.root_path)
    target = (root / sub_path).resolve() if sub_path else root.resolve()

    if not target.exists():
        return f"Path does not exist: {target}"
    if not target.is_dir():
        return f"Not a directory: {target}"

    exclude = set(config.TradingBotConfig.exclude_dirs)
    lines: list[str] = []

    def _walk(path: Path, prefix: str = "") -> None:
        try:
            entries = sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name))
        except PermissionError:
            return
        for entry in entries:
            if entry.name in exclude:
                continue
            connector = "├── " if entry != entries[-1] else "└── "
            lines.append(f"{prefix}{connector}{entry.name}{'/' if entry.is_dir() else ''}")
            if entry.is_dir():
                extension = "│   " if entry != entries[-1] else "    "
                _walk(entry, prefix + extension)

    lines.append(str(target))
    _walk(target)
    return "\n".join(lines)


def read_bot_file(file_path: str) -> str:
    """
    Read the contents of a file from within the trading bot directory.

    Args:
        file_path: Path to the file. Can be relative to the bot root or absolute.

    Returns:
        The file contents as a string.
    """
    try:
        resolved = _safe_path(file_path)
    except PermissionError as e:
        return str(e)

    if not resolved.exists():
        return f"File not found: {resolved}"
    if not resolved.is_file():
        return f"Not a file: {resolved}"

    ext = resolved.suffix.lower()
    allowed = config.TradingBotConfig.code_extensions
    if allowed and ext not in allowed:
        return (
            f"Reading '{ext}' files is not allowed. "
            f"Allowed extensions: {', '.join(allowed)}"
        )

    try:
        with open(resolved, "r", errors="replace") as f:
            content = f.read()
        return content or "(file is empty)"
    except Exception as e:
        return f"Error reading file: {e}"


def search_in_bot_code(keyword: str, file_extension: str = ".py") -> str:
    """
    Search for a keyword across all code files of a specific extension in the bot directory.

    Args:
        keyword: The text to search for (case-insensitive).
        file_extension: File extension to filter (default '.py').

    Returns:
        Matching lines with file paths and line numbers.
    """
    root = Path(config.TradingBotConfig.root_path)
    if not root.exists():
        return f"Bot root directory not found: {root}"

    exclude = set(config.TradingBotConfig.exclude_dirs)
    keyword_lower = keyword.lower()
    results: list[str] = []

    for dirpath, dirnames, filenames in os.walk(root):
        # Prune excluded directories in-place
        dirnames[:] = [d for d in dirnames if d not in exclude]
        for fname in filenames:
            if not fname.endswith(file_extension):
                continue
            fpath = Path(dirpath) / fname
            try:
                with open(fpath, "r", errors="replace") as f:
                    for lineno, line in enumerate(f, 1):
                        if keyword_lower in line.lower():
                            rel = fpath.relative_to(root)
                            results.append(f"{rel}:{lineno}: {line.rstrip()}")
            except Exception:
                continue

    if not results:
        return f"No matches for '{keyword}' in *{file_extension} files."

    header = f"Found {len(results)} match(es) for '{keyword}' in *{file_extension} files:\n"
    return header + "\n".join(results)


# ---------------------------------------------------------------------------
# Process control tools
# ---------------------------------------------------------------------------

# Journal: single JSON file, all dates, at data/journal.json inside project root
_JOURNAL_FILE = Path(__file__).resolve().parents[2] / "data" / "journal.json"


def save_journal_entry(date_str: str, entry: dict) -> None:
    """
    Persist a structured EOD journal entry for `date_str` (YYYY-MM-DD).
    Merges into the single data/journal.json file — one key per trading day.
    Called by the scheduler after generating the EOD report.
    """
    import json as _json
    import logging
    _log = logging.getLogger(__name__)
    try:
        _JOURNAL_FILE.parent.mkdir(parents=True, exist_ok=True)
        data: dict = {}
        if _JOURNAL_FILE.exists():
            try:
                data = _json.loads(_JOURNAL_FILE.read_text())
            except Exception:
                data = {}
        data[date_str] = entry
        _JOURNAL_FILE.write_text(_json.dumps(data, indent=2, ensure_ascii=False))
    except Exception as e:
        _log.warning("Failed to save journal entry: %s", e)


def read_journal(query: str = "") -> str:
    """
    Read the trading journal for alokrm. All entries are in a single JSON file.

    Args:
        query: How to query the journal:
          - Leave empty or 'latest'     → most recent entry
          - 'list'                       → list all available dates
          - 'all'                        → full journal as JSON (for aggregate analysis)
          - 'YYYY-MM-DD'                 → single date entry
          - 'YYYY-MM-DD:YYYY-MM-DD'      → date range (e.g. '2026-02-01:2026-02-28')

    Returns:
        JSON string with the requested entries, or an informative message.
    """
    import json as _json

    if not _JOURNAL_FILE.exists():
        return "📓 No journal entries yet — first entry saved after market close."

    try:
        data: dict = _json.loads(_JOURNAL_FILE.read_text())
    except Exception as e:
        return f"❌ Could not read journal: {e}"

    if not data:
        return "📓 Journal is empty."

    q = query.strip().lower()

    if not q or q == "latest":
        latest_date = sorted(data.keys())[-1]
        return f"*(Most recent: `{latest_date}`)*\n\n```json\n{_json.dumps({latest_date: data[latest_date]}, indent=2)}\n```"

    if q == "list":
        dates = sorted(data.keys(), reverse=True)
        return "📓 **Available journal entries:**\n" + "\n".join(f"• `{d}`" for d in dates)

    if q == "all":
        return f"```json\n{_json.dumps(data, indent=2)}\n```"

    if ":" in query.strip():
        # Date range
        parts = query.strip().split(":")
        if len(parts) == 2:
            start, end = parts[0].strip(), parts[1].strip()
            subset = {d: v for d, v in data.items() if start <= d <= end}
            if not subset:
                return f"❌ No journal entries between `{start}` and `{end}`."
            return f"```json\n{_json.dumps(subset, indent=2)}\n```"

    # Single date
    date_key = query.strip()
    if date_key in data:
        return f"```json\n{_json.dumps({date_key: data[date_key]}, indent=2)}\n```"

    available = ", ".join(sorted(data.keys())[-5:])
    return f"❌ No journal entry for `{date_key}`. Most recent: {available}"


def _find_bot_pids() -> list[tuple[int, str]]:
    """Return list of (pid, cmd_excerpt) for running trading bot processes.
    Matches only processes whose command line contains both the bot root path
    and 'src/main.py' — guaranteed not to match the options-monitor process.
    """
    root = str(Path(config.TradingBotConfig.root_path).resolve())
    try:
        result = subprocess.run(["ps", "aux"], capture_output=True, text=True, timeout=10)
    except Exception:
        return []

    # Match on the bot's own venv python — fully unique, never matches options-monitor
    bot_python = str(Path(config.TradingBotConfig.root_path).resolve() / ".venv" / "bin" / "python")

    found: list[tuple[int, str]] = []
    for line in result.stdout.splitlines():
        if bot_python in line and "src/main.py" in line and "grep" not in line:
            parts = line.split()
            try:
                pid = int(parts[1])
            except (IndexError, ValueError):
                continue
            cmd = " ".join(parts[10:])[:120] if len(parts) > 10 else line[:120]
            found.append((pid, cmd))
    return found


def get_trading_bot_status() -> str:
    """
    Check whether the trading bot is currently running by inspecting the process list.

    Looks for Python processes whose command line includes the bot's main script
    (src/main.py) running from the bot root directory.

    Returns:
        A status message with PID(s) if running, or confirmation it is stopped.
    """
    matches = _find_bot_pids()
    if matches:
        lines = "\n".join(f"• PID `{pid}` — `{cmd}`" for pid, cmd in matches)
        return f"✅ Trading bot is **running** ({len(matches)} process{'es' if len(matches) > 1 else ''}):\n{lines}"
    else:
        return "🔴 Trading bot is **not running** — no matching processes found."


def kill_trading_bot() -> str:
    """
    Kill all running trading bot processes.

    Finds processes by matching the bot root path + 'src/main.py' in their
    command line — surgical targeting that will never affect the options-monitor
    service or any other process.

    Returns:
        Summary of killed PIDs, or a message if nothing was running.
    """
    pids = _find_bot_pids()
    if not pids:
        return "ℹ️ No trading bot processes found — nothing to kill."

    killed: list[str] = []
    errors: list[str] = []
    for pid, cmd in pids:
        try:
            os.kill(pid, signal.SIGTERM)
            killed.append(f"PID `{pid}`")
        except ProcessLookupError:
            errors.append(f"PID `{pid}` — already gone")
        except PermissionError:
            errors.append(f"PID `{pid}` — permission denied")
        except Exception as e:
            errors.append(f"PID `{pid}` — {e}")

    lines: list[str] = []
    if killed:
        lines.append(f"✅ Sent SIGTERM to {len(killed)} process{'es' if len(killed) > 1 else ''}: {', '.join(killed)}")
    if errors:
        lines.append("⚠️ " + "; ".join(errors))
    return "\n".join(lines)


def restart_trading_bot() -> str:
    """
    Kill existing trading bot processes and start fresh with nohup (detached).

    This mirrors what the cron job does:
    1. Runs kill_options_processes.py to stop any running instances.
    2. Starts src/main.py detached via a new session (nohup-equivalent),
       appending stdout/stderr to logs/cron_output.log.

    Returns:
        Status message including the new process PID, or an error message.
    """
    root = Path(config.TradingBotConfig.root_path)
    python = root / ".venv" / "bin" / "python"
    main_script = root / "src" / "main.py"
    log_file = Path(config.TradingBotConfig.log_file)

    if not main_script.exists():
        return f"Main script not found at {main_script}"
    if not python.exists():
        return f"Python not found at {python}"

    # Step 1: kill existing processes
    kill_result = kill_trading_bot()

    # Step 2: wait briefly for processes to exit cleanly
    import time
    time.sleep(3)

    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        log_handle = open(log_file, "a")
        proc = subprocess.Popen(
            [str(python), str(main_script)],
            cwd=str(root),
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,  # detach — survives options-monitor restarts
        )
        from datetime import datetime
        with open(log_file, "a") as f:
            f.write(f"\n[options-monitor restart at {datetime.now().isoformat()} — PID {proc.pid}]\n")
        return (
            f"✅ Trading bot restarted.\n"
            f"• Kill step: {kill_result.splitlines()[0]}\n"
            f"• New PID: `{proc.pid}`\n"
            f"• Log: `{log_file}`"
        )
    except Exception as e:
        return f"❌ Error starting trading bot: {e}"


def deploy_trading_bot() -> str:
    """
    Deploy the latest code for the trading bot (options-bot).
    Runs 'git pull' in the bot directory.
    """
    root = Path(config.TradingBotConfig.root_path)
    if not root.exists():
        return f"❌ Bot directory not found: {root}"

    try:
        result = subprocess.run(
            ["git", "pull"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            return f"✅ **options-bot** deployed successfully:\n```\n{result.stdout or 'Already up to date.'}\n```"
        else:
            return f"❌ Error deploying **options-bot**:\n```\n{result.stderr}\n```"
    except Exception as e:
        return f"❌ Exception during deploy: {e}"


def deploy_monitor() -> str:
    """
    Deploy the latest code for the monitor (options-monitor).
    Runs 'git pull', 'uv sync', and 'sudo systemctl restart options-monitor'.
    """
    root = Path(config.TradingBotConfig.monitor_root_path)
    if not root.exists():
        return f"❌ Monitor directory not found: {root}"

    outputs: list[str] = []

    # 1. git pull
    try:
        res = subprocess.run(["git", "pull"], cwd=str(root), capture_output=True, text=True, timeout=30)
        outputs.append(f"• **git pull**: {'OK' if res.returncode == 0 else 'Error'}\n```\n{res.stdout or res.stderr}\n```")
        if res.returncode != 0:
            return f"❌ Deploy failed at **git pull**:\n\n" + "\n".join(outputs)
    except Exception as e:
        return f"❌ Exception during git pull: {e}"

    # 2. uv sync
    try:
        res = subprocess.run(["uv", "sync"], cwd=str(root), capture_output=True, text=True, timeout=60)
        outputs.append(f"• **uv sync**: {'OK' if res.returncode == 0 else 'Error'}\n```\n{res.stdout or res.stderr}\n```")
        if res.returncode != 0:
            return f"❌ Deploy failed at **uv sync**:\n\n" + "\n".join(outputs)
    except Exception as e:
        return f"❌ Exception during uv sync: {e}"

    # 3. sudo systemctl restart options-monitor
    try:
        # Note: This might cause the current process to terminate!
        # If it terminates, the response might not be sent.
        # But systemctl restart usually happens asynchronously or finishes after start.
        res = subprocess.run(
            ["sudo", "systemctl", "restart", "options-monitor"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        outputs.append(f"• **systemctl restart**: {'OK' if res.returncode == 0 else 'Error'}")
        if res.returncode != 0:
            outputs[-1] += f"\n```\n{res.stderr}\n```"
            return f"❌ Deploy failed at **systemctl restart**:\n\n" + "\n".join(outputs)
    except Exception as e:
        return f"❌ Exception during systemctl restart: {e}"

    return "✅ **options-monitor** deployed and restarted successfully!\n\n" + "\n".join(outputs)



# ---------------------------------------------------------------------------
# Tool registry used by the agent
# ---------------------------------------------------------------------------

TOOLS = [
    read_log,
    search_errors_in_log,
    list_bot_files,
    read_bot_file,
    search_in_bot_code,
    read_journal,
    get_trading_bot_status,
    kill_trading_bot,
    restart_trading_bot,
    deploy_trading_bot,
    deploy_monitor,
]

TOOL_MAP: dict[str, callable] = {fn.__name__: fn for fn in TOOLS}

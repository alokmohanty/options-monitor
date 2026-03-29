"""
Tools available to the Gemini agent for inspecting the trading bot.

Each function is exposed as a callable tool via the Gemini function-calling API.
"""

import os
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

def get_trading_bot_status() -> str:
    """
    Check whether the trading bot is currently running by inspecting the process list.

    Looks for Python processes whose command line includes the bot's main script
    (src/main.py) running from the bot root directory.

    Returns:
        A status message with PID(s) if running, or confirmation it is stopped.
    """
    root = str(Path(config.TradingBotConfig.root_path).resolve())

    try:
        result = subprocess.run(
            ["ps", "aux"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception as e:
        return f"❌ Could not read process list: {e}"

    matches: list[str] = []
    for line in result.stdout.splitlines():
        # Match lines that reference the bot root and src/main.py
        if root in line and "src/main.py" in line and "grep" not in line:
            parts = line.split()
            pid = parts[1] if len(parts) > 1 else "?"
            # Trim the command to keep it readable
            cmd = " ".join(parts[10:])[:120] if len(parts) > 10 else line[:120]
            matches.append(f"PID `{pid}` — `{cmd}`")

    if matches:
        lines = "\n".join(f"• {m}" for m in matches)
        return f"✅ Trading bot is **running** ({len(matches)} process{'es' if len(matches) > 1 else ''}):\n{lines}"
    else:
        return "🔴 Trading bot is **not running** — no matching processes found."


def kill_trading_bot() -> str:
    """
    Kill all running trading bot processes by executing the kill script.

    This runs scripts/kill_options_processes.py inside the bot virtualenv,
    the same script used by the daily cron job at 10:30 IST.

    Returns:
        Output from the kill script, or an error message.
    """
    root = Path(config.TradingBotConfig.root_path)
    python = root / ".venv" / "bin" / "python"
    kill_script = root / "scripts" / "kill_options_processes.py"
    kill_log = root / "logs" / "kill_options.log"

    if not kill_script.exists():
        return f"Kill script not found at {kill_script}"

    try:
        kill_log.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            [str(python), str(kill_script)],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=30,
        )
        output = (result.stdout + result.stderr).strip()
        # Append to kill log
        with open(kill_log, "a") as f:
            from datetime import datetime
            f.write(f"\n[options-monitor kill at {datetime.now().isoformat()}]\n")
            f.write(output + "\n")
        if result.returncode == 0:
            return f"✅ Kill script completed.\n{output}" if output else "✅ Kill script completed — no output."
        else:
            return f"⚠️ Kill script exited with code {result.returncode}.\n{output}"
    except subprocess.TimeoutExpired:
        return "❌ Kill script timed out after 30 seconds."
    except Exception as e:
        return f"❌ Error running kill script: {e}"


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

    # Step 2: wait briefly then start fresh
    import time
    time.sleep(2)

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


# ---------------------------------------------------------------------------
# Tool registry used by the agent
# ---------------------------------------------------------------------------

TOOLS = [
    read_log,
    search_errors_in_log,
    list_bot_files,
    read_bot_file,
    search_in_bot_code,
    get_trading_bot_status,
    kill_trading_bot,
    restart_trading_bot,
]

TOOL_MAP: dict[str, callable] = {fn.__name__: fn for fn in TOOLS}

"""
Tools available to the Gemini agent for inspecting the trading bot.

Each function is exposed as a callable tool via the Gemini function-calling API.
"""

import os
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

def read_log(last_n_lines: int = 200, search_keyword: str = "") -> str:
    """
    Read the trading bot log file.

    Args:
        last_n_lines: Number of lines to read from the end of the log (default 200).
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
# Tool registry used by the agent
# ---------------------------------------------------------------------------

TOOLS = [
    read_log,
    search_errors_in_log,
    list_bot_files,
    read_bot_file,
    search_in_bot_code,
]

TOOL_MAP: dict[str, callable] = {fn.__name__: fn for fn in TOOLS}

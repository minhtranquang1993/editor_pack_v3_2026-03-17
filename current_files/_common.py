#!/usr/bin/env python3
"""
_common.py — Shared utilities for Project Sharpen 2 tools.

Provides:
- ICT timezone constant
- Workspace path resolution (CLI > env > default)
- Telegram sender with 4096-char truncation
- Safe file/JSON readers
- Shared HTTP GET helper with strict timeout
- Logger factory
"""

import json
import logging
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ICT = timezone(timedelta(hours=7))

_SCRIPT_DIR = Path(__file__).resolve().parent
_DEFAULT_WORKSPACE = _SCRIPT_DIR.parent  # parent of tools/


# ---------------------------------------------------------------------------
# Workspace resolution
# ---------------------------------------------------------------------------

def resolve_workspace(cli_arg: str | None = None) -> Path:
    """Resolve workspace root directory.

    Resolution order (deterministic):
    1. cli_arg (--workspace flag)
    2. WORKSPACE_ROOT environment variable
    3. Default: parent of tools/ directory

    Raises:
        SystemExit: If resolved path does not exist or is not a directory.
    """
    if cli_arg:
        ws = Path(cli_arg).resolve()
    elif os.environ.get("WORKSPACE_ROOT"):
        ws = Path(os.environ["WORKSPACE_ROOT"]).resolve()
    else:
        ws = _DEFAULT_WORKSPACE

    if not ws.is_dir():
        print(f"Error: workspace not found: {ws}", file=sys.stderr)
        sys.exit(1)

    return ws


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def get_logger(name: str, verbose: bool = False) -> logging.Logger:
    """Create and configure a logger matching the project pattern."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(name)s] %(message)s",
            datefmt="%H:%M:%S",
        ))
        logger.addHandler(handler)
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    return logger


# ---------------------------------------------------------------------------
# Safe file readers
# ---------------------------------------------------------------------------

def read_file_safe(path: Path, default: str = "") -> str:
    """Read a text file, returning default on error."""
    try:
        return path.read_text(encoding="utf-8")
    except (FileNotFoundError, PermissionError, OSError):
        return default


def read_json_safe(path: Path, default: dict | list | None = None) -> dict | list:
    """Read and parse a JSON file, returning default on error."""
    if default is None:
        default = {}
    try:
        text = path.read_text(encoding="utf-8")
        return json.loads(text)
    except (FileNotFoundError, PermissionError, OSError, json.JSONDecodeError):
        return default


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------

def http_get(
    url: str,
    headers: dict[str, str] | None = None,
    timeout: int = 10,
) -> tuple[int, str]:
    """Perform an HTTP GET request with strict timeout.

    Returns:
        (status_code, response_body). On error returns (0, error_message).
    """
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return resp.getcode(), body
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        return e.code, body
    except (urllib.error.URLError, OSError) as e:
        return 0, f"Network error: {e}"
    except Exception as e:
        return 0, f"Unexpected error: {e}"


# ---------------------------------------------------------------------------
# Telegram sender
# ---------------------------------------------------------------------------

_TELEGRAM_MAX_LEN = 4096


def send_telegram(
    text: str,
    bot_token: str | None = None,
    chat_id: str | None = None,
) -> bool:
    """Send a message to Telegram (plain text).

    Truncates messages exceeding 4096 chars.
    Resolution order for creds:
    1) explicit args
    2) env vars TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID
    3) workspace credentials/telegram_token.txt + default chat_id 1661694132

    Returns:
        True if sent successfully, False otherwise.
    """
    bot_token = bot_token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID", "")

    if not bot_token:
        token_path = _DEFAULT_WORKSPACE / "credentials" / "telegram_token.txt"
        bot_token = read_file_safe(token_path).strip()
    if not chat_id:
        chat_id = "1661694132"

    if not bot_token or not chat_id:
        logging.getLogger("telegram").warning("Telegram credentials missing — skip send")
        return False

    # Truncate if needed
    if len(text) > _TELEGRAM_MAX_LEN:
        text = text[: _TELEGRAM_MAX_LEN - 30] + "\n\n... (truncated)"

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = json.dumps({
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.getcode() == 200:
                return True
            return False
    except (urllib.error.URLError, urllib.error.HTTPError):
        return False

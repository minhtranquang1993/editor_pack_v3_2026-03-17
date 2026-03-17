#!/usr/bin/env python3
"""
skill_health.py — Tool 2B: Skill Health Check.

Pings important skills/services and reports pass/fail/warn.
Sends Telegram alert only when FAIL or WARN detected.

Usage:
    python3 tools/skill_health.py --workspace /path/to/workspace
    python3 tools/skill_health.py --quiet --workspace /path/to/workspace
    python3 tools/skill_health.py --no-telegram --workspace /path/to/workspace

Exit codes:
    0 — all non-skipped checks OK
    1 — at least 1 FAIL
    2 — WARN only (no FAIL)
"""

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Fix Windows console encoding for emoji/Vietnamese
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from _common import (
    resolve_workspace,
    read_file_safe,
    get_logger,
    send_telegram,
    http_get,
    ICT,
)

logger = get_logger("skill-health")

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class CheckResult:
    name: str
    status: str  # "OK", "FAIL", "WARN", "SKIP"
    message: str = ""
    duration_ms: float = 0.0


# ---------------------------------------------------------------------------
# Per-call timeout and total budget
# ---------------------------------------------------------------------------

_CALL_TIMEOUT = 10  # seconds per network call
_TOTAL_BUDGET = 120  # seconds total

_budget_start: float = 0.0


def _budget_remaining() -> float:
    return max(0, _TOTAL_BUDGET - (time.monotonic() - _budget_start))


def _budget_exceeded() -> bool:
    return _budget_remaining() <= 0


# ---------------------------------------------------------------------------
# Check functions
# ---------------------------------------------------------------------------

def check_subprocess_import(
    script_path: Path,
    extra_code: str = "",
) -> CheckResult:
    """Check a Python script can be imported via subprocess isolation."""
    name = script_path.stem
    if not script_path.exists():
        return CheckResult(name, "SKIP", f"Script not found: {script_path}")

    parent_dir = str(script_path.parent)
    module_name = script_path.stem

    code = (
        f"import {module_name}; {extra_code}"
    )

    try:
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=_CALL_TIMEOUT,
            cwd=parent_dir,
            env=env,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode == 0:
            return CheckResult(name, "OK")
        stderr = result.stderr.strip().splitlines()
        err_msg = stderr[-1] if stderr else f"exit code {result.returncode}"
        return CheckResult(name, "FAIL", err_msg)
    except subprocess.TimeoutExpired:
        return CheckResult(name, "WARN", f"Import timed out after {_CALL_TIMEOUT}s")
    except Exception as e:
        return CheckResult(name, "FAIL", str(e))


def check_script_run(
    script_path: Path,
    args: list[str],
    expected_exit: int,
) -> CheckResult:
    """Run a script with args and verify exit code."""
    name = script_path.stem
    if not script_path.exists():
        return CheckResult(name, "SKIP", f"Script not found: {script_path}")

    try:
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        result = subprocess.run(
            [sys.executable, str(script_path)] + args,
            capture_output=True,
            text=True,
            timeout=_CALL_TIMEOUT * 3,  # 30s for full script runs
            cwd=str(script_path.parent),
            env=env,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode == expected_exit:
            return CheckResult(name, "OK")
        stderr = result.stderr.strip().splitlines()
        err_msg = stderr[-1] if stderr else ""
        return CheckResult(
            name, "FAIL",
            f"Exit {result.returncode} (expected {expected_exit}): {err_msg}",
        )
    except subprocess.TimeoutExpired:
        return CheckResult(name, "WARN", "Script timed out")
    except Exception as e:
        return CheckResult(name, "FAIL", str(e))


def check_supabase_ping() -> CheckResult:
    """Ping Supabase REST API."""
    url = os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_KEY", "")

    if not url or not key:
        # Fallback to known DND credentials in workspace
        ws = resolve_workspace(None)
        key = read_file_safe(ws / "credentials" / "supabase_key.txt").strip()
        if key:
            url = "https://lprtokohgnbpdqkrymje.supabase.co"

    if not url or not key:
        return CheckResult("Supabase DND", "SKIP", "Credentials not configured")

    endpoint = f"{url.rstrip('/')}/rest/v1/status_data?select=id&limit=1"
    status, body = http_get(
        endpoint,
        headers={"apikey": key, "Authorization": f"Bearer {key}"},
        timeout=_CALL_TIMEOUT,
    )

    if status == 200:
        return CheckResult("Supabase DND", "OK")
    if status in (401, 403):
        return CheckResult("Supabase DND", "WARN", f"HTTP {status} (auth issue)")
    if status == 0:
        return CheckResult("Supabase DND", "WARN", body)  # Network error → WARN
    return CheckResult("Supabase DND", "FAIL", f"HTTP {status}")


def check_telegram_bot() -> CheckResult:
    """Check Telegram bot via getMe API."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        ws = resolve_workspace(None)
        token = read_file_safe(ws / "credentials" / "telegram_token.txt").strip()
    if not token:
        return CheckResult("Telegram bot", "SKIP", "Credentials not configured")

    status, body = http_get(
        f"https://api.telegram.org/bot{token}/getMe",
        timeout=_CALL_TIMEOUT,
    )

    if status == 200:
        try:
            data = json.loads(body)
            if data.get("ok"):
                return CheckResult("Telegram bot", "OK")
        except (json.JSONDecodeError, KeyError):
            pass
        return CheckResult("Telegram bot", "FAIL", "API returned ok=false")
    if status == 0:
        return CheckResult("Telegram bot", "WARN", body)
    return CheckResult("Telegram bot", "FAIL", f"HTTP {status}")


def check_fb_ads_token() -> CheckResult:
    """Check FB Ads token via insights API."""
    token = os.environ.get("FB_ADS_TOKEN", "")
    act_id = os.environ.get("FB_ACT_ID", "")

    if not token:
        ws = resolve_workspace(None)
        token = read_file_safe(ws / "credentials" / "fb_token.txt").strip()
    if not act_id:
        act_id = "act_1465106504558065"

    if not token or not act_id:
        return CheckResult("FB Ads token", "SKIP", "Credentials not configured")

    url = (
        f"https://graph.facebook.com/v18.0/{act_id}/insights"
        f"?fields=spend&date_preset=today&access_token={token}"
    )
    status, body = http_get(url, timeout=_CALL_TIMEOUT)

    if status == 200:
        return CheckResult("FB Ads token", "OK")
    if status == 400:
        return CheckResult("FB Ads token", "WARN", f"HTTP 400 (token co the het han)")
    if status == 0:
        return CheckResult("FB Ads token", "WARN", body)
    return CheckResult("FB Ads token", "FAIL", f"HTTP {status}")


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def run_all_checks(workspace: Path) -> list[CheckResult]:
    """Run all health checks with timing."""
    global _budget_start
    _budget_start = time.monotonic()

    # Define checks: (name, callable, required)
    checks: list[tuple[str, callable, bool]] = [
        # Required checks
        (
            "session_snapshot",
            lambda: check_script_run(
                _SCRIPT_DIR / "session_snapshot.py",
                ["--dry-run", "--workspace", str(workspace)],
                expected_exit=0,
            ),
            True,
        ),
        ("Supabase DND", lambda: check_supabase_ping(), True),
        ("Telegram bot", lambda: check_telegram_bot(), True),
        # Optional checks
        (
            "kpi-tracker",
            lambda: check_subprocess_import(
                workspace / "skills" / "kpi-tracker" / "scripts" / "kpi_tracker.py",
                extra_code="kpi_tracker.load_config()",
            ),
            False,
        ),
        (
            "ads-insight-auto",
            lambda: check_subprocess_import(
                workspace / "skills" / "ads-insight-auto" / "scripts" / "ads_insight.py",
            ),
            False,
        ),
        (
            "report-ads",
            lambda: check_subprocess_import(
                workspace / "skills" / "report-ads" / "report_ads_aot.py",
            ),
            False,
        ),
        (
            "lead-monitor",
            lambda: check_subprocess_import(
                workspace / "skills" / "lead-monitor" / "scripts" / "lead_monitor.py",
            ),
            False,
        ),
        (
            "ads-anomaly",
            lambda: check_subprocess_import(
                workspace / "skills" / "ads-anomaly" / "scripts" / "ads_anomaly.py",
            ),
            False,
        ),
        (
            "daily_extract",
            lambda: check_script_run(
                workspace / "tools" / "daily_extract.py",
                ["--date", "1900-01-01"],
                expected_exit=1,
            ),
            False,
        ),
        ("FB Ads token", lambda: check_fb_ads_token(), False),
    ]

    results: list[CheckResult] = []
    for name, fn, required in checks:
        if _budget_exceeded():
            results.append(CheckResult(name, "SKIP", "Timeout budget exceeded"))
            continue

        start = time.monotonic()
        try:
            result = fn()
        except Exception as e:
            result = CheckResult(name, "FAIL", str(e))
        result.duration_ms = (time.monotonic() - start) * 1000
        result.name = name

        # Optional check: missing script → SKIP (not FAIL)
        if not required and result.status == "FAIL" and "not found" in result.message.lower():
            result.status = "SKIP"

        # Required check: SKIP → FAIL (required targets must not be skipped)
        if required and result.status == "SKIP":
            result.status = "FAIL"
            result.message = f"Required target missing: {result.message}"

        results.append(result)

    return results


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------

_STATUS_EMOJI = {
    "OK": "✅",
    "FAIL": "❌",
    "WARN": "⚠️",
    "SKIP": "⏭️",
}


def format_console(results: list[CheckResult]) -> str:
    """Format results as console table."""
    lines: list[str] = []
    for r in results:
        emoji = _STATUS_EMOJI.get(r.status, "?")
        name = r.name.ljust(20)
        detail = f"— {r.status}"
        if r.message:
            detail += f": {r.message}"
        timing = f"({r.duration_ms:.0f}ms)"
        lines.append(f"{emoji} {name} {detail} {timing}")

    # Summary (exclude SKIP)
    active = [r for r in results if r.status != "SKIP"]
    ok = sum(1 for r in active if r.status == "OK")
    fail = sum(1 for r in active if r.status == "FAIL")
    warn = sum(1 for r in active if r.status == "WARN")
    skip = sum(1 for r in results if r.status == "SKIP")

    lines.append("")
    summary = f"Summary: {ok} OK | {fail} FAIL | {warn} WARN"
    if skip:
        summary += f" | {skip} SKIP"
    lines.append(summary)

    return "\n".join(lines)


def format_telegram_alert(results: list[CheckResult]) -> str | None:
    """Format Telegram alert. Returns None if all OK/SKIP."""
    fails = [r for r in results if r.status == "FAIL"]
    warns = [r for r in results if r.status == "WARN"]

    if not fails and not warns:
        return None

    now_ict = datetime.now(ICT).strftime("%Y-%m-%d %H:%M ICT")
    lines: list[str] = [f"🚨 Skill Health Check — {now_ict}", ""]

    if fails:
        lines.append("❌ FAIL:")
        for r in fails:
            lines.append(f"• {r.name}: {r.message}")
        lines.append("")

    if warns:
        lines.append("⚠️ WARN:")
        for r in warns:
            lines.append(f"• {r.name}: {r.message}")
        lines.append("")

    active = [r for r in results if r.status != "SKIP"]
    ok_count = sum(1 for r in active if r.status == "OK")
    lines.append(f"→ {ok_count}/{len(active)} skills OK")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Skill Health Check — ping skills and services",
    )
    parser.add_argument(
        "--quiet", action="store_true",
        help="Suppress stdout, only send Telegram alerts on FAIL/WARN",
    )
    parser.add_argument(
        "--workspace", type=str, default=None,
        help="Override workspace root path",
    )
    parser.add_argument(
        "--no-telegram", action="store_true",
        help="Skip Telegram notification",
    )
    args = parser.parse_args()

    workspace = resolve_workspace(args.workspace)
    logger.info("Workspace: %s", workspace)

    results = run_all_checks(workspace)

    # Console output
    if not args.quiet:
        print(format_console(results))

    # Telegram alert
    alert = format_telegram_alert(results)
    if alert and not args.no_telegram:
        send_telegram(alert)

    # Exit code (exclude SKIP)
    active = [r for r in results if r.status != "SKIP"]
    has_fail = any(r.status == "FAIL" for r in active)
    has_warn = any(r.status == "WARN" for r in active)

    if has_fail:
        sys.exit(1)
    elif has_warn:
        sys.exit(2)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()

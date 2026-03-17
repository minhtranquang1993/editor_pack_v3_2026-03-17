#!/usr/bin/env python3
"""
api_usage.py — Tool 2C: API Usage Dashboard.

Snapshots API usage/budget, appends history, sends weekly Telegram summary.

Usage:
    python3 tools/api_usage.py --workspace /path/to/workspace
    python3 tools/api_usage.py --json --workspace /path/to/workspace
    python3 tools/api_usage.py --no-telegram --workspace /path/to/workspace

Exit codes:
    0 — success
    1 — error
"""

import argparse
import glob as glob_mod
import json
import os
import re
import sys
from dataclasses import dataclass, field, asdict
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
    read_json_safe,
    get_logger,
    send_telegram,
    http_get,
    ICT,
)

logger = get_logger("api-usage")

_MAX_HISTORY = 52  # 1 year of weekly snapshots


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class UsageSnapshot:
    timestamp: str = ""
    perplexity: dict = field(default_factory=dict)
    claudible: dict = field(default_factory=dict)
    token_health: dict = field(default_factory=dict)
    daytona: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# Collectors
# ---------------------------------------------------------------------------

def collect_perplexity(workspace: Path) -> dict:
    """Read perplexity_budget.json and calculate usage metrics."""
    path = workspace / "memory" / "perplexity_budget.json"
    data = read_json_safe(path)
    if not data:
        return {"file_missing": True, "requests_week": None, "est_cost_week": None,
                "budget_month": None, "used_month": None, "projected_month_end": None}

    budget = data.get("budget", 5.0)
    # Support both legacy and current schema
    total_cost = data.get("total_cost", data.get("total_spent_usd", 0))
    requests = data.get("requests", [])
    if not requests:
        # fallback count when requests array not present
        sonar_count = int(data.get("sonar_count", 0) or 0)
        sonar_pro_count = int(data.get("sonar_pro_count", 0) or 0)
        requests = [{"date": data.get("today", "")} for _ in range(sonar_count + sonar_pro_count)]

    # Calculate this week's requests (filter by date in last 7 days)
    now = datetime.now(timezone.utc)
    week_ago = now - timedelta(days=7)
    week_requests = []
    for req in requests:
        date_str = req.get("date", "") if isinstance(req, dict) else ""
        if date_str:
            try:
                dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                if dt >= week_ago:
                    week_requests.append(req)
            except ValueError:
                pass

    total_requests = len(requests) if requests else 1  # avoid div/0
    cost_per_req = total_cost / total_requests if total_requests else 0
    week_count = len(week_requests)
    est_cost_week = week_count * cost_per_req

    # Project month-end: assume ~4.3 weeks/month
    days_in_month = 30
    day_of_month = now.day
    if day_of_month > 0:
        daily_rate = total_cost / day_of_month
        projected = daily_rate * days_in_month
    else:
        projected = total_cost

    return {
        "requests_week": week_count,
        "est_cost_week": round(est_cost_week, 2),
        "budget_month": budget,
        "used_month": round(total_cost, 2),
        "projected_month_end": round(projected, 2),
    }


def collect_claudible(workspace: Path, weeks: int = 1) -> dict:
    """Scan memory/hands/*/run.log for LLM call counts."""
    pattern = str(workspace / "memory" / "hands" / "*" / "run.log")
    log_files = glob_mod.glob(pattern)

    if not log_files:
        return {"calls_week": 0, "top_skills": [], "note": "no run.log files found"}

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=7 * weeks)
    skill_counts: dict[str, int] = {}
    total = 0

    for log_path in log_files:
        log_p = Path(log_path)
        skill_name = log_p.parent.name
        text = read_file_safe(log_p)
        if not text:
            continue

        for line in text.splitlines():
            if "[INFO] Running step" not in line:
                continue
            # Try to extract date from log line; skip undated lines
            m = re.match(r"(\d{4}-\d{2}-\d{2})", line)
            if not m:
                continue  # Skip undated lines to avoid inflating counts
            try:
                dt = datetime.strptime(m.group(1), "%Y-%m-%d").replace(tzinfo=timezone.utc)
                if dt < cutoff:
                    continue
            except ValueError:
                continue
            total += 1
            skill_counts[skill_name] = skill_counts.get(skill_name, 0) + 1

    # Sort by count descending
    top_skills = sorted(skill_counts.items(), key=lambda x: x[1], reverse=True)

    return {"calls_week": total, "top_skills": top_skills}


def collect_token_health(workspace: Path) -> dict:
    """Check token health: expiry dates and API validation."""
    result: dict[str, dict] = {}

    # --- FB Ads token expiry ---
    fb_info = _scan_fb_expiry(workspace)
    result["fb_ads"] = fb_info

    # --- Google Ads token ---
    result["google_ads"] = _check_google_ads_token()

    # --- TikTok Ads token ---
    result["tiktok_ads"] = _check_tiktok_ads_token()

    return result


def _scan_fb_expiry(workspace: Path) -> dict:
    """Scan for FB Ads token expiry date in memory files."""
    for filename in ["memory/MEMORY.md", "memory/working-context.md"]:
        text = read_file_safe(workspace / filename)
        if not text:
            continue

        # Look for FB token expiry patterns
        pattern = re.compile(
            r"FB\s*Ads.*?(?:het han|hết hạn|expir).*?(\d{2}/\d{2}/\d{4}|\d{4}-\d{2}-\d{2})",
            re.IGNORECASE,
        )
        m = pattern.search(text)
        if m:
            date_str = m.group(1)
            try:
                if "/" in date_str:
                    dt = datetime.strptime(date_str, "%d/%m/%Y")
                else:
                    dt = datetime.strptime(date_str, "%Y-%m-%d")
                days_left = (dt - datetime.now()).days
                status = "OK" if days_left >= 30 else "WARN"
                return {"status": status, "days_remaining": days_left, "expiry": date_str}
            except ValueError:
                pass

    return {"status": "UNKNOWN", "note": "Expiry date not found in memory files"}


def _check_google_ads_token() -> dict:
    """Test Google Ads token refresh."""
    refresh_token = os.environ.get("GOOGLE_ADS_REFRESH_TOKEN", "")
    client_id = os.environ.get("GOOGLE_ADS_CLIENT_ID", "")
    client_secret = os.environ.get("GOOGLE_ADS_CLIENT_SECRET", "")

    # Fallback to workspace credentials
    if not refresh_token or not client_id or not client_secret:
        ws = resolve_workspace(None)
        secrets = read_json_safe(ws / "credentials" / "report_ads_secrets.json", default={})
        gg = secrets.get("gg_config", {}) if isinstance(secrets, dict) else {}
        refresh_token = refresh_token or gg.get("refresh_token", "")
        client_id = client_id or gg.get("client_id", "")
        client_secret = client_secret or gg.get("client_secret", "")

    if not refresh_token or not client_id or not client_secret:
        return {"status": "UNKNOWN", "note": "Credentials not configured"}

    # Lightweight token refresh test
    import urllib.request
    import urllib.parse

    data = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret,
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://oauth2.googleapis.com/token",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.getcode() == 200:
                return {"status": "OK"}
            return {"status": "FAIL", "note": f"HTTP {resp.getcode()}"}
    except Exception as e:
        return {"status": "FAIL", "note": str(e)}


def _check_tiktok_ads_token() -> dict:
    """Test TikTok Ads token."""
    token = os.environ.get("TIKTOK_ADS_TOKEN", "")
    if not token:
        ws = resolve_workspace(None)
        secrets = read_json_safe(ws / "credentials" / "report_ads_secrets.json", default={})
        token = (secrets.get("tt_token") if isinstance(secrets, dict) else "") or ""
    if not token:
        return {"status": "UNKNOWN", "note": "Credentials not configured"}

    status, body = http_get(
        "https://business-api.tiktok.com/open_api/v1.3/user/info/",
        headers={"Access-Token": token},
        timeout=10,
    )

    if status == 200:
        return {"status": "OK"}
    if status == 0:
        return {"status": "UNKNOWN", "note": body}
    return {"status": "FAIL", "note": f"HTTP {status}"}


def collect_daytona(workspace: Path, weeks: int = 1) -> dict:
    """Count Daytona sandbox mentions in daily memory files."""
    now = datetime.now(timezone.utc)
    count = 0

    for i in range(7 * weeks):
        day = (now - timedelta(days=i)).strftime("%Y-%m-%d")
        path = workspace / "memory" / f"{day}.md"
        text = read_file_safe(path)
        if text:
            count += len(re.findall(r"daytona", text, re.IGNORECASE))

    return {"sandboxes_week": count, "method": "keyword_count"}


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def collect_all(workspace: Path, weeks: int = 1) -> UsageSnapshot:
    """Collect all usage data into a snapshot."""
    snap = UsageSnapshot()
    snap.timestamp = datetime.now(timezone.utc).isoformat()

    snap.perplexity = collect_perplexity(workspace)
    snap.claudible = collect_claudible(workspace, weeks)
    snap.token_health = collect_token_health(workspace)
    snap.daytona = collect_daytona(workspace, weeks)

    # Build warnings
    warnings: list[str] = []

    # FB Ads expiry warning
    fb = snap.token_health.get("fb_ads", {})
    days_left = fb.get("days_remaining")
    if days_left is not None and days_left < 30:
        warnings.append(
            f"FB Ads token het han {fb.get('expiry', '?')} — con {days_left} ngay"
        )

    # Perplexity budget warning
    perp = snap.perplexity
    projected = perp.get("projected_month_end")
    budget = perp.get("budget_month")
    if projected and budget and projected > budget * 0.9:
        warnings.append(
            f"Perplexity du bao ${projected}/{budget} cuoi thang ({projected/budget*100:.0f}%)"
        )

    # Token failures
    for name, info in snap.token_health.items():
        if info.get("status") == "FAIL":
            warnings.append(f"{name}: token FAIL — {info.get('note', '')}")

    snap.warnings = warnings
    return snap


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------

def format_telegram(snap: UsageSnapshot) -> str:
    """Format Telegram weekly summary."""
    now_ict = datetime.now(ICT)
    week_end = now_ict.strftime("%d/%m")
    week_start = (now_ict - timedelta(days=6)).strftime("%d/%m")
    year = now_ict.strftime("%Y")

    lines: list[str] = [
        f"📊 API Usage Dashboard — tuan {week_start}–{week_end}/{year}",
        "",
    ]

    # Perplexity
    perp = snap.perplexity
    lines.append("🔍 Perplexity Search")
    if perp.get("file_missing"):
        lines.append("  N/A (perplexity_budget.json chua co)")
    else:
        rw = perp.get("requests_week", 0)
        ecw = perp.get("est_cost_week", 0)
        um = perp.get("used_month", 0)
        bm = perp.get("budget_month", 5)
        pct = (um / bm * 100) if bm else 0
        proj = perp.get("projected_month_end", 0)
        proj_emoji = "✅" if proj <= bm else "⚠️"
        lines.append(f"  Requests: {rw}/tuan (~${ecw})")
        lines.append(f"  Budget thang: ${um}/${bm} da dung ({pct:.0f}%)")
        lines.append(f"  Du bao: ~${proj} cuoi thang {proj_emoji}")
    lines.append("")

    # Claudible
    cl = snap.claudible
    lines.append("🤖 Claudible (Haiku LLM)")
    calls = cl.get("calls_week", 0)
    lines.append(f"  Calls: {calls} lan/tuan")
    top = cl.get("top_skills", [])
    if top:
        top_str = ", ".join(f"{name} ({count}x)" for name, count in top[:3])
        lines.append(f"  Skills dung nhieu nhat: {top_str}")
    elif cl.get("note"):
        lines.append(f"  ({cl['note']})")
    lines.append("")

    # Token Health
    lines.append("🔑 Token Health")
    th = snap.token_health
    for name, info in th.items():
        status = info.get("status", "UNKNOWN")
        emoji = {"OK": "✅", "WARN": "⚠️", "FAIL": "❌", "UNKNOWN": "❓"}.get(status, "?")
        display_name = name.replace("_", " ").title().ljust(14)
        detail = ""
        if "days_remaining" in info:
            detail = f"con {info['days_remaining']} ngay (het {info.get('expiry', '?')})"
        elif "note" in info:
            detail = info["note"]
        else:
            detail = "valid"
        lines.append(f"  {display_name} {emoji} {detail}")
    lines.append("")

    # Daytona
    dt = snap.daytona
    lines.append("☁️ Daytona Sandboxes")
    lines.append(f"  Tuan nay: {dt.get('sandboxes_week', 0)} mentions (tu daily logs)")
    lines.append("")

    # Warnings
    if snap.warnings:
        lines.append("⚠️ Can chu y:")
        for w in snap.warnings:
            lines.append(f"  • {w}")

    return "\n".join(lines)


def format_json(snap: UsageSnapshot) -> str:
    """Format snapshot as JSON."""
    data = asdict(snap)
    return json.dumps(data, indent=2, ensure_ascii=False, default=str)


# ---------------------------------------------------------------------------
# History management
# ---------------------------------------------------------------------------

def append_history(workspace: Path, snap: UsageSnapshot) -> None:
    """Append snapshot to api-usage.json history (max 52 entries)."""
    history_path = workspace / "memory" / "api-usage.json"
    history_path.parent.mkdir(parents=True, exist_ok=True)

    history = read_json_safe(history_path, default=[])
    if not isinstance(history, list):
        history = []

    history.append(asdict(snap))

    # Trim to max entries
    if len(history) > _MAX_HISTORY:
        history = history[-_MAX_HISTORY:]

    history_path.write_text(
        json.dumps(history, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    logger.info("History appended: %s (%d entries)", history_path, len(history))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="API Usage Dashboard — track budget and usage",
    )
    parser.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Print JSON to stdout, skip Telegram",
    )
    parser.add_argument(
        "--workspace", type=str, default=None,
        help="Override workspace root path",
    )
    parser.add_argument(
        "--no-telegram", action="store_true",
        help="Skip Telegram notification",
    )
    parser.add_argument(
        "--weeks", type=int, default=1,
        help="Look back N weeks for log scanning (default: 1)",
    )
    args = parser.parse_args()

    workspace = resolve_workspace(args.workspace)
    logger.info("Workspace: %s", workspace)

    try:
        snap = collect_all(workspace, args.weeks)
    except Exception as e:
        logger.error("Failed to collect usage data: %s", e)
        sys.exit(1)

    # Append to history
    try:
        append_history(workspace, snap)
    except Exception as e:
        logger.warning("Failed to append history: %s", e)

    # Output
    if args.json_output:
        print(format_json(snap))
        sys.exit(0)

    output = format_telegram(snap)
    print(output)

    if not args.no_telegram:
        send_telegram(output)

    sys.exit(0)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
session_snapshot.py — Tool 2A: Session Handover Snapshot.

Creates a concise session-handover.md (max 80 lines) summarizing:
- Active Tasks, Pending Decisions, Recent Context, Reminders
- KPI targets from kpi_config.json
- Credentials expiry scan
- Yesterday's Extracted Facts

Usage:
    python3 tools/session_snapshot.py --workspace /path/to/workspace
    python3 tools/session_snapshot.py --dry-run
    python3 tools/session_snapshot.py --context-file /path/to/working-context.md
"""

import argparse
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Fix Windows console encoding for emoji/Vietnamese
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Allow running as script or module
_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from _common import (
    resolve_workspace,
    read_file_safe,
    read_json_safe,
    get_logger,
)

logger = get_logger("session-snapshot")

# No external fallback path: workspace only for security/reproducibility
_FALLBACK_CONTEXT = None

# Known sections in priority order (for truncation)
_SECTION_KEYS = [
    "Active Tasks",
    "Pending Decisions",
    "Recent Context",
    "Notes / Reminders",
]

# Min lines per section when truncating (priority order)
_SECTION_MIN_LINES = {
    "Active Tasks": 5,
    "Pending Decisions": 3,
    "Recent Context": 3,
    "Notes / Reminders": 2,
}

_MAX_LINES = 80


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

def parse_working_context(text: str) -> dict[str, str]:
    """Parse working-context.md into sections by ## headers.

    Returns dict keyed by normalized section name.
    Unknown sections are ignored.
    """
    sections: dict[str, str] = {}
    current_key = None
    current_lines: list[str] = []

    for line in text.splitlines():
        # Match ## headers (case-insensitive, strip whitespace)
        m = re.match(r"^##\s+(.+)$", line)
        if m:
            # Save previous section
            if current_key:
                sections[current_key] = "\n".join(current_lines).strip()
            header = m.group(1).strip()
            # Normalize: match known keys case-insensitively
            matched_key = _match_section_key(header)
            if matched_key:
                current_key = matched_key
                current_lines = []
            else:
                current_key = None
                current_lines = []
        elif current_key is not None:
            # Strip HTML comments
            clean = re.sub(r"<!--.*?-->", "", line).rstrip()
            current_lines.append(clean)

    # Save last section
    if current_key:
        sections[current_key] = "\n".join(current_lines).strip()

    return sections


def _normalize(text: str) -> str:
    """Collapse whitespace/punctuation to lowercase alphanumeric tokens."""
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _match_section_key(header: str) -> str | None:
    """Match header text to known section keys (case-insensitive, normalized)."""
    norm_header = _normalize(header)
    for key in _SECTION_KEYS:
        norm_key = _normalize(key)
        if norm_key in norm_header or norm_header in norm_key:
            return key
    return None


def load_kpi_summary(workspace: Path) -> str:
    """Load KPI config and return summary line."""
    config_path = workspace / "skills" / "kpi-tracker" / "config" / "kpi_config.json"
    data = read_json_safe(config_path)
    if not data:
        return "_KPI config chua co._"

    month = data.get("month", "?")
    leads = data.get("leads_target", "?")
    inbox = data.get("inbox_target", "?")
    return f"- Month: {month} | Leads target: {leads} | Inbox target: {inbox}"


def load_yesterday_extract(workspace: Path) -> str:
    """Load first 3 lines of yesterday's Extracted Facts section."""
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    daily_path = workspace / "memory" / f"{yesterday}.md"
    text = read_file_safe(daily_path)
    if not text:
        return "_Khong co daily extract hom qua._"

    # Find ## Extracted Facts section
    in_section = False
    lines: list[str] = []
    for line in text.splitlines():
        if re.match(r"^##\s+Extracted\s+Facts", line, re.IGNORECASE):
            in_section = True
            continue
        if in_section:
            if re.match(r"^##\s+", line):
                break  # Next section
            stripped = line.strip()
            if stripped:
                lines.append(stripped)
                if len(lines) >= 3:
                    break

    return "\n".join(lines) if lines else "_Khong co daily extract hom qua._"


def scan_credential_expiry(workspace: Path) -> str:
    """Scan MEMORY.md and working-context.md for credential expiry info.

    Only emits safe fields (provider + expiry date + days remaining).
    Never outputs raw matched lines to avoid credential leaks.
    """
    results: list[str] = []
    seen_providers: set[str] = set()

    sources = [
        workspace / "MEMORY.md",
        workspace / "memory" / "working-context.md",
    ]

    pattern = re.compile(
        r"(FB|Facebook|Google|TikTok|Ads)\s*(?:Ads)?\s*(?:token)?"
        r".*?(het han|expir|het hạn|hết hạn).*?"
        r"(\d{2}/\d{2}/\d{4}|\d{4}-\d{2}-\d{2})",
        re.IGNORECASE,
    )

    for src in sources:
        text = read_file_safe(src)
        if not text:
            continue

        for match in pattern.finditer(text):
            provider = match.group(1).strip()
            date_str = match.group(3)
            # Deduplicate by provider
            if provider.lower() in seen_providers:
                continue
            try:
                if "/" in date_str:
                    dt = datetime.strptime(date_str, "%d/%m/%Y")
                else:
                    dt = datetime.strptime(date_str, "%Y-%m-%d")
                days_left = (dt - datetime.now()).days
                if days_left < 30:
                    results.append(f"- {provider} Ads token: het han {date_str} (con {days_left} ngay)")
                    seen_providers.add(provider.lower())
            except ValueError:
                pass

    return "\n".join(results) if results else "_Khong phat hien credentials sap het han._"


# ---------------------------------------------------------------------------
# Snapshot builder
# ---------------------------------------------------------------------------

def build_snapshot(
    sections: dict[str, str],
    kpi: str,
    extract: str,
    credentials: str,
) -> str:
    """Assemble session-handover.md content, enforcing 80-line limit.

    Truncation priority (cut from bottom sections first):
    Active Tasks (5) > Pending Decisions (3) > Recent Context (3) >
    Reminders (2) > KPI (1) > Credentials (1) > Extract (1)
    """
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # Build raw blocks
    blocks: list[tuple[str, str, int]] = []  # (header, content, min_lines)

    blocks.append(("# Session Handover — " + now_utc, "", 1))

    for key in _SECTION_KEYS:
        emoji_map = {
            "Active Tasks": "🎯",
            "Pending Decisions": "⏳",
            "Recent Context": "📅",
            "Notes / Reminders": "⚠️",
        }
        emoji = emoji_map.get(key, "")
        content = sections.get(key, "_Khong co du lieu._")
        min_l = _SECTION_MIN_LINES.get(key, 2)
        blocks.append((f"## {emoji} {key}", content, min_l))

    blocks.append(("## 📊 KPI thang nay", kpi, 1))
    blocks.append(("## 🔑 Credentials sap het han (< 30 ngay)", credentials, 1))
    blocks.append(("## 📝 Daily Extract hom qua", extract, 1))

    # Assemble full output
    output_lines: list[str] = []
    for header, content, _ in blocks:
        output_lines.append(header)
        if content:
            output_lines.extend(content.splitlines())
        output_lines.append("")  # blank line after section

    # Enforce 80-line limit by truncating from bottom sections
    if len(output_lines) > _MAX_LINES:
        output_lines = _truncate_sections(blocks, _MAX_LINES)

    return "\n".join(output_lines).rstrip() + "\n"


def _truncate_sections(
    blocks: list[tuple[str, str, int]],
    max_lines: int,
) -> list[str]:
    """Truncate sections from bottom-priority first to fit max_lines.

    Removes exactly the needed number of lines from lowest-priority sections,
    strictly enforcing per-section minimums. No final hard slicing.
    """
    # Build content lists per block: [header, ...content_lines, spacer]
    # Track content lines separately from header/spacer for precise removal
    block_headers: list[str] = []
    block_content: list[list[str]] = []
    for header, content, _ in blocks:
        block_headers.append(header)
        block_content.append(content.splitlines() if content else [])

    # Total = headers + contents + spacers (1 per block)
    total = len(blocks)  # spacers
    total += len(blocks)  # headers
    total += sum(len(c) for c in block_content)  # content lines

    overflow = total - max_lines
    if overflow <= 0:
        return _assemble(block_headers, block_content)

    # Remove lines from lowest-priority blocks first (reverse order, skip block 0 = title)
    for i in range(len(blocks) - 1, 0, -1):
        if overflow <= 0:
            break
        min_content = blocks[i][2]
        current_content = block_content[i]
        removable = len(current_content) - min_content
        if removable <= 0:
            continue

        to_remove = min(removable, overflow)
        # Keep first (current - to_remove) lines of content
        new_content = current_content[:len(current_content) - to_remove]
        # Only add truncation marker if net removal is positive (to_remove >= 2)
        # When to_remove == 1, adding a marker would negate the removal
        if to_remove >= 2:
            new_content.append("... (truncated)")
            actual_removed = to_remove - 1  # removed to_remove, added 1 marker
        else:
            actual_removed = to_remove  # removed 1 line, no marker added
        overflow -= actual_removed
        block_content[i] = new_content

    result = _assemble(block_headers, block_content)
    # Final safety: hard-guarantee max_lines (should not trigger with correct math)
    assert len(result) <= max_lines, f"Truncation bug: {len(result)} > {max_lines}"
    return result


def _assemble(headers: list[str], contents: list[list[str]]) -> list[str]:
    """Assemble blocks into flat line list."""
    result: list[str] = []
    for i, header in enumerate(headers):
        result.append(header)
        result.extend(contents[i])
        result.append("")  # spacer
    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create a session handover snapshot (max 80 lines)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print snapshot to stdout, do not write file",
    )
    parser.add_argument(
        "--workspace", type=str, default=None,
        help="Override workspace root path",
    )
    parser.add_argument(
        "--context-file", type=str, default=None,
        help="Override path to working-context.md",
    )
    args = parser.parse_args()

    workspace = resolve_workspace(args.workspace)
    logger.info("Workspace: %s", workspace)

    # Find working-context.md
    context_text = ""
    if args.context_file:
        context_text = read_file_safe(Path(args.context_file))
        if not context_text:
            logger.error("Context file not found: %s", args.context_file)
            sys.exit(1)
    else:
        # Try workspace/memory/ first, then fallback
        candidates = [
            workspace / "memory" / "working-context.md",
        ]
        for candidate in candidates:
            context_text = read_file_safe(candidate)
            if context_text:
                logger.info("Using context file: %s", candidate)
                break

        if not context_text:
            logger.error(
                "working-context.md not found. Tried: %s",
                ", ".join(str(c) for c in candidates),
            )
            sys.exit(1)

    # Parse and collect data
    sections = parse_working_context(context_text)
    kpi = load_kpi_summary(workspace)
    extract = load_yesterday_extract(workspace)
    credentials = scan_credential_expiry(workspace)

    # Build snapshot
    snapshot = build_snapshot(sections, kpi, extract, credentials)

    if args.dry_run:
        print(snapshot)
        logger.info("Dry run — not writing file")
        sys.exit(0)

    # Write output
    output_dir = workspace / "memory"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "session-handover.md"
    output_path.write_text(snapshot, encoding="utf-8")

    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"Handover snapshot saved — {now_utc}")
    sys.exit(0)


if __name__ == "__main__":
    main()

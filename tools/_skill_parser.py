#!/usr/bin/env python3
"""
_skill_parser.py — Shared frontmatter parser for skill catalog tools.

Supports:
- UTF-8 with/without BOM
- LF/CRLF
- YAML-like frontmatter bounded by --- ... ---
- Single-line fields: key: value
- Folded/literal block for description: >, >-, |, |-
"""

from pathlib import Path
import re


_KEY_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*)\s*:\s*(.*)$")


def read_file_text(filepath: Path) -> str | None:
    """Read file text with BOM handling."""
    try:
        raw = filepath.read_bytes()
        if raw.startswith(b"\xef\xbb\xbf"):
            raw = raw[3:]
        return raw.decode("utf-8")
    except Exception:
        return None


def _strip_quotes(value: str) -> str:
    value = value.strip()
    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        return value[1:-1]
    return value


def parse_frontmatter(filepath: Path) -> dict | None:
    text = read_file_text(filepath)
    if text is None:
        return None

    lines = text.replace("\r\n", "\n").split("\n")
    if not lines or lines[0].strip() != "---":
        return None

    end_idx = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break
    if end_idx is None:
        return None

    fm_lines = lines[1:end_idx]
    fm: dict[str, str] = {}

    i = 0
    while i < len(fm_lines):
        raw = fm_lines[i]
        line = raw.rstrip("\n")
        stripped = line.strip()

        if not stripped or stripped.startswith("#"):
            i += 1
            continue

        m = _KEY_RE.match(stripped)
        if not m:
            return None

        key = m.group(1)
        value = m.group(2).strip()

        # Multi-line folded/literal scalars
        if value in {">", ">-", "|", "|-"}:
            block_style = value
            i += 1
            block_lines = []
            while i < len(fm_lines):
                nxt = fm_lines[i]
                if nxt.startswith(" ") or nxt.startswith("\t"):
                    block_lines.append(nxt.lstrip())
                    i += 1
                    continue

                # blank line inside block
                if nxt.strip() == "":
                    block_lines.append("")
                    i += 1
                    continue

                break

            if block_style.startswith(">"):
                # YAML folded style: join non-empty lines with spaces, preserve paragraph breaks
                paragraphs = []
                current = []
                for bl in block_lines:
                    if bl == "":
                        if current:
                            paragraphs.append(" ".join(current).strip())
                            current = []
                        paragraphs.append("")
                    else:
                        current.append(bl.strip())
                if current:
                    paragraphs.append(" ".join(current).strip())
                # normalize multiple paragraph breaks
                out = []
                last_blank = False
                for p in paragraphs:
                    if p == "":
                        if not last_blank:
                            out.append("")
                        last_blank = True
                    else:
                        out.append(p)
                        last_blank = False
                fm[key] = "\n".join(out).strip()
            else:
                # Literal block
                fm[key] = "\n".join(block_lines).rstrip("\n")
            continue

        fm[key] = _strip_quotes(value)
        i += 1

    return fm


def is_safe_subpath(base: Path, target: Path) -> bool:
    """Check target resolves as descendant of base (prevents path traversal)."""
    try:
        return target.resolve().is_relative_to(base.resolve())
    except Exception:
        return False

#!/usr/bin/env python3
"""
generate_skill_manifest.py — Generate skill_manifest.json (optionally _INDEX.md).

Scans skills/ directory and generates:
1. skills/skill_manifest.json (canonical source-of-truth)
2. skills/_INDEX.md (optional, auto-generated from manifest)

Usage:
    python tools/generate_skill_manifest.py
    python tools/generate_skill_manifest.py --skills-dir skills
    python tools/generate_skill_manifest.py --write-index
"""

import argparse
import json
import re
import sys
from pathlib import Path

# Import shared parser (per parser contract)

# Ensure tools/ dir is on path for local imports
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _skill_parser import parse_frontmatter, read_file_text


# ---------------------------------------------------------------------------
# Manifest field derivation (per generation contract)
# ---------------------------------------------------------------------------

def extract_section(text: str, heading: str) -> str | None:
    """Extract content from a markdown ## section."""
    pattern = rf"##\s+{re.escape(heading)}\s*\n(.*?)(?=\n##\s|\Z)"
    match = re.search(pattern, text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return None


def derive_category(skill_dir: Path, text: str | None) -> str:
    """Derive category from ## Category section or default."""
    if text:
        section = extract_section(text, "Category")
        if section:
            # Take first non-empty line
            for line in section.split("\n"):
                line = line.strip()
                if line:
                    return line.lower()
    return "uncategorized"


def derive_risk(skill_dir: Path, text: str | None) -> str:
    """Derive risk from ## Risk section or default."""
    if text:
        section = extract_section(text, "Risk")
        if section:
            for line in section.split("\n"):
                line = line.strip().lower()
                if line in ("low", "medium", "high"):
                    return line
    return "low"


def derive_triggers(description: str) -> list[str]:
    """Derive triggers from first sentence of description, split by comma."""
    if not description:
        return []
    # Take first sentence (up to period or end)
    first_sentence = re.split(r"\.\s", description)[0]
    # Split by comma for trigger phrases
    parts = [p.strip() for p in first_sentence.split(",") if p.strip()]
    return parts if parts else []


def build_manifest_entry(skill_dir: Path) -> dict | None:
    """Build a manifest entry for a single skill directory."""
    skill_name = skill_dir.name
    skill_file = skill_dir / "SKILL.md"

    if not skill_file.is_file():
        return "__NO_SKILL__"

    fm = parse_frontmatter(skill_file)
    if fm is None:
        return None

    text = read_file_text(skill_file)

    name = skill_dir.name  # Always use folder name per generation contract
    description = fm.get("description", "")

    has_scripts = (skill_dir / "scripts").is_dir()
    has_references = (skill_dir / "references").is_dir()

    return {
        "name": name,
        "category": derive_category(skill_dir, text),
        "risk": derive_risk(skill_dir, text),
        "triggers": derive_triggers(description),
        "entrypoint": {
            "type": "script" if has_scripts else "instruction",
            "path": f"skills/{skill_name}/SKILL.md",
        },
        "requires": {
            "scripts": has_scripts,
            "references": has_references,
        },
        "status": "active",
    }


# ---------------------------------------------------------------------------
# _INDEX.md generator
# ---------------------------------------------------------------------------

def generate_index_md(manifest: list[dict]) -> str:
    """Generate _INDEX.md content from manifest entries."""
    lines = [
        "# Skill Catalog Index",
        "",
        "> ⚠️ **Auto-generated** from `skill_manifest.json`. Do not edit manually.",
        "",
    ]

    # Group by category
    categories = {}
    for entry in manifest:
        cat = entry.get("category", "uncategorized")
        categories.setdefault(cat, []).append(entry)

    lines.append(f"**Total skills:** {len(manifest)}")
    lines.append("")

    for cat in sorted(categories.keys()):
        entries = sorted(categories[cat], key=lambda e: e["name"])
        lines.append(f"## {cat.title()}")
        lines.append("")
        lines.append("| Skill | Risk | Type | Readiness | Status |")
        lines.append("|-------|------|------|-----------|--------|")
        for e in entries:
            name = e["name"]
            risk = e.get("risk", "low")
            entry_type = e.get("entrypoint", {}).get("type", "instruction")
            status = e.get("status", "active")
            has_scripts = e.get("requires", {}).get("scripts", False)
            readiness = "executable" if has_scripts else "spec-only"
            lines.append(f"| [{name}]({name}/SKILL.md) | {risk} | {entry_type} | `{readiness}` | {status} |")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Generate skill_manifest.json and _INDEX.md from skills/ directory",
    )
    parser.add_argument(
        "--skills-dir", type=str, default="skills",
        help="Path to skills directory (default: skills/)",
    )
    parser.add_argument(
        "--write-index", action="store_true",
        help="Also regenerate skills/_INDEX.md from manifest (default: keep existing _INDEX.md)",
    )

    args = parser.parse_args()
    skills_dir = Path(args.skills_dir)

    if not skills_dir.is_dir():
        print(f"ERROR: Skills directory not found: {skills_dir}", file=sys.stderr)
        sys.exit(1)

    # Scan all skill directories
    manifest = []
    errors = []

    for d in sorted(skills_dir.iterdir()):
        if d.is_dir() and not d.name.startswith("_") and not d.name.startswith("."):
            entry = build_manifest_entry(d)
            if isinstance(entry, dict):
                manifest.append(entry)
            elif entry == "__NO_SKILL__":
                # Ignore helper folders that are not skills
                continue
            else:
                errors.append(f"WARNING: Could not build manifest entry for {d.name}")

    # Write manifest
    manifest_path = skills_dir / "skill_manifest.json"
    manifest_json = json.dumps(manifest, indent=2, ensure_ascii=False)
    manifest_path.write_text(manifest_json, encoding="utf-8")
    print(f"✅ Generated {manifest_path} ({len(manifest)} skills)")

    # Optionally write _INDEX.md
    if args.write_index:
        index_path = skills_dir / "_INDEX.md"
        index_content = generate_index_md(manifest)
        index_path.write_text(index_content, encoding="utf-8")
        print(f"✅ Generated {index_path}")
    else:
        print("ℹ️  Skipped _INDEX.md regeneration (use --write-index to enable)")

    # Report errors
    for err in errors:
        print(err, file=sys.stderr)

    if errors:
        print(f"\n⚠️  {len(errors)} skill(s) could not be processed", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

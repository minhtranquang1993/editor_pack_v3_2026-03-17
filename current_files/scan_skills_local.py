#!/usr/bin/env python3
"""
scan_skills_local.py — Skill catalog linter CLI.

Scans skills/ directory for metadata hygiene issues.
Outputs severity-ranked report (ERROR/WARN/INFO) in Markdown or JSON.

Usage:
    python tools/scan_skills_local.py --format md --out memory/skill-catalog-report.md
    python tools/scan_skills_local.py --format json --out memory/skill-catalog-report.json
    python tools/scan_skills_local.py --fail-on error
    python tools/scan_skills_local.py --legacy-warn
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

from _skill_parser import parse_frontmatter


# ---------------------------------------------------------------------------
# Lint checks
# ---------------------------------------------------------------------------

def lint_skill(skill_dir: Path, index_skills: set, manifest_skills: set,
               legacy_warn: bool) -> list[dict]:
    """Run all lint checks on a single skill directory. Returns list of issues."""
    issues = []
    folder_name = skill_dir.name

    # Skip hidden/system directories
    if folder_name.startswith("_") or folder_name.startswith("."):
        return issues

    skill_file = skill_dir / "SKILL.md"

    # --- ERROR checks ---

    if not skill_file.is_file():
        issues.append({
            "severity": "ERROR",
            "skill": folder_name,
            "check": "missing_skill_md",
            "message": f"Missing SKILL.md in skills/{folder_name}/",
        })
        return issues  # Can't check further without the file

    fm = parse_frontmatter(skill_file)
    if fm is None:
        sev = "WARN" if legacy_warn else "ERROR"
        issues.append({
            "severity": sev,
            "skill": folder_name,
            "check": "frontmatter_parse_fail",
            "message": f"Failed to parse YAML frontmatter in skills/{folder_name}/SKILL.md",
        })
        return issues

    name = fm.get("name", "")
    description = fm.get("description", "")

    if not name:
        sev = "WARN" if legacy_warn else "ERROR"
        issues.append({
            "severity": sev,
            "skill": folder_name,
            "check": "missing_name",
            "message": f"Missing 'name' field in frontmatter of skills/{folder_name}/SKILL.md",
        })

    if not description:
        sev = "WARN" if legacy_warn else "ERROR"
        issues.append({
            "severity": sev,
            "skill": folder_name,
            "check": "missing_description",
            "message": f"Missing 'description' field in frontmatter of skills/{folder_name}/SKILL.md",
        })

    if name and name != folder_name:
        sev = "WARN" if legacy_warn else "ERROR"
        issues.append({
            "severity": sev,
            "skill": folder_name,
            "check": "name_mismatch",
            "message": f"Frontmatter name '{name}' does not match folder name '{folder_name}'",
        })

    # --- WARN checks ---

    has_scripts = (skill_dir / "scripts").is_dir()
    has_references = (skill_dir / "references").is_dir()

    if not has_scripts and not has_references:
        issues.append({
            "severity": "WARN",
            "skill": folder_name,
            "check": "no_resources",
            "message": f"No scripts/ or references/ directory in skills/{folder_name}/",
        })

    if description and len(description) < 80:
        issues.append({
            "severity": "WARN",
            "skill": folder_name,
            "check": "short_description",
            "message": f"Description is only {len(description)} chars (< 80) in skills/{folder_name}/SKILL.md",
        })

    if folder_name not in index_skills:
        issues.append({
            "severity": "WARN",
            "skill": folder_name,
            "check": "missing_from_index",
            "message": f"Skill '{folder_name}' not found in _INDEX.md",
        })

    return issues


def check_manifest_coverage(skills_dir: Path, manifest_path: Path) -> list[dict]:
    """Check that manifest covers all skill folders and vice versa."""
    issues = []

    # Count actual skill folders with SKILL.md
    actual_skills = set()
    if skills_dir.is_dir():
        for d in sorted(skills_dir.iterdir()):
            if d.is_dir() and not d.name.startswith("_") and not d.name.startswith("."):
                if (d / "SKILL.md").is_file():
                    actual_skills.add(d.name)

    # Read manifest
    manifest_skills = set()
    if manifest_path.is_file():
        try:
            with open(manifest_path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                for entry in data:
                    manifest_skills.add(entry.get("name", ""))
        except Exception:
            pass

    if actual_skills != manifest_skills:
        missing_in_manifest = actual_skills - manifest_skills
        extra_in_manifest = manifest_skills - actual_skills
        parts = []
        if missing_in_manifest:
            parts.append(f"missing from manifest: {', '.join(sorted(missing_in_manifest))}")
        if extra_in_manifest:
            parts.append(f"extra in manifest: {', '.join(sorted(extra_in_manifest))}")
        issues.append({
            "severity": "ERROR",
            "skill": "_manifest",
            "check": "manifest_coverage_mismatch",
            "message": f"Manifest coverage mismatch ({len(actual_skills)} folders vs {len(manifest_skills)} entries). {'; '.join(parts)}",
        })

    return issues


def compute_info_stats(skills_dir: Path, all_issues: list[dict]) -> list[dict]:
    """Generate INFO-level statistics."""
    infos = []

    # Category coverage from SKILL.md body
    categories = {}
    if skills_dir.is_dir():
        for d in sorted(skills_dir.iterdir()):
            if d.is_dir() and not d.name.startswith("_") and not d.name.startswith("."):
                skill_file = d / "SKILL.md"
                if skill_file.is_file():
                    try:
                        text = skill_file.read_text(encoding="utf-8")
                    except Exception:
                        continue
                    # Extract category from ## Category section
                    cat_match = re.search(r"##\s+Category\s*\n+\s*(\S+)", text)
                    cat = cat_match.group(1) if cat_match else "uncategorized"
                    categories.setdefault(cat, []).append(d.name)

    if categories:
        summary_parts = [f"{cat}: {len(skills)}" for cat, skills in sorted(categories.items())]
        infos.append({
            "severity": "INFO",
            "skill": "_summary",
            "check": "category_coverage",
            "message": f"Category coverage: {', '.join(summary_parts)}",
        })

    # Resource richness
    richness = {}
    if skills_dir.is_dir():
        for d in sorted(skills_dir.iterdir()):
            if d.is_dir() and not d.name.startswith("_") and not d.name.startswith("."):
                if not (d / "SKILL.md").is_file():
                    continue
                score = 0
                if (d / "scripts").is_dir():
                    score += 1
                if (d / "references").is_dir():
                    score += 1
                if (d / "assets").is_dir():
                    score += 1
                richness[d.name] = score

    if richness:
        avg = sum(richness.values()) / len(richness) if richness else 0
        infos.append({
            "severity": "INFO",
            "skill": "_summary",
            "check": "resource_richness",
            "message": f"Resource richness: avg {avg:.1f}/3 across {len(richness)} skills",
        })

    return infos


# ---------------------------------------------------------------------------
# Index parser
# ---------------------------------------------------------------------------

def parse_index(index_path: Path) -> set:
    """Extract skill names mentioned in _INDEX.md."""
    skills = set()
    if not index_path.is_file():
        return skills
    try:
        text = index_path.read_text(encoding="utf-8")
    except Exception:
        return skills
    # Match patterns like **skill-name**
    for match in re.finditer(r"\*\*([a-z0-9][a-z0-9-]*)\*\*", text):
        skills.add(match.group(1))
    # Match markdown links like [skill-name](...)
    for match in re.finditer(r"\[([a-z0-9][a-z0-9-]*)\]\(", text):
        skills.add(match.group(1))
    # Match inline code refs like `skill-name`
    for match in re.finditer(r"`([a-z0-9][a-z0-9-]*)`", text):
        skills.add(match.group(1))
    return skills


# ---------------------------------------------------------------------------
# Report formatters
# ---------------------------------------------------------------------------

def format_markdown(issues: list[dict]) -> str:
    """Format issues as a Markdown report."""
    lines = ["# Skill Catalog Lint Report", ""]

    errors = [i for i in issues if i["severity"] == "ERROR"]
    warns = [i for i in issues if i["severity"] == "WARN"]
    infos = [i for i in issues if i["severity"] == "INFO"]

    lines.append(f"**Summary:** {len(errors)} errors, {len(warns)} warnings, {len(infos)} infos")
    lines.append("")

    if errors:
        lines.append("## ❌ Errors")
        lines.append("")
        for i in errors:
            lines.append(f"- `[{i['skill']}]` {i['message']}")
        lines.append("")

    if warns:
        lines.append("## ⚠️ Warnings")
        lines.append("")
        for i in warns:
            lines.append(f"- `[{i['skill']}]` {i['message']}")
        lines.append("")

    if infos:
        lines.append("## ℹ️ Info")
        lines.append("")
        for i in infos:
            lines.append(f"- {i['message']}")
        lines.append("")

    return "\n".join(lines)


def format_json(issues: list[dict]) -> str:
    """Format issues as JSON."""
    return json.dumps({
        "total": len(issues),
        "errors": len([i for i in issues if i["severity"] == "ERROR"]),
        "warnings": len([i for i in issues if i["severity"] == "WARN"]),
        "infos": len([i for i in issues if i["severity"] == "INFO"]),
        "issues": issues,
    }, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Skill catalog linter — checks metadata hygiene across skills/",
    )
    parser.add_argument(
        "--format", choices=["md", "json"], default="md",
        help="Output format: md (Markdown) or json",
    )
    parser.add_argument(
        "--out", type=str, default=None,
        help="Output file path (default: stdout)",
    )
    parser.add_argument(
        "--fail-on", choices=["error", "warn"], default=None,
        help="Exit with code 1 if issues at this severity or higher exist",
    )
    parser.add_argument(
        "--legacy-warn", action="store_true",
        help="Backward compatibility: downgrade frontmatter ERRORs to WARNs",
    )
    parser.add_argument(
        "--skills-dir", type=str, default="skills",
        help="Path to skills directory (default: skills/)",
    )

    args = parser.parse_args()

    skills_dir = Path(args.skills_dir)
    index_path = skills_dir / "_INDEX.md"
    manifest_path = skills_dir / "skill_manifest.json"

    if not skills_dir.is_dir():
        print(f"ERROR: Skills directory not found: {skills_dir}", file=sys.stderr)
        sys.exit(1)

    # Parse index for coverage checks
    index_skills = parse_index(index_path)

    # Collect manifest skills
    manifest_skills = set()
    if manifest_path.is_file():
        try:
            with open(manifest_path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                manifest_skills = {e.get("name", "") for e in data}
        except Exception:
            pass

    # Run lint checks on each skill directory
    all_issues = []
    for d in sorted(skills_dir.iterdir()):
        if d.is_dir() and not d.name.startswith("_") and not d.name.startswith("."):
            # Ignore helper folders that are not skill folders
            if not (d / "SKILL.md").is_file():
                continue
            all_issues.extend(lint_skill(d, index_skills, manifest_skills, args.legacy_warn))

    # Manifest coverage check
    all_issues.extend(check_manifest_coverage(skills_dir, manifest_path))

    # Info stats
    all_issues.extend(compute_info_stats(skills_dir, all_issues))

    # Format output
    if args.format == "json":
        output = format_json(all_issues)
    else:
        output = format_markdown(all_issues)

    # Write output
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(output, encoding="utf-8")
        print(f"Report written to {args.out}")
    else:
        print(output)

    # Fail-on check
    if args.fail_on:
        errors = [i for i in all_issues if i["severity"] == "ERROR"]
        warns = [i for i in all_issues if i["severity"] == "WARN"]

        if args.fail_on == "error" and errors:
            print(f"\nFAILED: {len(errors)} error(s) found", file=sys.stderr)
            sys.exit(1)
        elif args.fail_on == "warn" and (errors or warns):
            print(f"\nFAILED: {len(errors)} error(s), {len(warns)} warning(s) found",
                  file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()

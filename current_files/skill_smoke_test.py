#!/usr/bin/env python3
"""
skill_smoke_test.py — Smoke test CLI for skill catalog.

Validates skill structure, frontmatter parsing, file references,
and Python script compilation.

Usage:
    python tools/skill_smoke_test.py --all
    python tools/skill_smoke_test.py --skill ads-anomaly
    python tools/skill_smoke_test.py --strict
"""

import argparse
import os
import py_compile
import re
import sys
from pathlib import Path

# Import shared parser (per parser contract)

# Ensure tools/ dir is on path for local imports
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _skill_parser import parse_frontmatter, read_file_text, is_safe_subpath


# ---------------------------------------------------------------------------
# Smoke test checks
# ---------------------------------------------------------------------------

def smoke_test_skill(skill_dir: Path, strict: bool = False) -> list[dict]:
    """Run smoke tests on a single skill directory."""
    results = []
    skill_name = skill_dir.name
    skill_file = skill_dir / "SKILL.md"

    # 1. SKILL.md exists
    if not skill_file.is_file():
        results.append({
            "skill": skill_name,
            "test": "skill_md_exists",
            "status": "FAIL",
            "message": "SKILL.md not found",
        })
        return results

    results.append({
        "skill": skill_name,
        "test": "skill_md_exists",
        "status": "PASS",
        "message": "SKILL.md found",
    })

    # 2. Parse frontmatter
    fm = parse_frontmatter(skill_file)
    if fm is None:
        results.append({
            "skill": skill_name,
            "test": "frontmatter_parse",
            "status": "FAIL",
            "message": "Failed to parse YAML frontmatter",
        })
    else:
        results.append({
            "skill": skill_name,
            "test": "frontmatter_parse",
            "status": "PASS",
            "message": f"Parsed: name={fm.get('name', '?')}, desc={len(fm.get('description', ''))} chars",
        })

    # 3. Validate internal path references
    text = read_file_text(skill_file)
    if text:
        # Only validate explicit backticked local refs: `scripts/...`, `references/...`, `assets/...`
        code_spans = re.findall(r'`([^`]+)`', text)
        ref_patterns = [
            span.strip() for span in code_spans
            if re.fullmatch(r'(?:scripts|references|assets)/[A-Za-z0-9_./-]+\.[A-Za-z0-9]+', span.strip())
        ]
        for ref in sorted(set(ref_patterns)):
            ref_path = skill_dir / ref
            if not is_safe_subpath(skill_dir, ref_path):
                results.append({
                    "skill": skill_name,
                    "test": "internal_ref",
                    "status": "FAIL",
                    "message": f"Path traversal detected: {ref}",
                })
            elif ref_path.is_file() or ref_path.is_dir():
                results.append({
                    "skill": skill_name,
                    "test": "internal_ref",
                    "status": "PASS",
                    "message": f"Reference exists: {ref}",
                })
            else:
                results.append({
                    "skill": skill_name,
                    "test": "internal_ref",
                    "status": "FAIL",
                    "message": f"Missing referenced file: {ref}",
                })

    # 4. Compile Python scripts
    scripts_dir = skill_dir / "scripts"
    if scripts_dir.is_dir():
        for py_file in sorted(scripts_dir.glob("*.py")):
            try:
                py_compile.compile(str(py_file), doraise=True)
                results.append({
                    "skill": skill_name,
                    "test": "py_compile",
                    "status": "PASS",
                    "message": f"Compiled OK: scripts/{py_file.name}",
                })
            except py_compile.PyCompileError as e:
                results.append({
                    "skill": skill_name,
                    "test": "py_compile",
                    "status": "FAIL",
                    "message": f"Compile error in scripts/{py_file.name}: {e}",
                })

    # 5. Strict: check broken markdown links
    if strict and text:
        # Find markdown links [text](path)
        md_links = re.findall(r'\[([^\]]*)\]\(([^)]+)\)', text)
        for link_text, link_target in md_links:
            # Skip external links
            if link_target.startswith("http://") or link_target.startswith("https://"):
                continue
            # Skip anchor-only links
            if link_target.startswith("#"):
                continue
            # Resolve relative path (strip fragment/query before check)
            base_target = link_target.split("#")[0].split("?")[0]
            if not base_target:
                continue
            target_path = skill_dir / base_target
            if not is_safe_subpath(skill_dir, target_path):
                results.append({
                    "skill": skill_name,
                    "test": "markdown_link",
                    "status": "FAIL",
                    "message": f"Path traversal detected: [{link_text}]({link_target})",
                })
            elif target_path.is_file() or target_path.is_dir():
                results.append({
                    "skill": skill_name,
                    "test": "markdown_link",
                    "status": "PASS",
                    "message": f"Link OK: [{link_text}]({link_target})",
                })
            else:
                results.append({
                    "skill": skill_name,
                    "test": "markdown_link",
                    "status": "FAIL",
                    "message": f"Broken link: [{link_text}]({link_target})",
                })

    return results


# ---------------------------------------------------------------------------
# Report formatter
# ---------------------------------------------------------------------------

def format_console_table(all_results: list[dict]) -> str:
    """Format results as a console table."""
    lines = []
    lines.append(f"{'Skill':<25} {'Test':<20} {'Status':<8} Message")
    lines.append("-" * 90)

    for r in all_results:
        status_icon = "✅" if r["status"] == "PASS" else "❌"
        lines.append(
            f"{r['skill']:<25} {r['test']:<20} {status_icon:<8} {r['message']}"
        )

    lines.append("-" * 90)

    pass_count = sum(1 for r in all_results if r["status"] == "PASS")
    fail_count = sum(1 for r in all_results if r["status"] == "FAIL")
    lines.append(f"Total: {pass_count} passed, {fail_count} failed")

    return "\n".join(lines)


def format_report_md(all_results: list[dict]) -> str:
    """Format results as a Markdown report."""
    lines = ["# Skill Smoke Test Report", ""]

    pass_count = sum(1 for r in all_results if r["status"] == "PASS")
    fail_count = sum(1 for r in all_results if r["status"] == "FAIL")
    lines.append(f"**Summary:** {pass_count} passed, {fail_count} failed")
    lines.append("")

    # Group by skill
    skills = {}
    for r in all_results:
        skills.setdefault(r["skill"], []).append(r)

    for skill_name, results in sorted(skills.items()):
        skill_fails = sum(1 for r in results if r["status"] == "FAIL")
        icon = "❌" if skill_fails > 0 else "✅"
        lines.append(f"## {icon} {skill_name}")
        lines.append("")
        for r in results:
            status = "✅ PASS" if r["status"] == "PASS" else "❌ FAIL"
            lines.append(f"- {status}: **{r['test']}** — {r['message']}")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Skill smoke test — validates skill structure and references",
    )
    parser.add_argument(
        "--all", action="store_true",
        help="Test all skills in the skills/ directory",
    )
    parser.add_argument(
        "--skill", type=str, default=None,
        help="Test a specific skill by name",
    )
    parser.add_argument(
        "--strict", action="store_true",
        help="Enable strict mode: detect broken markdown links",
    )
    parser.add_argument(
        "--skills-dir", type=str, default="skills",
        help="Path to skills directory (default: skills/)",
    )
    parser.add_argument(
        "--report-out", type=str, default="memory/skill-smoke-report.md",
        help="Output report file path (default: memory/skill-smoke-report.md)",
    )

    args = parser.parse_args()

    skills_dir = Path(args.skills_dir)

    if not skills_dir.is_dir():
        print(f"ERROR: Skills directory not found: {skills_dir}", file=sys.stderr)
        sys.exit(1)

    # Determine which skills to test
    skill_dirs = []
    if args.skill:
        target = skills_dir / args.skill
        if not target.is_dir():
            print(f"ERROR: Skill not found: {args.skill}", file=sys.stderr)
            sys.exit(1)
        skill_dirs.append(target)
    elif args.all:
        for d in sorted(skills_dir.iterdir()):
            if d.is_dir() and not d.name.startswith("_") and not d.name.startswith("."):
                if not (d / "SKILL.md").is_file():
                    continue
                skill_dirs.append(d)
    else:
        print("ERROR: Specify --all or --skill <name>", file=sys.stderr)
        sys.exit(1)

    if not skill_dirs:
        print("No skills found to test.", file=sys.stderr)
        sys.exit(1)

    # Run smoke tests
    all_results = []
    for sd in skill_dirs:
        all_results.extend(smoke_test_skill(sd, strict=args.strict))

    # Print console table
    print(format_console_table(all_results))

    # Write report
    report_path = Path(args.report_out)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(format_report_md(all_results), encoding="utf-8")
    print(f"\nReport written to {args.report_out}")

    # Exit code
    fail_count = sum(1 for r in all_results if r["status"] == "FAIL")
    if fail_count > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
memory_tier.py — Memory tiering: classify memory files vào hot/warm/cold.
Hot: modified trong 3 ngày | Warm: 4-14 ngày | Cold: > 14 ngày

Usage:
    python3 tools/memory_tier.py --status
    python3 tools/memory_tier.py --archive-cold
    python3 tools/memory_tier.py --report
"""
import argparse
import os
import shutil
from pathlib import Path
from datetime import datetime, timezone

WORKSPACE = Path(os.environ.get("OPENCLAW_WORKSPACE", "/root/.openclaw/workspace"))
MEMORY_DIR = WORKSPACE / "memory"
ARCHIVE_DIR = MEMORY_DIR / "archive"

PROTECTED = {"working-context.md", "media.db", "perplexity_budget.json"}

def classify(mtime_days):
    if mtime_days <= 3: return "🔥 hot"
    elif mtime_days <= 14: return "🌡 warm"
    else: return "🧊 cold"

def scan_files():
    now = datetime.now(timezone.utc)
    results = []
    for f in sorted(MEMORY_DIR.iterdir()):
        if f.is_file() and f.suffix in (".md", ".json", ".txt"):
            mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc)
            days = (now - mtime).days
            results.append({
                "name": f.name,
                "path": f,
                "days": days,
                "tier": classify(days),
                "size": f.stat().st_size,
                "protected": f.name in PROTECTED or f.name == "MEMORY.md"
            })
    return results

def cmd_status(files):
    hot = [f for f in files if "hot" in f["tier"]]
    warm = [f for f in files if "warm" in f["tier"]]
    cold = [f for f in files if "cold" in f["tier"]]
    print(f"🔥 Hot ({len(hot)}): {', '.join(f['name'] for f in hot)}")
    print(f"🌡 Warm ({len(warm)}): {', '.join(f['name'] for f in warm)}")
    print(f"🧊 Cold ({len(cold)}): {', '.join(f['name'] for f in cold)}")

def cmd_report(files):
    print("| File | Last Modified | Tier | Size |")
    print("|------|--------------|------|------|")
    for f in files:
        print(f"| {f['name']} | {f['days']}d ago | {f['tier']} | {f['size']} bytes |")

def cmd_archive_cold(files):
    ARCHIVE_DIR.mkdir(exist_ok=True)
    archived = []
    for f in files:
        if "cold" in f["tier"] and not f["protected"]:
            # Only archive daily logs pattern YYYY-MM-DD.md
            import re
            if re.match(r'^\d{4}-\d{2}-\d{2}\.md$', f["name"]):
                dest = ARCHIVE_DIR / f["name"]
                shutil.move(str(f["path"]), str(dest))
                archived.append(f["name"])
    if archived:
        print(f"Archived {len(archived)} files: {', '.join(archived)}")
    else:
        print("No cold daily log files to archive.")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--report", action="store_true")
    parser.add_argument("--archive-cold", action="store_true")
    args = parser.parse_args()
    
    files = scan_files()
    if args.status:
        cmd_status(files)
    elif args.report:
        cmd_report(files)
    elif args.archive_cold:
        cmd_archive_cold(files)
    else:
        parser.print_help()

if __name__ == "__main__":
    main()

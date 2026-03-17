# EDITOR_README.md — editor_pack_v3_2026-03-17

> Self-scan session 2026-03-17 ~14:55 UTC  
> Regression: 11/11 PASS ✅  
> Issues found: 6 (2 high, 2 medium, 2 low)

---

## Task Summary

| ID | Priority | Description | Files |
|----|----------|-------------|-------|
| P1 | 🔴 HIGH | Fix fragile relative imports in 6 tools | 6 files |
| P2 | 🟡 MED | Replace hardcoded `/root/.openclaw/workspace` with env var | 17 files |
| P3 | 🟡 MED | Implement Drive upload in `openclaw_backup.py` | 1 file |
| P4 | 🟢 LOW | Fix 4 SKILL.md that ref nonexistent scripts — update docs | 4 files |
| P5 | 🟢 LOW | Add `## Trigger` section to `ads-insight-auto/SKILL.md` | 1 file |

---

## P1 🔴 — Fix Fragile Relative Imports (6 files)

### Problem
These 6 tools use `from _common import ...` or `from _skill_parser import ...` which only works when `cwd=tools/`. Running from workspace root → `ModuleNotFoundError`.

### Files affected
```
tools/skill_smoke_test.py     → from _skill_parser import ...
tools/session_snapshot.py     → from _common import ...
tools/skill_health.py         → from _common import ...
tools/scan_skills_local.py    → from _skill_parser import ...
tools/api_usage.py            → from _common import ...
tools/generate_skill_manifest.py → from _skill_parser import ...
```

### Fix pattern
Add sys.path injection **before** the import, like this:

```python
import sys
from pathlib import Path
# Ensure tools/ is on path regardless of cwd
_TOOLS_DIR = Path(__file__).resolve().parent
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from _common import (...)  # now always works
```

Apply this pattern to all 6 files. The injection block goes right before the `from _common` or `from _skill_parser` line.

### Acceptance criteria
- [ ] All 6 files: `python3 tools/<file>.py --help` works from workspace root (no ModuleNotFoundError)
- [ ] No change to logic, only sys.path injection added

---

## P2 🟡 — Replace Hardcoded Workspace Paths (17 files)

### Problem
17 files hardcode `/root/.openclaw/workspace` instead of using env var. Breaks on any deployment with different path.

### Files + lines affected
```
skills/antrua/scripts/antrua.py                     (line 27)
skills/apps-script-deployer-lite/scripts/deploy.py  (line 23)
skills/persistent-memory/scripts/mem_manager.py     (line 24)
skills/rag-kit/scripts/kb_manager.py                (line 24)
skills/smart-memory/scripts/extract_facts.py        (line 15)
skills/yt-content/scripts/yt_download.py            (line 14)
tools/api_key_rotator.py                            (line 18)
tools/connect_ga_gsc.py                             (lines 89, 93)
tools/drive_media_tools.py                          (line 26)
tools/fb_comment_sync.py                            (line 26)
tools/fb_page_comment.py                            (line 35)
tools/gmail_auth.py                                 (lines 8, 9)
tools/linkedin_tools.py                             (lines 67, 97, 98)
tools/memory_tier.py                                (line 17)
tools/openclaw_backup.py                            (line 19)
tools/pancake_webhook.py                            (line 10)
tools/reader_tools.py                               (line 19)
```

### Fix pattern
Replace every hardcoded path with:

```python
WORKSPACE = Path(os.environ.get("OPENCLAW_WORKSPACE", "/root/.openclaw/workspace"))
```

- For tools/: usually near top of file where WORKSPACE is defined
- For skills/scripts/: same pattern
- The fallback `/root/.openclaw/workspace` is intentional (backwards compat)

### Acceptance criteria
- [ ] All 17 files: no bare `/root/.openclaw/workspace` string literal (only in the fallback default)
- [ ] `grep -r "'/root/.openclaw/workspace'" tools/ skills/` returns 0 results (except default fallback lines)

---

## P3 🟡 — Implement Drive Upload in `openclaw_backup.py`

### Problem
`openclaw_backup.py` creates encrypted backup tarball but has a TODO stub for Drive upload:
```python
# TODO: Upload to Google Drive using drive API
print(f"   ⚠️ Upload implementation pending — use drive_media_tools.py or manual upload")
```

### Fix
Implement Drive upload using existing `drive_media_tools.py` functions.

**Reference file:** `current_files/drive_media_tools.py` (included in this pack)  
**Target file:** `current_files/openclaw_backup.py` (line 136)

Expected upload logic:
```python
# After creating the tarball (tar_path exists at this point)
from drive_media_tools import upload_file_to_drive

result = upload_file_to_drive(
    file_path=str(tar_path),
    folder_id=folder_id,
    mime_type="application/gzip"
)
if result.get("id"):
    print(f"✅ Uploaded to Drive: {result['id']}")
else:
    print(f"❌ Drive upload failed: {result}")
    sys.exit(1)
```

Check `drive_media_tools.py` for exact function signature before implementing.

### Acceptance criteria
- [ ] `python3 tools/openclaw_backup.py --help` runs without error
- [ ] TODO comment removed, replaced with working implementation
- [ ] If Drive creds not available → graceful error message (not crash)

---

## P4 🟢 — Fix SKILL.md with Phantom Script References

### Problem
4 skills reference `.py` files in their SKILL.md but the `scripts/` folder doesn't exist. These are documentation inconsistencies (no actual scripts needed, SKILL.md just uses wrong example names).

### Files to update (documentation fix only — NO new scripts needed)
```
skills/context-warning/SKILL.md    → mentions "file.py" (generic placeholder, remove/fix)
skills/daytona-sandbox/SKILL.md    → mentions "script.py" (generic placeholder, remove/fix)
skills/parallel-file-ownership-lite/SKILL.md → mentions "tools/example_tool.py" (example only, clarify)
skills/test-orchestrator-lite/SKILL.md → test-orchestrator-lite references
```

**Note:** `auto-save`, `drive-media`, `openclaw-backup`, `reader-adapter`, `skill-catalog-audit-lite` actually reference real existing tools (e.g. `tools/drive_media_tools.py`) — those are correct, no fix needed.

### Fix
- Remove or replace generic placeholder `.py` filenames (file.py, script.py, example_tool.py) with accurate descriptions
- No new files needed — just update docs

### Acceptance criteria
- [ ] No misleading script references in those 4 SKILL.md files
- [ ] Content still accurate (just remove/clarify phantom references)

---

## P5 🟢 — Add `## Trigger` to `ads-insight-auto/SKILL.md`

### Problem
`ads-insight-auto/SKILL.md` is the only skill missing `## Trigger` section (all 65 others have it after today's deploys).

### Current file
`current_files/ads-insight-auto_SKILL.md`

### Fix
Add this section after the frontmatter block (before `## Workflow`):

```markdown
## Trigger

- Cron: 19h hằng ngày (automatic)
- Manual: `/ads-insight` hoặc "chạy ads insight"
- Auto: Khi agent phát hiện cần phân tích ads performance
```

### Acceptance criteria
- [ ] `## Trigger` section present in `skills/ads-insight-auto/SKILL.md`

---

## Delivery Checklist

After all tasks done:

```
[ ] P1: python3 tools/session_snapshot.py --dry-run (from workspace root) → no import error
[ ] P1: python3 tools/skill_health.py --no-telegram → runs ok
[ ] P2: grep -rn "'/root/.openclaw/workspace'" tools/ skills/ → only fallback default lines
[ ] P3: grep -n 'TODO' tools/openclaw_backup.py → 0 results
[ ] P4: 4 SKILL.md updated (docs only)
[ ] P5: grep '## Trigger' skills/ads-insight-auto/SKILL.md → found
[ ] Regression: python3 tools/regression_suite.py → 11/11 PASS
```

Push to: `https://github.com/minhtranquang1993/editor_pack_2026-03-17` (folder `/v3/`)

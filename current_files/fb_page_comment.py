#!/usr/bin/env python3
"""
FB Page Auto Comment
- Comment bài FB Page DND Sài Gòn theo template (text + ảnh từ Google Drive)
- Auto-sync templates từ Drive khi cache > 24h
- Idempotent: không comment trùng cùng 1 post

Usage:
  python3 tools/fb_page_comment.py --post-url "https://www.facebook.com/..." --templates all
  python3 tools/fb_page_comment.py --post-url "https://www.facebook.com/..." --templates 1,3
  python3 tools/fb_page_comment.py --post-url "https://www.facebook.com/..." --dry-run
  python3 tools/fb_page_comment.py --sync-templates
"""

import argparse
import glob
import mimetypes
import json
import os
import random
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

import requests

# ---------------------------------------------------------------------------
# Paths (VPS workspace)
# ---------------------------------------------------------------------------
WORKSPACE = Path(os.environ.get("OPENCLAW_WORKSPACE", "/root/.openclaw/workspace"))
CREDENTIALS_DIR = WORKSPACE / "credentials"
FB_TOKEN_PATH = CREDENTIALS_DIR / "fb_page_token.txt"
FB_CONFIG_PATH = CREDENTIALS_DIR / "fb_page_config.json"

CACHE_DIR = WORKSPACE / "memory/fb_comment_cache"
COMMENTS_CACHE = CACHE_DIR / "comments.json"
LAST_SYNC_FILE = CACHE_DIR / "last_sync.json"
LOG_FILE = WORKSPACE / "memory/fb_comment_log.json"

FB_GRAPH_API = "https://graph.facebook.com/v21.0"

# Retry config for FB rate limit
MAX_RETRIES = 3
RETRY_DELAY_SEC = 60

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_fb_token() -> str:
    """Load FB Page access token from credentials file."""
    if not FB_TOKEN_PATH.exists():
        raise FileNotFoundError(
            f"FB Page token not found: {FB_TOKEN_PATH}\n"
            "→ Cần tạo file fb_page_token.txt với Page Access Token"
        )
    token = FB_TOKEN_PATH.read_text(encoding="utf-8").strip()
    if not token:
        raise ValueError("fb_page_token.txt is empty")
    return token


def load_fb_config() -> Dict[str, Any]:
    """Load FB Page config (page_id, page_name, etc.)."""
    if not FB_CONFIG_PATH.exists():
        raise FileNotFoundError(f"FB Page config not found: {FB_CONFIG_PATH}")
    return load_json(FB_CONFIG_PATH)


# ---------------------------------------------------------------------------
# URL Parsing
# ---------------------------------------------------------------------------

def normalize_post_id(raw_id: str) -> str:
    """
    Normalize post_id to a canonical form for consistent idempotency.

    Strips 'PAGE_ID_' prefix if present, keeping only the story/object ID.
    This ensures different URL formats for the same post produce the same key.
    """
    # If format is PAGE_ID_STORY_ID, extract STORY_ID
    if "_" in raw_id:
        parts = raw_id.split("_", 1)
        if parts[1].isdigit():
            return parts[1]
    return raw_id


def parse_post_id(url: str) -> str:
    """
    Parse fb post_id from various URL formats.

    Supported:
      https://www.facebook.com/PageName/posts/1234567890
      https://www.facebook.com/permalink.php?story_fbid=123&id=456
      https://www.facebook.com/photo?fbid=123
      https://www.facebook.com/share/p/AbcXyz   (pfbid format)
      https://www.facebook.com/watch?v=123
      https://www.facebook.com/reel/123
      https://www.facebook.com/1234567890/posts/9876543210
    """
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    qs = parse_qs(parsed.query)

    # /permalink.php?story_fbid=XXX&id=YYY
    if "permalink.php" in path:
        story_fbid = qs.get("story_fbid", [None])[0]
        if story_fbid:
            return story_fbid
        raise ValueError(f"Cannot parse story_fbid from: {url}")

    # /photo?fbid=XXX  or  /photo/?fbid=XXX
    if "/photo" in path:
        fbid = qs.get("fbid", [None])[0]
        if fbid:
            return fbid
        raise ValueError(f"Cannot parse fbid from photo URL: {url}")

    # /watch?v=XXX
    if "/watch" in path:
        v = qs.get("v", [None])[0]
        if v:
            return v
        raise ValueError(f"Cannot parse video id from watch URL: {url}")

    # /reel/XXX
    m = re.search(r"/reel/(\d+)", path)
    if m:
        return m.group(1)

    # /share/p/PFBID  (pfbid dạng mới)
    # NOTE: pfbid tokens are NOT Graph API object IDs and cannot be used
    # with /{post_id}/comments endpoint. Reject with helpful message.
    m = re.search(r"/share/p/([A-Za-z0-9]+)", path)
    if m:
        raise ValueError(
            f"Share link detected (pfbid: {m.group(1)}).\n"
            "→ /share/p/ URLs use opaque tokens that are NOT valid Graph API post IDs.\n"
            "→ Hãy dùng URL dạng /PageName/posts/POST_ID hoặc /permalink.php?story_fbid=ID"
        )

    # /PageName/posts/POST_ID  or  /PAGE_ID/posts/POST_ID
    m = re.search(r"/posts/(\d+)", path)
    if m:
        return m.group(1)

    # /PageName/photos/a.XXX/YYY
    m = re.search(r"/photos/[^/]+/(\d+)", path)
    if m:
        return m.group(1)

    # /PageName/videos/XXX
    m = re.search(r"/videos/(\d+)", path)
    if m:
        return m.group(1)

    # Fallback: last numeric segment in path
    m = re.search(r"/(\d{5,})\/?$", path)
    if m:
        return m.group(1)

    raise ValueError(
        f"Cannot parse post_id from URL: {url}\n"
        "Supported formats:\n"
        "  /PageName/posts/POST_ID\n"
        "  /permalink.php?story_fbid=ID&id=PAGE_ID\n"
        "  /photo?fbid=ID\n"
        "  /share/p/PFBID\n"
        "  /watch?v=ID\n"
        "  /reel/ID"
    )


def ensure_post_object_id(post_id: str, page_id: str) -> str:
    """
    Ensure post_id is in Graph object form PAGE_ID_POST_ID.

    Why:
    - Endpoint /{post_id}/comments expects object id format for page posts.
    - URLs like /PageName/posts/122... only give story id, not full object id.
    """
    if "_" in post_id:
        return post_id
    if post_id.isdigit():
        return f"{page_id}_{post_id}"
    return post_id


# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------

def auto_sync(force: bool = False) -> None:
    """Auto-sync templates if cache is stale (>24h) or force=True."""
    sync_script = WORKSPACE / "tools/fb_comment_sync.py"
    if not sync_script.exists():
        # Try relative path (dev environment)
        sync_script = Path(__file__).parent / "fb_comment_sync.py"

    if not sync_script.exists():
        print("⚠️  fb_comment_sync.py not found, skipping sync")
        return

    needs = force
    if not needs:
        if not LAST_SYNC_FILE.exists():
            needs = True
        else:
            try:
                from datetime import timedelta
                info = load_json(LAST_SYNC_FILE)
                last = datetime.fromisoformat(info["synced_at"])
                needs = datetime.now(timezone.utc) - last > timedelta(hours=24)
            except (KeyError, ValueError):
                needs = True

    if not needs:
        return

    print("🔄 Auto-syncing templates from Drive...")
    cmd = [sys.executable, str(sync_script)]
    if force:
        cmd.append("--force")

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"⚠️  Sync had warnings:\n{result.stderr or result.stdout}")
    else:
        print(result.stdout)


# ---------------------------------------------------------------------------
# Log (idempotent check)
# ---------------------------------------------------------------------------

def load_log() -> List[Dict[str, Any]]:
    if not LOG_FILE.exists():
        return []
    try:
        return load_json(LOG_FILE)
    except (json.JSONDecodeError, ValueError):
        return []


def save_log(entries: List[Dict[str, Any]]) -> None:
    save_json(LOG_FILE, entries)


def already_commented(post_id: str) -> bool:
    """Check if we already successfully commented on this post.

    Only considers entries with at least one successful visible comment.
    Dry-run and all-failed entries are ignored.
    """
    normalized = normalize_post_id(post_id)
    entries = load_log()
    for entry in entries:
        entry_id = normalize_post_id(entry.get("post_id", ""))
        if entry_id == normalized:
            comments = entry.get("comments", [])
            if any(c.get("status") in {"ok", "visible_ok"} for c in comments):
                return True
    return False


# ---------------------------------------------------------------------------
# FB API — Comment
# ---------------------------------------------------------------------------

def fb_api_request(
    method: str,
    endpoint: str,
    token: str,
    data: Optional[Dict[str, Any]] = None,
    files: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Make FB Graph API request with retry on rate limit."""
    url = f"{FB_GRAPH_API}/{endpoint}"

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            if method == "POST":
                if files:
                    # Multipart upload (photo)
                    r = requests.post(url, data=data, files=files, timeout=120)
                else:
                    r = requests.post(url, data=data, timeout=60)
            else:
                r = requests.get(url, params=data, timeout=60)

            resp = r.json()

            # Check for FB errors
            if "error" in resp:
                error = resp["error"]
                code = error.get("code", 0)
                message = error.get("message", "Unknown error")

                # Rate limit — retry
                if code == 613 or code == 32:
                    if attempt < MAX_RETRIES:
                        print(f"⏳ Rate limited (attempt {attempt}/{MAX_RETRIES}). Retrying in {RETRY_DELAY_SEC}s...")
                        time.sleep(RETRY_DELAY_SEC)
                        continue
                    raise Exception(f"FB rate limit after {MAX_RETRIES} retries: {message}")

                # Token expired
                if code == 190:
                    raise Exception(
                        f"FB token expired/invalid: {message}\n"
                        f"→ Cần refresh fb_page_token.txt"
                    )

                raise Exception(f"FB API error ({code}): {message}")

            return resp

        except requests.exceptions.RequestException as e:
            if attempt < MAX_RETRIES:
                print(f"⏳ Request failed (attempt {attempt}/{MAX_RETRIES}): {e}. Retrying in 10s...")
                time.sleep(10)
                continue
            raise

    return {}  # Should never reach here


def upload_photo_unpublished(
    page_id: str,
    image_path: Path,
    token: str,
) -> str:
    """Upload photo as unpublished to get photo_id for comment attachment."""
    if not image_path.exists():
        raise FileNotFoundError(f"Image not found: {image_path}")

    with open(image_path, "rb") as img:
        resp = fb_api_request(
            "POST",
            f"{page_id}/photos",
            token,
            data={"published": "false", "access_token": token},
            files={"source": (image_path.name, img, mimetypes.guess_type(str(image_path))[0] or "application/octet-stream")},
        )

    photo_id = resp.get("id")
    if not photo_id:
        raise Exception(f"Failed to upload photo, no id in response: {resp}")
    return photo_id


def post_comment(
    post_id: str,
    message: str,
    token: str,
    attachment_id: Optional[str] = None,
) -> str:
    """Post a comment to a FB post. Returns comment_id."""
    data: Dict[str, Any] = {
        "message": message,
        "access_token": token,
    }
    if attachment_id:
        data["attachment_id"] = attachment_id

    resp = fb_api_request("POST", f"{post_id}/comments", token, data=data)
    comment_id = resp.get("id")
    if not comment_id:
        raise Exception(f"Failed to post comment, no id in response: {resp}")
    return comment_id


def verify_comment_visible(post_id: str, comment_id: str, token: str) -> Tuple[bool, Optional[str]]:
    """Check if a comment_id is visible on post comment stream."""
    try:
        resp = fb_api_request(
            "GET",
            f"{post_id}/comments",
            token,
            data={
                "fields": "id,message,from,created_time,is_hidden",
                "filter": "stream",
                "limit": 200,
                "access_token": token,
            },
        )
    except Exception as e:
        return False, f"verify_error: {e}"

    ids = {c.get("id") for c in resp.get("data", [])}
    return (comment_id in ids), None


# ---------------------------------------------------------------------------
# Main logic
# ---------------------------------------------------------------------------

def cleanup_cache_images() -> None:
    """Delete cached images (jpg/png/jpeg/gif/webp) from CACHE_DIR.

    Keeps comments.json, last_sync.json, and any images still referenced
    by active templates to avoid text-only degradation on subsequent runs
    within the 24h sync window.
    """
    # Build set of image filenames still referenced by templates
    referenced_images: set = set()
    if COMMENTS_CACHE.exists():
        try:
            templates = load_json(COMMENTS_CACHE)
            for tmpl in templates:
                img = tmpl.get("image_filename")
                if img:
                    referenced_images.add(img)
        except (json.JSONDecodeError, ValueError):
            pass  # If cache is corrupt, clean everything

    patterns = ["*.jpg", "*.jpeg", "*.png", "*.gif", "*.webp"]
    removed = 0
    kept = 0
    for pat in patterns:
        for img_path in glob.glob(str(CACHE_DIR / pat)):
            img_name = os.path.basename(img_path)
            if img_name in referenced_images:
                kept += 1
                continue  # Keep images still needed by templates
            try:
                os.remove(img_path)
                removed += 1
            except OSError:
                pass
    if removed or kept:
        print(f"🧹 Cleaned {removed} cached image(s), kept {kept} referenced image(s)")
    else:
        print("🧹 No cached images to clean")


def run_comment(
    post_url: str,
    template_ids: Optional[List[int]],
    dry_run: bool,
    delay: int,
    force_sync: bool,
    max_comments: Optional[int] = None,
    shuffle: bool = False,
    cleanup_cache: bool = True,
    verify_visible: bool = True,
) -> None:
    """Main comment execution flow."""

    # 1. Auto-sync
    auto_sync(force=force_sync)

    # 2. Parse post_id
    print(f"\n🔗 Post URL: {post_url}")
    try:
        parsed_post_id = parse_post_id(post_url)
    except ValueError as e:
        print(f"❌ {e}")
        sys.exit(1)

    # 3. Load token + config
    try:
        token = load_fb_token()
        config = load_fb_config()
    except (FileNotFoundError, ValueError) as e:
        print(f"❌ {e}")
        sys.exit(1)

    page_id = config["page_id"]
    page_name = config.get("page_name", page_id)

    # Build canonical Graph object id (PAGE_ID_POST_ID)
    post_id = ensure_post_object_id(parsed_post_id, page_id)

    print(f"📌 Parsed Post ID: {parsed_post_id}")
    print(f"📌 Object Post ID: {post_id}")
    print(f"📄 Page: {page_name} ({page_id})")

    # 4. Check idempotent
    if not dry_run and already_commented(post_id):
        print(f"\n⚠️  Đã comment bài này rồi (post_id: {post_id})")
        print("→ Bỏ qua. Nếu muốn comment lại, xóa entry trong memory/fb_comment_log.json")
        return

    # 5. Load templates
    if not COMMENTS_CACHE.exists():
        print("❌ No templates cached. Run: python3 tools/fb_comment_sync.py")
        sys.exit(1)

    try:
        templates = load_json(COMMENTS_CACHE)
    except (json.JSONDecodeError, ValueError) as e:
        print(f"❌ Invalid comments.json: {e}")
        sys.exit(1)

    # Filter templates
    if template_ids is not None:
        templates = [t for t in templates if t.get("id") in template_ids]
    else:
        templates = [t for t in templates if t.get("enabled", True)]

    if not templates:
        print("❌ No templates to post (all disabled or filtered out)")
        sys.exit(1)

    # Shuffle templates if requested (before max-comments truncation)
    if shuffle:
        random.shuffle(templates)
        print("🔀 Templates shuffled")

    # Truncate to max-comments if specified
    if max_comments is not None and max_comments > 0:
        templates = templates[:max_comments]
        print(f"✂️  Limited to {max_comments} comment(s)")

    print(f"\n📝 {len(templates)} template(s) to comment:")

    # 6. Execute comments
    log_entry: Dict[str, Any] = {
        "post_url": post_url,
        "post_id": post_id,
        "timestamp": now_iso(),
        "comments": [],
    }

    # Duplicate-safe: track seen texts within this run
    seen_texts: set = set()

    for i, tmpl in enumerate(templates):
        tmpl_id = tmpl.get("id", i + 1)
        text = tmpl.get("text", "")
        img_name = tmpl.get("image_filename")
        comment_result: Dict[str, Any] = {
            "template_id": tmpl_id,
            "status": "pending",
        }

        print(f"\n--- Template #{tmpl_id} ---")
        print(f"  Text: {text[:80]}{'...' if len(text) > 80 else ''}")
        if img_name:
            print(f"  Image: {img_name}")

        # Duplicate-safe: skip templates with identical text in this run
        text_key = text.strip()
        if text_key in seen_texts:
            comment_result["status"] = "skipped_duplicate"
            print(f"  ⚠️  Skipping template #{tmpl_id} (duplicate text in this run)")
            log_entry["comments"].append(comment_result)
            continue
        seen_texts.add(text_key)

        if dry_run:
            comment_result["status"] = "dry_run"
            print("  → [DRY RUN] Skipped")
            log_entry["comments"].append(comment_result)
            continue

        try:
            # Upload image if needed
            attachment_id = None
            if img_name:
                img_path = CACHE_DIR / img_name
                if img_path.exists():
                    print(f"  ⬆️  Uploading {img_name}...")
                    attachment_id = upload_photo_unpublished(page_id, img_path, token)
                    print(f"  ✅ Photo ID: {attachment_id}")
                else:
                    print(f"  ⚠️  Image not found: {img_path}. Posting text only.")

            # Post comment
            print("  💬 Posting comment...")
            comment_id = post_comment(post_id, text, token, attachment_id)
            comment_result["comment_id"] = comment_id

            if verify_visible:
                visible, verify_err = verify_comment_visible(post_id, comment_id, token)
                if visible:
                    comment_result["status"] = "visible_ok"
                    print(f"  ✅ VISIBLE_OK: {comment_id}")
                else:
                    comment_result["status"] = "posted_but_hidden"
                    comment_result["error"] = verify_err or "comment not visible on post after posting"
                    print(f"  ⚠️  POSTED_BUT_HIDDEN: {comment_id}")
            else:
                comment_result["status"] = "ok"
                print(f"  ✅ Comment ID: {comment_id}")

        except Exception as e:
            comment_result["status"] = "failed"
            comment_result["error"] = str(e)
            print(f"  ❌ FAILED: {e}")

        log_entry["comments"].append(comment_result)

        # Delay between comments
        if i < len(templates) - 1 and not dry_run:
            print(f"  ⏳ Waiting {delay}s...")
            time.sleep(delay)

    # 7. Save log
    entries = load_log()
    entries.append(log_entry)
    save_log(entries)

    # 8. Cleanup cache images if requested
    if cleanup_cache and not dry_run:
        cleanup_cache_images()

    # Summary
    ok_count = sum(1 for c in log_entry["comments"] if c["status"] in {"ok", "visible_ok"})
    hidden_count = sum(1 for c in log_entry["comments"] if c["status"] == "posted_but_hidden")
    fail_count = sum(1 for c in log_entry["comments"] if c["status"] == "failed")
    dry_count = sum(1 for c in log_entry["comments"] if c["status"] == "dry_run")
    skip_count = sum(1 for c in log_entry["comments"] if c["status"] == "skipped_duplicate")

    print(f"\n{'=' * 40}")
    if dry_run:
        print(f"🏁 Dry run complete: {dry_count} template(s)")
    else:
        print(f"📊 Result: {ok_count} VISIBLE_OK / {hidden_count} POSTED_BUT_HIDDEN / {fail_count} FAILED")
    if skip_count:
        print(f"⏩ Skipped {skip_count} duplicate template(s)")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Auto-comment FB Page posts with templates (text + images from Google Drive)"
    )
    parser.add_argument(
        "--post-url",
        help="URL of the FB post to comment on"
    )
    parser.add_argument(
        "--templates",
        default="all",
        help="Template IDs: 'all' or comma-separated like '1,2,3' (default: all)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be commented without actually posting"
    )
    parser.add_argument(
        "--delay",
        type=int,
        default=3,
        help="Seconds to wait between comments (default: 3)"
    )
    parser.add_argument(
        "--force-sync",
        action="store_true",
        help="Force sync templates from Drive before commenting"
    )
    parser.add_argument(
        "--max-comments",
        type=int,
        default=None,
        help="Maximum number of comments to post (default: all)"
    )
    parser.add_argument(
        "--shuffle",
        action="store_true",
        help="Shuffle template order before commenting"
    )
    parser.add_argument(
        "--no-cleanup-cache",
        action="store_true",
        help="Do not delete cached images after commenting (default: cleanup is ON)"
    )
    parser.add_argument(
        "--sync-templates",
        action="store_true",
        help="Only sync templates, do not comment"
    )
    parser.add_argument(
        "--no-verify-visible",
        action="store_true",
        help="Disable visibility verification after posting"
    )

    args = parser.parse_args()

    # Sync-only mode
    if args.sync_templates:
        auto_sync(force=True)
        return

    # Validate delay
    if args.delay < 0:
        parser.error("--delay must be >= 0")

    # Require post-url for comment mode
    if not args.post_url:
        parser.error("--post-url is required (or use --sync-templates)")

    # Parse template IDs
    template_ids: Optional[List[int]] = None
    if args.templates != "all":
        try:
            template_ids = [int(x.strip()) for x in args.templates.split(",")]
        except ValueError:
            parser.error("--templates must be 'all' or comma-separated integers like '1,2,3'")

    run_comment(
        post_url=args.post_url,
        template_ids=template_ids,
        dry_run=args.dry_run,
        delay=args.delay,
        force_sync=args.force_sync,
        max_comments=args.max_comments,
        shuffle=args.shuffle,
        cleanup_cache=not args.no_cleanup_cache,
        verify_visible=not args.no_verify_visible,
    )


if __name__ == "__main__":
    main()

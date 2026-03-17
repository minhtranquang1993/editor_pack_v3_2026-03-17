#!/usr/bin/env python3
"""
FB Comment Template Sync
- Sync comment templates (comments.json + images) from Google Drive to local cache
- Reuse OAuth2 pattern from drive_media_tools.py

Usage:
  python3 tools/fb_comment_sync.py              # sync templates
  python3 tools/fb_comment_sync.py --force      # force re-download all
  python3 tools/fb_comment_sync.py --status     # show last sync info
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

# ---------------------------------------------------------------------------
# Paths (VPS workspace)
# ---------------------------------------------------------------------------
WORKSPACE = Path(os.environ.get("OPENCLAW_WORKSPACE", "/root/.openclaw/workspace"))
CRED_PATH = WORKSPACE / "credentials/google_workspace_credentials.json"
TOKEN_PATH = WORKSPACE / "credentials/google_workspace_token.json"

CACHE_DIR = WORKSPACE / "memory/fb_comment_cache"
COMMENTS_CACHE = CACHE_DIR / "comments.json"
LAST_SYNC_FILE = CACHE_DIR / "last_sync.json"

DRIVE_API = "https://www.googleapis.com/drive/v3"
DRIVE_FOLDER_ID = "1aNr4N9eUkqDlZTef8D1nKYkPnGpRqeGl"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Auth — reuse pattern from drive_media_tools.py
# ---------------------------------------------------------------------------

# Module-level token cache to avoid refreshing on every API call
_cached_token: Optional[str] = None


def refresh_access_token(force: bool = False) -> str:
    """Refresh Google OAuth2 access token using stored credentials.

    Caches the token for the duration of the process to avoid unnecessary refreshes.
    """
    global _cached_token
    if _cached_token and not force:
        return _cached_token

    if not CRED_PATH.exists():
        raise FileNotFoundError(f"Missing credentials: {CRED_PATH}")
    if not TOKEN_PATH.exists():
        raise FileNotFoundError(f"Missing token: {TOKEN_PATH}")

    creds = load_json(CRED_PATH)["installed"]
    token = load_json(TOKEN_PATH)
    refresh_token = token.get("refresh_token")
    if not refresh_token:
        raise ValueError("google_workspace_token.json has no refresh_token")

    resp = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": creds["client_id"],
            "client_secret": creds["client_secret"],
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=30,
    )
    resp.raise_for_status()
    new_tok = resp.json()

    token["access_token"] = new_tok["access_token"]
    token["expires_in"] = new_tok.get("expires_in")
    token["scope"] = new_tok.get("scope", token.get("scope"))
    token["token_type"] = new_tok.get("token_type", token.get("token_type"))
    token["updated_at"] = now_iso()
    save_json(TOKEN_PATH, token)

    _cached_token = token["access_token"]
    return _cached_token


def auth_headers() -> Dict[str, str]:
    return {"Authorization": f"Bearer {refresh_access_token()}"}


# ---------------------------------------------------------------------------
# Drive operations
# ---------------------------------------------------------------------------

def list_drive_folder(folder_id: str) -> List[Dict[str, Any]]:
    """List all files in a Google Drive folder."""
    all_files: List[Dict[str, Any]] = []
    page_token: Optional[str] = None

    while True:
        params: Dict[str, Any] = {
            "q": f"'{folder_id}' in parents and trashed=false",
            "fields": "nextPageToken,files(id,name,mimeType,size)",
            "pageSize": 100,
            "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true",
        }
        if page_token:
            params["pageToken"] = page_token

        r = requests.get(
            f"{DRIVE_API}/files",
            headers=auth_headers(),
            params=params,
            timeout=60,
        )
        r.raise_for_status()
        data = r.json()
        all_files.extend(data.get("files", []))
        page_token = data.get("nextPageToken")
        if not page_token:
            break

    return all_files


def download_file(file_id: str, dest_path: Path) -> None:
    """Download a file from Google Drive to local path."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    r = requests.get(
        f"{DRIVE_API}/files/{file_id}",
        headers=auth_headers(),
        params={"alt": "media"},
        timeout=120,
        stream=True,
    )
    r.raise_for_status()
    with open(dest_path, "wb") as f:
        for chunk in r.iter_content(chunk_size=8192):
            f.write(chunk)


# ---------------------------------------------------------------------------
# Sync logic
# ---------------------------------------------------------------------------

def needs_sync(max_age_hours: int = 24) -> bool:
    """Check if cache is older than max_age_hours."""
    if not LAST_SYNC_FILE.exists():
        return True
    try:
        info = load_json(LAST_SYNC_FILE)
        last = datetime.fromisoformat(info["synced_at"])
        return datetime.now(timezone.utc) - last > timedelta(hours=max_age_hours)
    except (KeyError, ValueError):
        return True


def sync_templates(force: bool = False) -> Dict[str, Any]:
    """
    Sync templates from Google Drive to local cache.

    Returns dict with sync results.
    """
    result = {
        "synced_at": now_iso(),
        "templates_count": 0,
        "images_downloaded": [],
        "errors": [],
    }

    print(f"📂 Listing files in Drive folder {DRIVE_FOLDER_ID}...")
    try:
        files = list_drive_folder(DRIVE_FOLDER_ID)
    except Exception as e:
        msg = f"Drive sync failed: {e}"
        print(f"❌ {msg}")
        result["errors"].append(msg)
        # Fall back to existing cache
        if COMMENTS_CACHE.exists():
            print("⚠️  Using existing cache")
        else:
            print("❌ No cache available")
        return result

    # Find comments.json
    comments_file = None
    image_files: Dict[str, str] = {}  # name -> file_id

    for f in files:
        if f["name"] == "comments.json":
            comments_file = f
        else:
            image_files[f["name"]] = f["id"]

    if not comments_file:
        msg = "comments.json not found in Drive folder"
        print(f"❌ {msg}")
        result["errors"].append(msg)
        return result

    # Download comments.json to temp file first, validate, then replace
    print("⬇️  Downloading comments.json...")
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp_comments = CACHE_DIR / "comments.json.tmp"
    try:
        download_file(comments_file["id"], tmp_comments)
    except Exception as e:
        msg = f"Failed to download comments.json: {e}"
        print(f"❌ {msg}")
        result["errors"].append(msg)
        if COMMENTS_CACHE.exists():
            print("⚠️  Keeping existing cache")
        return result

    # Validate JSON schema before replacing cache
    try:
        with open(tmp_comments, "r", encoding="utf-8") as f:
            templates = json.load(f)
        # Basic schema validation: must be a list of dicts with 'id' and 'text'
        if not isinstance(templates, list):
            raise ValueError("comments.json must be a JSON array")
        for tmpl in templates:
            if not isinstance(tmpl, dict) or "id" not in tmpl or "text" not in tmpl:
                raise ValueError(f"Invalid template entry: {tmpl}")
    except (json.JSONDecodeError, ValueError) as e:
        msg = f"Invalid comments.json from Drive: {e}"
        print(f"❌ {msg}")
        result["errors"].append(msg)
        tmp_comments.unlink(missing_ok=True)
        if COMMENTS_CACHE.exists():
            print("⚠️  Keeping existing valid cache")
        return result

    # Atomically replace cache with validated file
    import shutil
    shutil.move(str(tmp_comments), str(COMMENTS_CACHE))

    result["templates_count"] = len(templates)
    print(f"📄 Found {len(templates)} templates")

    for tmpl in templates:
        img_name = tmpl.get("image_filename")
        if not img_name:
            continue

        if img_name not in image_files:
            msg = f"Image '{img_name}' referenced in template {tmpl.get('id')} not found in Drive"
            print(f"⚠️  {msg}")
            result["errors"].append(msg)
            continue

        dest = CACHE_DIR / img_name
        # Skip if already exists and not force
        if dest.exists() and not force:
            print(f"✅ {img_name} (cached)")
            result["images_downloaded"].append(img_name)
            continue

        print(f"⬇️  Downloading {img_name}...")
        try:
            download_file(image_files[img_name], dest)
            result["images_downloaded"].append(img_name)
            print(f"✅ {img_name}")
        except Exception as e:
            msg = f"Failed to download {img_name}: {e}"
            print(f"❌ {msg}")
            result["errors"].append(msg)

    # Save sync metadata
    save_json(LAST_SYNC_FILE, {
        "synced_at": result["synced_at"],
        "templates_count": result["templates_count"],
        "images": result["images_downloaded"],
        "errors": result["errors"],
        "drive_folder_id": DRIVE_FOLDER_ID,
    })

    if result["errors"]:
        print(f"\n⚠️  Sync completed with {len(result['errors'])} warning(s)")
    else:
        print(f"\n✅ Sync completed — {result['templates_count']} templates, {len(result['images_downloaded'])} images")

    return result


def show_status() -> None:
    """Show last sync status."""
    if not LAST_SYNC_FILE.exists():
        print("❌ No sync data found. Run sync first.")
        return

    info = load_json(LAST_SYNC_FILE)
    print(f"📅 Last sync: {info.get('synced_at', 'unknown')}")
    print(f"📄 Templates: {info.get('templates_count', 0)}")
    print(f"🖼️  Images: {', '.join(info.get('images', [])) or 'none'}")
    if info.get("errors"):
        print(f"⚠️  Errors: {len(info['errors'])}")
        for e in info["errors"]:
            print(f"   - {e}")

    if needs_sync():
        print("\n⏰ Cache is stale (>24h). Run sync to update.")
    else:
        print("\n✅ Cache is fresh")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Sync FB comment templates from Google Drive to local cache"
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Force re-download all files (ignore cache)"
    )
    parser.add_argument(
        "--status", action="store_true",
        help="Show last sync info without syncing"
    )
    args = parser.parse_args()

    if args.status:
        show_status()
        return

    print("🔄 Syncing FB comment templates from Google Drive...\n")
    result = sync_templates(force=args.force)
    sys.exit(1 if result["errors"] else 0)


if __name__ == "__main__":
    main()

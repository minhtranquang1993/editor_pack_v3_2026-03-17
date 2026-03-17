#!/usr/bin/env python3
"""
openclaw_backup.py — Encrypted backup (AES-256-CBC) workspace → Google Drive.

Usage:
    python3 tools/openclaw_backup.py --drive-folder-id <FOLDER_ID>
    python3 tools/openclaw_backup.py                       # reads from config
    python3 tools/openclaw_backup.py --min-days 0          # force backup
"""

import argparse
import json
import os
import subprocess
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

WORKSPACE = Path(os.environ.get("OPENCLAW_WORKSPACE", "/root/.openclaw/workspace"))
CRED_DIR = WORKSPACE / "credentials"
PASSPHRASE_PATH = CRED_DIR / "openclaw_backup_passphrase.txt"
CONFIG_PATH = CRED_DIR / "backup_config.json"
STATE_PATH = WORKSPACE / "memory" / "backup_state.json"

ICT = timezone(timedelta(hours=7))


def load_passphrase() -> str:
    """Load or auto-generate passphrase."""
    if not PASSPHRASE_PATH.exists():
        import secrets
        passphrase = secrets.token_urlsafe(32)
        PASSPHRASE_PATH.parent.mkdir(parents=True, exist_ok=True)
        PASSPHRASE_PATH.write_text(passphrase)
        PASSPHRASE_PATH.chmod(0o600)
        print(f"⚠️ Generated new passphrase: {PASSPHRASE_PATH}")
    return PASSPHRASE_PATH.read_text().strip()


def load_config() -> dict:
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text())
    return {}


def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text())
        except Exception:
            pass
    return {"last_backup": None, "count": 0}


def save_state(state: dict):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2))


def should_skip(min_days: int, state: dict) -> bool:
    """Return True if last backup was less than min_days ago."""
    if min_days <= 0:
        return False
    last = state.get("last_backup")
    if not last:
        return False
    last_dt = datetime.fromisoformat(last)
    now = datetime.now(timezone.utc)
    return (now - last_dt).days < min_days


def create_archive(workspace: Path) -> Path:
    """Create tar.gz of workspace."""
    ts = datetime.now(ICT).strftime("%Y%m%d_%H%M%S")
    archive = Path(f"/tmp/openclaw_backup_{ts}.tar.gz")
    subprocess.run(
        ["tar", "czf", str(archive),
         "--exclude", ".git",
         "--exclude", "__pycache__",
         "--exclude", "*.pyc",
         "-C", str(workspace.parent),
         workspace.name],
        check=True
    )
    return archive


def encrypt_file(archive: Path, passphrase: str) -> Path:
    """Encrypt archive with AES-256-CBC via openssl."""
    encrypted = archive.with_suffix(".tar.gz.enc")
    subprocess.run(
        ["openssl", "enc", "-aes-256-cbc", "-pbkdf2",
         "-salt", "-in", str(archive), "-out", str(encrypted),
         "-pass", f"pass:{passphrase}"],
        check=True
    )
    archive.unlink()  # remove unencrypted archive
    return encrypted


def main():
    parser = argparse.ArgumentParser(description="OpenClaw encrypted backup")
    parser.add_argument("--drive-folder-id", help="Google Drive folder ID")
    parser.add_argument("--min-days", type=int, default=6,
                        help="Skip if last backup < N days ago (default: 6, use 0 to force)")
    args = parser.parse_args()

    state = load_state()

    # Resolve drive folder ID
    folder_id = args.drive_folder_id
    if not folder_id:
        cfg = load_config()
        folder_id = cfg.get("drive_folder_id")
    if not folder_id:
        print("❌ No drive-folder-id provided and no config found.")
        print(f"   Create {CONFIG_PATH} with {{\"drive_folder_id\": \"...\"}}")
        return

    # Check min-days
    if should_skip(args.min_days, state):
        print(f"⏭ Last backup was < {args.min_days} days ago — skipping.")
        print(f"   Use --min-days 0 to force.")
        return

    passphrase = load_passphrase()

    print(f"📦 Creating archive of {WORKSPACE}...")
    archive = create_archive(WORKSPACE)

    print(f"🔐 Encrypting (AES-256-CBC, PBKDF2)...")
    encrypted = encrypt_file(archive, passphrase)
    size_mb = encrypted.stat().st_size / (1024 * 1024)
    print(f"   Encrypted: {encrypted.name} ({size_mb:.1f} MB)")

    # TODO: Upload to Google Drive using drive API
    # For now, just report the file location
    print(f"📤 Upload target: Drive folder {folder_id}")
    print(f"   File: {encrypted}")
    print(f"   ⚠️ Upload implementation pending — use drive_media_tools.py or manual upload")

    # Update state
    state["last_backup"] = datetime.now(timezone.utc).isoformat()
    state["count"] = state.get("count", 0) + 1
    save_state(state)

    print(f"✅ Backup #{state['count']} complete.")


if __name__ == "__main__":
    main()

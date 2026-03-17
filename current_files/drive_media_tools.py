#!/usr/bin/env python3
"""
Drive Media Tools
- Store media on Google Drive (instead of local VPS workspace)
- Auto-index metadata into SQLite for fast search/list

Usage:
  python3 tools/drive_media_tools.py init --root-folder-id <FOLDER_ID>
  python3 tools/drive_media_tools.py upload --file /path/a.jpg --type image --tags "bé Dung,sinh nhật"
  python3 tools/drive_media_tools.py search --q "bé dung" --type image
  python3 tools/drive_media_tools.py list --type video --limit 20
  python3 tools/drive_media_tools.py reindex
"""

import argparse
import json
import mimetypes
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List

import requests

WORKSPACE = Path(os.environ.get("OPENCLAW_WORKSPACE", "/root/.openclaw/workspace"))
CRED_PATH = WORKSPACE / "credentials/google_workspace_credentials.json"
TOKEN_PATH = WORKSPACE / "credentials/google_workspace_token.json"
CFG_PATH = WORKSPACE / "credentials/drive_media_config.json"
DB_PATH = WORKSPACE / "memory/media.db"

DRIVE_API = "https://www.googleapis.com/drive/v3"
UPLOAD_API = "https://www.googleapis.com/upload/drive/v3/files"
FOLDER_MIME = "application/vnd.google-apps.folder"

MEDIA_TYPES = ["image", "video", "audio", "text"]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def refresh_access_token() -> str:
    if not CRED_PATH.exists() or not TOKEN_PATH.exists():
        raise FileNotFoundError("Missing google workspace credentials/token")

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

    return token["access_token"]


def auth_headers() -> Dict[str, str]:
    return {"Authorization": f"Bearer {refresh_access_token()}"}


def db_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS media_files (
          id TEXT PRIMARY KEY,
          name TEXT NOT NULL,
          type TEXT NOT NULL,
          folder_id TEXT,
          folder_name TEXT,
          mime_type TEXT,
          size INTEGER,
          md5_checksum TEXT,
          tags TEXT,
          web_view_link TEXT,
          web_content_link TEXT,
          modified_time TEXT,
          uploaded_at TEXT,
          indexed_at TEXT,
          source_path TEXT
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_media_name ON media_files(name)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_media_type ON media_files(type)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_media_uploaded_at ON media_files(uploaded_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_media_folder ON media_files(folder_name)")
    conn.commit()
    return conn


def drive_get(url: str, params: Dict[str, Any]) -> Dict[str, Any]:
    r = requests.get(url, headers=auth_headers(), params=params, timeout=60)
    r.raise_for_status()
    return r.json()


def drive_post(url: str, *, data=None, files=None, params=None) -> Dict[str, Any]:
    r = requests.post(url, headers=auth_headers(), data=data, files=files, params=params, timeout=120)
    r.raise_for_status()
    return r.json()


def find_folder(parent_id: str, name: str) -> str | None:
    q = f"'{parent_id}' in parents and trashed=false and mimeType='{FOLDER_MIME}' and name='{name}'"
    data = drive_get(f"{DRIVE_API}/files", {
        "q": q,
        "fields": "files(id,name)",
        "pageSize": 10,
        "supportsAllDrives": "true",
        "includeItemsFromAllDrives": "true",
    })
    files = data.get("files", [])
    return files[0]["id"] if files else None


def create_folder(parent_id: str, name: str) -> str:
    meta = {"name": name, "mimeType": FOLDER_MIME, "parents": [parent_id]}
    res = drive_post(
        f"{DRIVE_API}/files",
        data=json.dumps(meta),
        params={"fields": "id,name", "supportsAllDrives": "true"},
    )
    return res["id"]


def ensure_subfolders(root_id: str) -> Dict[str, str]:
    folders = {}
    for mtype in MEDIA_TYPES:
        fid = find_folder(root_id, mtype)
        if not fid:
            fid = create_folder(root_id, mtype)
        folders[mtype] = fid
    return folders


def save_config(root_id: str, folders: Dict[str, str]) -> None:
    save_json(CFG_PATH, {
        "root_folder_id": root_id,
        "folders": folders,
        "updated_at": now_iso(),
    })


def load_config() -> Dict[str, Any]:
    if not CFG_PATH.exists():
        raise FileNotFoundError("Missing drive_media_config.json. Run init first.")
    cfg = load_json(CFG_PATH)
    if "folders" not in cfg:
        raise ValueError("Invalid drive_media_config.json")
    return cfg


def infer_mime(path: Path) -> str:
    mime, _ = mimetypes.guess_type(str(path))
    return mime or "application/octet-stream"


def upsert_db(conn: sqlite3.Connection, rec: Dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO media_files (
          id,name,type,folder_id,folder_name,mime_type,size,md5_checksum,tags,
          web_view_link,web_content_link,modified_time,uploaded_at,indexed_at,source_path
        ) VALUES (
          :id,:name,:type,:folder_id,:folder_name,:mime_type,:size,:md5_checksum,:tags,
          :web_view_link,:web_content_link,:modified_time,:uploaded_at,:indexed_at,:source_path
        )
        ON CONFLICT(id) DO UPDATE SET
          name=excluded.name,
          type=excluded.type,
          folder_id=excluded.folder_id,
          folder_name=excluded.folder_name,
          mime_type=excluded.mime_type,
          size=excluded.size,
          md5_checksum=excluded.md5_checksum,
          tags=excluded.tags,
          web_view_link=excluded.web_view_link,
          web_content_link=excluded.web_content_link,
          modified_time=excluded.modified_time,
          uploaded_at=excluded.uploaded_at,
          indexed_at=excluded.indexed_at,
          source_path=excluded.source_path
        """,
        rec,
    )
    conn.commit()


def upload_file(path: Path, mtype: str, tags: str = "") -> Dict[str, Any]:
    if mtype not in MEDIA_TYPES:
        raise ValueError(f"type must be one of: {', '.join(MEDIA_TYPES)}")
    if not path.exists():
        raise FileNotFoundError(path)

    cfg = load_config()
    folder_id = cfg["folders"][mtype]
    mime = infer_mime(path)

    metadata = {
        "name": path.name,
        "parents": [folder_id],
    }

    with open(path, "rb") as f:
        files = {
            "metadata": ("metadata", json.dumps(metadata), "application/json; charset=UTF-8"),
            "file": (path.name, f, mime),
        }
        res = drive_post(
            UPLOAD_API,
            files=files,
            params={
                "uploadType": "multipart",
                "fields": "id,name,mimeType,size,md5Checksum,webViewLink,webContentLink,modifiedTime,createdTime,parents",
                "supportsAllDrives": "true",
            },
        )

    conn = db_conn()
    rec = {
        "id": res.get("id"),
        "name": res.get("name", path.name),
        "type": mtype,
        "folder_id": folder_id,
        "folder_name": mtype,
        "mime_type": res.get("mimeType", mime),
        "size": int(res.get("size", 0) or 0),
        "md5_checksum": res.get("md5Checksum"),
        "tags": tags,
        "web_view_link": res.get("webViewLink", f"https://drive.google.com/file/d/{res.get('id')}/view"),
        "web_content_link": res.get("webContentLink"),
        "modified_time": res.get("modifiedTime"),
        "uploaded_at": res.get("createdTime") or now_iso(),
        "indexed_at": now_iso(),
        "source_path": str(path),
    }
    upsert_db(conn, rec)
    return rec


def list_drive_files(folder_id: str, page_size: int = 200) -> List[Dict[str, Any]]:
    all_files = []
    page_token = None
    while True:
        params = {
            "q": f"'{folder_id}' in parents and trashed=false",
            "fields": "nextPageToken,files(id,name,mimeType,size,md5Checksum,webViewLink,webContentLink,modifiedTime,createdTime,parents)",
            "pageSize": page_size,
            "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true",
        }
        if page_token:
            params["pageToken"] = page_token
        data = drive_get(f"{DRIVE_API}/files", params)
        all_files.extend(data.get("files", []))
        page_token = data.get("nextPageToken")
        if not page_token:
            break
    return all_files


def reindex_all() -> int:
    cfg = load_config()
    conn = db_conn()
    count = 0
    for mtype, fid in cfg["folders"].items():
        files = list_drive_files(fid)
        for f in files:
            rec = {
                "id": f.get("id"),
                "name": f.get("name"),
                "type": mtype,
                "folder_id": fid,
                "folder_name": mtype,
                "mime_type": f.get("mimeType"),
                "size": int(f.get("size", 0) or 0),
                "md5_checksum": f.get("md5Checksum"),
                "tags": "",
                "web_view_link": f.get("webViewLink", f"https://drive.google.com/file/d/{f.get('id')}/view"),
                "web_content_link": f.get("webContentLink"),
                "modified_time": f.get("modifiedTime"),
                "uploaded_at": f.get("createdTime") or now_iso(),
                "indexed_at": now_iso(),
                "source_path": "",
            }
            upsert_db(conn, rec)
            count += 1
    return count


def search_db(q: str, mtype: str | None, limit: int) -> List[sqlite3.Row]:
    conn = db_conn()
    conn.row_factory = sqlite3.Row
    sql = """
      SELECT id,name,type,tags,uploaded_at,web_view_link
      FROM media_files
      WHERE (lower(name) LIKE ? OR lower(tags) LIKE ?)
    """
    params = [f"%{q.lower()}%", f"%{q.lower()}%"]
    if mtype:
        sql += " AND type = ?"
        params.append(mtype)
    sql += " ORDER BY COALESCE(uploaded_at, indexed_at) DESC LIMIT ?"
    params.append(limit)
    return conn.execute(sql, params).fetchall()


def list_db(mtype: str | None, limit: int) -> List[sqlite3.Row]:
    conn = db_conn()
    conn.row_factory = sqlite3.Row
    if mtype:
        return conn.execute(
            "SELECT id,name,type,tags,uploaded_at,web_view_link FROM media_files WHERE type=? ORDER BY COALESCE(uploaded_at,indexed_at) DESC LIMIT ?",
            (mtype, limit),
        ).fetchall()
    return conn.execute(
        "SELECT id,name,type,tags,uploaded_at,web_view_link FROM media_files ORDER BY COALESCE(uploaded_at,indexed_at) DESC LIMIT ?",
        (limit,),
    ).fetchall()


def print_rows(rows: List[sqlite3.Row]) -> None:
    if not rows:
        print("(no results)")
        return
    for i, r in enumerate(rows, 1):
        tags = (r["tags"] or "").strip()
        tags_txt = f" | tags: {tags}" if tags else ""
        print(f"{i}. [{r['type']}] {r['name']}{tags_txt}")
        print(f"   {r['web_view_link']}")


def cmd_init(args):
    headers = auth_headers()  # validate auth
    _ = headers
    folders = ensure_subfolders(args.root_folder_id)
    save_config(args.root_folder_id, folders)
    _ = db_conn()
    print("✅ Drive media initialized")
    print(f"root: {args.root_folder_id}")
    for k, v in folders.items():
        print(f"- {k}: {v}")


def cmd_upload(args):
    rec = upload_file(Path(args.file), args.type, tags=args.tags or "")
    print("✅ Uploaded + indexed")
    print(rec["name"])
    print(rec["web_view_link"])


def cmd_search(args):
    rows = search_db(args.q, args.type, args.limit)
    print_rows(rows)


def cmd_list(args):
    rows = list_db(args.type, args.limit)
    print_rows(rows)


def cmd_reindex(args):
    n = reindex_all()
    print(f"✅ Reindexed {n} files")


def main():
    p = argparse.ArgumentParser(description="Drive media manager")
    sp = p.add_subparsers(dest="cmd", required=True)

    p_init = sp.add_parser("init")
    p_init.add_argument("--root-folder-id", required=True)
    p_init.set_defaults(func=cmd_init)

    p_upload = sp.add_parser("upload")
    p_upload.add_argument("--file", required=True)
    p_upload.add_argument("--type", required=True, choices=MEDIA_TYPES)
    p_upload.add_argument("--tags", default="")
    p_upload.set_defaults(func=cmd_upload)

    p_search = sp.add_parser("search")
    p_search.add_argument("--q", required=True)
    p_search.add_argument("--type", choices=MEDIA_TYPES)
    p_search.add_argument("--limit", type=int, default=10)
    p_search.set_defaults(func=cmd_search)

    p_list = sp.add_parser("list")
    p_list.add_argument("--type", choices=MEDIA_TYPES)
    p_list.add_argument("--limit", type=int, default=20)
    p_list.set_defaults(func=cmd_list)

    p_reindex = sp.add_parser("reindex")
    p_reindex.set_defaults(func=cmd_reindex)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
import argparse
import json
import os
from pathlib import Path
from typing import List

from google_auth_oauthlib.flow import InstalledAppFlow
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import AuthorizedSession

SCOPES = [
    "https://www.googleapis.com/auth/analytics.readonly",
    "https://www.googleapis.com/auth/webmasters.readonly",
]

WORKSPACE = Path(os.environ.get("OPENCLAW_WORKSPACE", "/root/.openclaw/workspace"))


def save_token(creds: Credentials, token_path: str):
    os.makedirs(os.path.dirname(token_path), exist_ok=True)
    with open(token_path, "w", encoding="utf-8") as f:
        f.write(creds.to_json())


def load_token(token_path: str):
    if not os.path.exists(token_path):
        return None
    with open(token_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return Credentials.from_authorized_user_info(data, SCOPES)


def do_oauth(client_secret_path: str, token_path: str, port: int):
    creds = load_token(token_path)
    if creds and creds.valid:
        return creds

    flow = InstalledAppFlow.from_client_secrets_file(client_secret_path, SCOPES)
    creds = flow.run_local_server(
        host="127.0.0.1",
        port=port,
        open_browser=False,
        authorization_prompt_message=(
            "\nMở URL này trên máy local để cấp quyền:\n{url}\n"
            "Nếu chạy qua SSH tunnel, giữ tunnel mở đến khi hoàn tất.\n"
        ),
        success_message="Xác thực thành công. Có thể quay lại terminal.",
    )
    save_token(creds, token_path)
    return creds


def check_gsc(session: AuthorizedSession, expected_sites: List[str]):
    r = session.get("https://searchconsole.googleapis.com/webmasters/v3/sites")
    r.raise_for_status()
    payload = r.json()
    site_entries = payload.get("siteEntry", [])
    sites = {x.get("siteUrl"): x.get("permissionLevel") for x in site_entries}

    result = {"all_sites": sites, "checks": {}}
    for site in expected_sites:
        result["checks"][site] = {
            "exists": site in sites,
            "permission": sites.get(site),
        }
    return result


def check_ga(session: AuthorizedSession, property_ids: List[str]):
    results = {}
    for pid in property_ids:
        url = f"https://analyticsdata.googleapis.com/v1beta/properties/{pid}:runReport"
        body = {
            "dateRanges": [{"startDate": "7daysAgo", "endDate": "today"}],
            "metrics": [{"name": "activeUsers"}],
            "limit": "1",
        }
        r = session.post(url, json=body)
        ok = r.status_code == 200
        results[pid] = {
            "ok": ok,
            "status": r.status_code,
            "message": (r.json().get("error", {}).get("message") if not ok else "connected"),
        }
    return results


def main():
    ap = argparse.ArgumentParser(description="Connect and validate GA4 + GSC read-only access")
    ap.add_argument("--client-secret", required=True, help="OAuth client JSON")
    ap.add_argument("--token", default=str(WORKSPACE / "credentials/ga_gsc_token.json"))
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--ga", nargs="*", default=[])
    ap.add_argument("--gsc", nargs="*", default=[])
    ap.add_argument("--out", default=str(WORKSPACE / "memory/ga_gsc_connection_status.json"))
    args = ap.parse_args()

    creds = do_oauth(args.client_secret, args.token, args.port)
    session = AuthorizedSession(creds)

    report = {
        "scopes": SCOPES,
        "ga": check_ga(session, args.ga),
        "gsc": check_gsc(session, args.gsc),
    }

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\nSaved: {args.out}")


if __name__ == "__main__":
    main()

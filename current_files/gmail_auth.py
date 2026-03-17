#!/usr/bin/env python3
"""Gmail OAuth2 setup - add gmail.send scope to existing workspace token."""

import json
import sys
import urllib.parse

CREDS_FILE = "/root/.openclaw/workspace/credentials/google_workspace_credentials.json"
TOKEN_FILE = "/root/.openclaw/workspace/credentials/google_workspace_token.json"

# All scopes: existing + gmail.send
SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/gmail.send",
]

def generate_auth_url():
    with open(CREDS_FILE) as f:
        creds = json.load(f)["installed"]
    
    params = {
        "client_id": creds["client_id"],
        "redirect_uri": "urn:ietf:wg:oauth:2.0:oob",
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
    }
    url = f"https://accounts.google.com/o/oauth2/auth?{urllib.parse.urlencode(params)}"
    print("\n🔗 Anh mở link này trong browser, đăng nhập bằng minhtqm1993@gmail.com:")
    print(f"\n{url}\n")
    print("Sau khi Allow, Google sẽ hiện 1 mã code. Copy mã đó gửi lại cho em nha!")

def exchange_code(code):
    import urllib.request
    
    with open(CREDS_FILE) as f:
        creds = json.load(f)["installed"]
    
    data = urllib.parse.urlencode({
        "code": code,
        "client_id": creds["client_id"],
        "client_secret": creds["client_secret"],
        "redirect_uri": "urn:ietf:wg:oauth:2.0:oob",
        "grant_type": "authorization_code",
    }).encode()
    
    req = urllib.request.Request("https://oauth2.googleapis.com/token", data=data)
    try:
        with urllib.request.urlopen(req) as resp:
            token_data = json.loads(resp.read())
        
        # Save token
        import time
        token_data["updated_at"] = int(time.time())
        with open(TOKEN_FILE, "w") as f:
            json.dump(token_data, f, indent=2)
        
        print("✅ Token saved! Scopes:", token_data.get("scope", ""))
        return True
    except Exception as e:
        print(f"❌ Error: {e}")
        return False

if __name__ == "__main__":
    if len(sys.argv) > 1:
        exchange_code(sys.argv[1])
    else:
        generate_auth_url()

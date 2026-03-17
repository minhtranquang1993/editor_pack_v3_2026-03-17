#!/usr/bin/env python3
"""Pancake CRM Webhook Receiver - nhận lead mới từ Pancake, lưu log + báo Telegram."""

import json, os, sys, time, urllib.request
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime
from pathlib import Path

TELEGRAM_BOT_TOKEN = "7193001951:AAEx9X1BeBrlU4VvzDhtpuoLPbJYRismEGc"
TELEGRAM_CHAT_ID = "1661694132"
WORKSPACE = Path(os.environ.get("OPENCLAW_WORKSPACE", "/root/.openclaw/workspace"))
LOG_DIR = str(WORKSPACE / "memory/pancake_leads")
PORT = 8765

os.makedirs(LOG_DIR, exist_ok=True)

def send_telegram(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = json.dumps({"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"}).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(f"[TG ERROR] {e}", flush=True)

class WebhookHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""
        
        try:
            payload = json.loads(body.decode("utf-8")) if body else {}
        except:
            payload = {"raw": body.decode("utf-8", errors="ignore")}
        
        # Log mọi request
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        log_file = os.path.join(LOG_DIR, f"{ts}.json")
        with open(log_file, "w", encoding="utf-8") as f:
            json.dump({"timestamp": ts, "headers": dict(self.headers), "payload": payload}, f, ensure_ascii=False, indent=2)
        
        print(f"[{ts}] Received webhook: {json.dumps(payload, ensure_ascii=False)[:500]}", flush=True)
        
        # Gửi Telegram thông báo
        now_vn = datetime.now().strftime("%d/%m/%Y %H:%M")
        msg = f"🔔 *PANCAKE WEBHOOK*\n"
        msg += f"⏰ {now_vn}\n\n"
        msg += f"```\n{json.dumps(payload, ensure_ascii=False, indent=2)[:800]}\n```"
        send_telegram(msg)
        
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"status": "ok"}).encode())
    
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Pancake Webhook Receiver is running!")
    
    def log_message(self, format, *args):
        pass  # suppress default logs

if __name__ == "__main__":
    print(f"🚀 Pancake Webhook Server starting on port {PORT}...", flush=True)
    server = HTTPServer(("0.0.0.0", PORT), WebhookHandler)
    print(f"✅ Listening on http://0.0.0.0:{PORT}", flush=True)
    server.serve_forever()

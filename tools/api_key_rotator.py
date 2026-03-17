#!/usr/bin/env python3
"""
Generic API Key Rotator — auto-rotate khi bị rate limit.
Usage: 
  from api_key_rotator import KeyRotator
  rotator = KeyRotator("service_name", ["key1", "key2", "key3"])
  key = rotator.get_key()
  rotator.mark_failed(key)  # khi bị rate limit
"""

import json
import time
from pathlib import Path
from typing import Optional

import os

_WORKSPACE = Path(os.environ.get("OPENCLAW_WORKSPACE", "/root/.openclaw/workspace"))
STATE_DIR = _WORKSPACE / "memory"

class KeyRotator:
    def __init__(self, service: str, keys: list[str], cooldown_seconds: int = 60):
        self.service = service
        self.keys = keys
        self.cooldown = cooldown_seconds
        self.state_file = STATE_DIR / f"key_rotator_{service}.json"
        self.state = self._load_state()
    
    def _load_state(self) -> dict:
        if self.state_file.exists():
            try:
                return json.loads(self.state_file.read_text())
            except:
                pass
        return {
            "current_index": 0,
            "failed_keys": {},  # key_hash -> failed_until_ts
            "stats": {}  # key_hash -> {"calls": 0, "fails": 0}
        }
    
    def _save_state(self):
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps(self.state, indent=2))
    
    def _hash(self, key: str) -> str:
        return key[-8:]  # last 8 chars as identifier
    
    def get_key(self) -> Optional[str]:
        """Get next available key, skipping failed ones."""
        now = time.time()
        
        # Clean expired failures
        self.state["failed_keys"] = {
            k: v for k, v in self.state["failed_keys"].items()
            if v > now
        }
        
        # Try keys starting from current_index
        for i in range(len(self.keys)):
            idx = (self.state["current_index"] + i) % len(self.keys)
            key = self.keys[idx]
            h = self._hash(key)
            
            if h not in self.state["failed_keys"]:
                self.state["current_index"] = idx
                
                # Track stats
                if h not in self.state["stats"]:
                    self.state["stats"][h] = {"calls": 0, "fails": 0}
                self.state["stats"][h]["calls"] += 1
                
                self._save_state()
                return key
        
        # All keys failed — return least recently failed
        return self.keys[self.state["current_index"]]
    
    def mark_failed(self, key: str):
        """Mark key as rate-limited, rotate to next."""
        h = self._hash(key)
        self.state["failed_keys"][h] = time.time() + self.cooldown
        
        if h in self.state["stats"]:
            self.state["stats"][h]["fails"] += 1
        
        # Rotate
        self.state["current_index"] = (self.state["current_index"] + 1) % len(self.keys)
        self._save_state()
    
    def mark_success(self, key: str):
        """Clear failure status for key."""
        h = self._hash(key)
        self.state["failed_keys"].pop(h, None)
        self._save_state()
    
    def status(self) -> dict:
        """Current rotator status."""
        now = time.time()
        available = sum(1 for k in self.keys if self._hash(k) not in self.state["failed_keys"] or self.state["failed_keys"][self._hash(k)] <= now)
        return {
            "service": self.service,
            "total_keys": len(self.keys),
            "available": available,
            "failed": len(self.keys) - available,
            "current_index": self.state["current_index"],
            "stats": self.state["stats"]
        }


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--service", required=True)
    parser.add_argument("--keys", required=True, help="Comma-separated keys")
    parser.add_argument("--action", choices=["get", "fail", "status"], default="get")
    parser.add_argument("--key", help="Key to mark failed")
    args = parser.parse_args()
    
    keys = [k.strip() for k in args.keys.split(",")]
    rotator = KeyRotator(args.service, keys)
    
    if args.action == "get":
        print(rotator.get_key())
    elif args.action == "fail":
        rotator.mark_failed(args.key or keys[0])
        print(f"Marked failed, rotated to next")
    elif args.action == "status":
        print(json.dumps(rotator.status(), indent=2))

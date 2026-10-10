#!/usr/bin/env python3
"""
session_sync.py - PreInvocation hook handler for agy-client.

Runs when an invocation starts in Antigravity (i.e. when the user submits a prompt in agy).
1. Synchronizes active session workspace metadata for `agy_client --list`.
2. Drains any piped context queued in the session inbox and injects it as `injectSteps`.
"""

import sys
import os
import json
import time
from pathlib import Path

def get_base_dir() -> Path:
    app_data = os.environ.get("ANTIGRAVITY_APP_DATA_DIR")
    if app_data:
        return Path(app_data)
    return Path.home() / ".gemini" / "antigravity-cli"

def get_inbox_dir(cid: str) -> Path:
    inbox = get_base_dir() / "agy_client" / "inbox" / cid
    inbox.mkdir(parents=True, mode=0o700, exist_ok=True)
    return inbox

def get_sessions_dir() -> Path:
    sess_dir = get_base_dir() / "agy_client" / "sessions"
    sess_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
    return sess_dir

def main():
    hook_payload = {}
    try:
        if not sys.stdin.isatty():
            raw = sys.stdin.read()
            if raw.strip():
                hook_payload = json.loads(raw)
    except Exception:
        hook_payload = {}

    cid = hook_payload.get("conversationId") or os.environ.get("ANTIGRAVITY_CONVERSATION_ID", "")
    workspaces = hook_payload.get("workspacePaths", [])
    if not workspaces and "PWD" in os.environ:
        workspaces = [os.environ["PWD"]]

    if cid:
        # 1. Update session workspace metadata for agy_client --list
        sess_dir = get_sessions_dir()
        sess_file = sess_dir / f"{cid}.json"
        sess_data = {
            "conversation_id": cid,
            "workspaces": workspaces,
            "last_updated": time.time(),
        }
        try:
            tmp = sess_dir / f"{cid}.tmp"
            with open(tmp, "w", encoding="utf-8") as fp:
                json.dump(sess_data, fp, indent=2)
            tmp.replace(sess_file)
        except Exception:
            pass

        # Only drain inbox during PreInvocation (not during tool hooks like PostToolUse)
        if "toolCall" not in hook_payload and "stepIdx" not in hook_payload:
            inbox = get_inbox_dir(cid)
            inject_steps = []
            if inbox.exists():
                msg_files = sorted(inbox.glob("*.json"), key=lambda p: p.name)
                for f in msg_files:
                    try:
                        with open(f, "r", encoding="utf-8") as fp:
                            data = json.load(fp)
                        payload = data.get("payload", "").strip()
                        if payload:
                            inject_steps.append({"userMessage": payload})
                        f.unlink(missing_ok=True)
                    except Exception:
                        pass

            if inject_steps:
                print(json.dumps({"injectSteps": inject_steps}))
                return

    print("{}")

if __name__ == "__main__":
    main()

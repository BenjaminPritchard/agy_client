#!/usr/bin/env python3
"""
Unit tests for agy_client and session synchronization.
"""

import unittest
import tempfile
import os
import json
import time
from pathlib import Path

# Add scripts directory to sys.path
REPO_DIR = Path(__file__).resolve().parent.parent
import sys
sys.path.insert(0, str(REPO_DIR / "scripts"))

from agy_client_core import (
    format_payload,
    is_pid_alive,
    resolve_target_session,
    format_screenshot_payload,
    get_png_dimensions,
    MAX_PAYLOAD_BYTES,
)

class TestPayloadFormatting(unittest.TestCase):
    def test_format_payload_with_piped_data(self):
        output = format_payload("file1.txt\nfile2.txt", "", "/tmp/test_dir", raw=False)
        self.assertIn("[Context piped via agy_client]", output)
        self.assertIn("• Working Directory: `/tmp/test_dir`", output)
        self.assertIn("```text\nfile1.txt\nfile2.txt\n```", output)

    def test_format_payload_with_user_instruction(self):
        output = format_payload("output text", "Review this output", "/tmp/test_dir", raw=False)
        self.assertIn("[Context piped via agy_client]", output)
        self.assertIn("• Instruction / User Note: Review this output", output)
        self.assertIn("output text", output)

    def test_format_payload_raw_mode(self):
        output = format_payload("raw output", "note", "/tmp", raw=True)
        self.assertEqual(output, "note\n\nraw output")

    def test_format_payload_instruction_only(self):
        output = format_payload("", "Just a note", "/tmp", raw=False)
        self.assertIn("• Instruction / User Note: Just a note", output)
        self.assertNotIn("```text", output)

class TestProcessLiveness(unittest.TestCase):
    def test_current_process_alive(self):
        self.assertTrue(is_pid_alive(os.getpid()))

    def test_invalid_pid_dead(self):
        self.assertFalse(is_pid_alive(-1))
        self.assertFalse(is_pid_alive(0))
        # High unlikely PID
        self.assertFalse(is_pid_alive(9999999))

class TestSessionRegistrySync(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.app_data = Path(self.temp_dir.name)
        os.environ["ANTIGRAVITY_APP_DATA_DIR"] = str(self.app_data)
        self.registry_dir = self.app_data / "agy_client" / "sessions"
        self.registry_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.temp_dir.cleanup()
        os.environ.pop("ANTIGRAVITY_APP_DATA_DIR", None)

    def test_queue_message(self):
        from agy_client_core import queue_message, get_inbox_dir
        sess = {"conversation_id": "test-conv-1234"}
        ok = queue_message(sess, title="Test", payload="Hello world")
        self.assertTrue(ok)

        inbox = get_inbox_dir("test-conv-1234")
        files = list(inbox.glob("*.json"))
        self.assertEqual(len(files), 1)
        with open(files[0]) as f:
            data = json.load(f)
        self.assertEqual(data["payload"], "Hello world")
        self.assertEqual(data["title"], "Test")

    def test_session_sync_drain(self):
        from agy_client_core import queue_message, get_inbox_dir
        import session_sync
        sess = {"conversation_id": "test-conv-5678"}
        queue_message(sess, title="T1", payload="Message 1")
        queue_message(sess, title="T2", payload="Message 2")

        # Mock stdin and stdout
        import io
        sys.stdin = io.StringIO(json.dumps({"conversationId": "test-conv-5678", "workspacePaths": ["/test"]}))
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            session_sync.main()
            output = sys.stdout.getvalue()
        finally:
            sys.stdout = old_stdout

        res = json.loads(output)
        self.assertIn("injectSteps", res)
        self.assertEqual(len(res["injectSteps"]), 2)
        self.assertEqual(res["injectSteps"][0]["userMessage"], "Message 1")
        self.assertEqual(res["injectSteps"][1]["userMessage"], "Message 2")

        # Verify inbox was emptied
        inbox = get_inbox_dir("test-conv-5678")
        self.assertEqual(len(list(inbox.glob("*.json"))), 0)

class TestScreenshot(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.fake_png = Path(self.temp_dir.name) / "test.png"
        # Write minimal PNG header with 800x600 resolution
        # \x89PNG\r\n\x1a\n (8) + \x00\x00\x00\rIHDR (8) + width(4) + height(4)
        png_header = (
            b"\x89PNG\r\n\x1a\n"
            b"\x00\x00\x00\rIHDR"
            b"\x00\x00\x03\x20"  # width: 800
            b"\x00\x00\x01\xf4"  # height: 500
            b"\x08\x06\x00\x00\x00"
        )
        with open(self.fake_png, "wb") as f:
            f.write(png_header)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_get_png_dimensions(self):
        dims = get_png_dimensions(self.fake_png)
        self.assertEqual(dims, (800, 500))

    def test_format_screenshot_payload(self):
        payload = format_screenshot_payload(
            self.fake_png,
            "Is the button centered?",
            "/workspace/myproject",
            dimensions=(800, 500)
        )
        self.assertIn("[Context piped via agy-screenshot]", payload)
        self.assertIn("• Resolution: `800x500`", payload)
        self.assertIn("• User Question / Note: Is the button centered?", payload)
        self.assertIn(f"![Screenshot](file://{self.fake_png.resolve()})", payload)
        self.assertIn("Please inspect this screenshot using `view_file`", payload)

    def test_format_screenshot_payload_with_source(self):
        payload = format_screenshot_payload(
            self.fake_png,
            "Check clipboard image",
            "/workspace/myproject",
            source_note="macOS Clipboard"
        )
        self.assertIn("• Source: macOS Clipboard", payload)

class TestPresenceAndIndexTargeting(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.presence_dir = Path(self.temp_dir.name) / "presence"
        self.presence_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_presence_lock_detection(self):
        import fcntl
        from agy_client_core import is_presence_locked
        lock_file = self.presence_dir / "test-session.lock"
        lock_file.touch()

        # When not locked
        self.assertFalse(is_presence_locked(lock_file))

        # When locked with flock
        with open(lock_file, "r+") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertTrue(is_presence_locked(lock_file))
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)

        # After releasing lock
        self.assertFalse(is_presence_locked(lock_file))

if __name__ == "__main__":
    unittest.main()

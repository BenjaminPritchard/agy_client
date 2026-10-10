"""
agy_client_core.py - Core logic for piping external context into Antigravity sessions.

Uses Antigravity's PreInvocation lifecycle hook and local inbox queue.
No reliance on language server network addresses or CSRF tokens.
"""

import sys
import os
import json
import argparse
import subprocess
import time
import shutil
import fcntl
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

VERSION = "1.2.0"
MAX_PAYLOAD_BYTES = 1024 * 1024  # 1 MB warning limit

def get_antigravity_base() -> Path:
    app_data = os.environ.get("ANTIGRAVITY_APP_DATA_DIR")
    if app_data:
        return Path(app_data)
    return Path.home() / ".gemini" / "antigravity-cli"

def get_registry_base() -> Path:
    base = get_antigravity_base()
    agy_client_dir = base / "agy_client"
    agy_client_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
    try:
        os.chmod(agy_client_dir, 0o700)
    except OSError:
        pass
    return agy_client_dir

def get_inbox_dir(cid: str) -> Path:
    inbox = get_registry_base() / "inbox" / cid
    inbox.mkdir(parents=True, mode=0o700, exist_ok=True)
    return inbox

def is_pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False

def is_presence_locked(lock_file: Path) -> bool:
    """Check if an Antigravity presence lock file is held by an active session."""
    try:
        with open(lock_file, "r+") as f:
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                return False
            except (BlockingIOError, IOError):
                return True
    except Exception:
        return False

def discover_workspaces_from_brain(cid: str) -> List[str]:
    """Fallback: detect workspaces from conversation brain files."""
    workspaces = []
    for app_name in ["antigravity-cli", "antigravity", "antigravity-ide"]:
        brain_dir = Path.home() / ".gemini" / app_name / "brain" / cid
        transcript = brain_dir / ".system_generated" / "logs" / "transcript.jsonl"
        if transcript.exists():
            try:
                with open(transcript, "r", encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        if '"Cwd":' in line:
                            import re
                            m = re.search(r'"Cwd":\s*"([^"]+)"', line)
                            if m:
                                ws = m.group(1).replace('\\"', '"')
                                if ws and ws not in workspaces:
                                    workspaces.append(ws)
                                    if len(workspaces) >= 3:
                                        return workspaces
            except Exception:
                pass
    return workspaces

def load_active_sessions() -> List[Dict[str, Any]]:
    """Discover active Antigravity sessions using native presence locks & registry cache."""
    base = get_antigravity_base()
    presence_dir = base / "presence"
    registry_sessions_dir = get_registry_base() / "sessions"
    registry_sessions_dir.mkdir(parents=True, exist_ok=True)

    active_sessions: List[Dict[str, Any]] = []
    seen_cids = set()

    if presence_dir.exists():
        lock_files = sorted(presence_dir.glob("*.lock"), key=lambda p: p.stat().st_mtime, reverse=True)
        for lock_file in lock_files:
            cid = lock_file.stem
            if cid in seen_cids:
                continue
            if not is_presence_locked(lock_file):
                continue

            seen_cids.add(cid)
            sess_file = registry_sessions_dir / f"{cid}.json"
            workspaces = []
            if sess_file.exists():
                try:
                    with open(sess_file, "r", encoding="utf-8") as fp:
                        data = json.load(fp)
                        workspaces = data.get("workspaces", [])
                except Exception:
                    pass

            if not workspaces:
                workspaces = discover_workspaces_from_brain(cid)

            active_sessions.append({
                "conversation_id": cid,
                "workspaces": workspaces,
                "last_updated": lock_file.stat().st_mtime,
            })

    return active_sessions

def resolve_target_session(target_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    sessions = load_active_sessions()
    if not sessions:
        return None

    if target_id:
        if target_id.isdigit():
            idx = int(target_id) - 1
            if 0 <= idx < len(sessions):
                return sessions[idx]

        target_id_lower = target_id.lower()
        for s in sessions:
            cid = s.get("conversation_id", "").lower()
            if cid == target_id_lower or cid.startswith(target_id_lower):
                return s
        return None

    # Priority 1: Match by current working directory / workspace
    cwd = Path.cwd().resolve()
    best_match = None
    best_depth = -1

    for s in sessions:
        for ws in s.get("workspaces", []):
            try:
                ws_path = Path(ws).resolve()
                if cwd == ws_path:
                    return s
                elif ws_path in cwd.parents:
                    depth = len(ws_path.parts)
                    if depth > best_depth:
                        best_depth = depth
                        best_match = s
            except Exception:
                pass

    if best_match:
        return best_match

    # Priority 2: Return most recently active session
    return sessions[0]

def test_connection(session: Dict[str, Any]) -> bool:
    cid = session.get("conversation_id", "")
    if cid:
        lock_file = get_antigravity_base() / "presence" / f"{cid}.lock"
        return lock_file.exists() and is_presence_locked(lock_file)
    return False

def queue_message(session: Dict[str, Any], title: str, payload: str) -> bool:
    """Queue message into session inbox for PreInvocation hook delivery."""
    cid = session.get("conversation_id", "")
    if not cid:
        return False

    inbox = get_inbox_dir(cid)
    msg_id = f"{time.time_ns()}"
    tmp_file = inbox / f"msg_{msg_id}.tmp"
    target_file = inbox / f"msg_{msg_id}.json"

    data = {
        "title": title,
        "payload": payload,
        "cwd": os.getcwd(),
        "timestamp": time.time(),
    }

    try:
        with open(tmp_file, "w", encoding="utf-8") as fp:
            json.dump(data, fp, indent=2)
        tmp_file.replace(target_file)
        return True
    except Exception as e:
        sys.stderr.write(f"Error queueing message: {e}\n")
        return False

def check_clipboard() -> str:
    """Detect whether clipboard contains 'IMAGE', 'TEXT', or is 'EMPTY'."""
    script = '''
    try
        set png_data to the clipboard as «class PNGf»
        return "IMAGE"
    on error
        try
            set txt_data to the clipboard as text
            if txt_data is not "" then
                return "TEXT"
            else
                return "EMPTY"
            end if
        on error
            return "EMPTY"
        end try
    end try
    '''
    try:
        res = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=2)
        return res.stdout.strip() or "EMPTY"
    except Exception:
        return "EMPTY"

def extract_clipboard_image(dest_path: Path) -> bool:
    """Save PNG image from clipboard to dest_path."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    if shutil.which("pngpaste"):
        try:
            res = subprocess.run(["pngpaste", str(dest_path)], capture_output=True, timeout=2)
            if res.returncode == 0 and dest_path.exists() and dest_path.stat().st_size > 0:
                return True
        except Exception:
            pass

    script = f'''
    try
        set png_data to the clipboard as «class PNGf»
        set fp to open for access POSIX file "{dest_path.resolve()}" with write permission
        set eof fp to 0
        write png_data to fp
        close access fp
        return "OK"
    on error
        return "FAIL"
    end try
    '''
    try:
        res = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=3)
        return res.returncode == 0 and res.stdout.strip() == "OK" and dest_path.exists() and dest_path.stat().st_size > 0
    except Exception:
        return False

def get_clipboard_text() -> str:
    """Extract plain text from clipboard."""
    try:
        res = subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=2)
        return res.stdout
    except Exception:
        return ""

def get_png_dimensions(path: Path) -> Optional[Tuple[int, int]]:
    """Extract width and height from PNG header without external libraries."""
    import struct
    try:
        with open(path, "rb") as f:
            header = f.read(24)
            if header.startswith(b"\x89PNG\r\n\x1a\n"):
                w, h = struct.unpack(">II", header[16:24])
                return (w, h)
    except Exception:
        pass
    return None

def format_payload(stdin_content: str, user_prompt: str, cwd: str, raw: bool = False) -> str:
    stdin_content = stdin_content.strip()
    user_prompt = user_prompt.strip()

    if raw:
        if user_prompt and stdin_content:
            return f"{user_prompt}\n\n{stdin_content}"
        return user_prompt or stdin_content

    parts = []
    parts.append("[Context piped via agy_client]")
    parts.append(f"• Working Directory: `{cwd}`")

    if user_prompt:
        parts.append(f"• Instruction / User Note: {user_prompt}")

    if stdin_content:
        parts.append("\n```text")
        parts.append(stdin_content)
        parts.append("```")
    elif not user_prompt:
        parts.append("*(No piped content or user note provided)*")

    return "\n".join(parts)

def format_screenshot_payload(image_path: Path, user_prompt: str, cwd: str, dimensions: Optional[Tuple[int, int]] = None, source_note: str = "") -> str:
    user_prompt = user_prompt.strip()
    size_kb = image_path.stat().st_size / 1024
    dim_str = f"{dimensions[0]}x{dimensions[1]}" if dimensions else "unknown"
    from datetime import datetime
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    parts = [
        "[Context piped via agy-screenshot]",
        f"• Captured: `{timestamp}`",
        f"• Resolution: `{dim_str}` ({size_kb:.1f} KB)",
        f"• Working Directory: `{cwd}`",
        f"• Image File: `{image_path.resolve()}`",
    ]
    if source_note:
        parts.append(f"• Source: {source_note}")
    if user_prompt:
        parts.append(f"• User Question / Note: {user_prompt}")
    
    parts.append(f"\n![Screenshot](file://{image_path.resolve()})")
    parts.append(f"\nPlease inspect this screenshot using `view_file` on `{image_path.resolve()}` to answer the user's inquiry.")
    return "\n".join(parts)

def capture_screenshot(dest_path: Path, window_mode: bool = False, delay: int = 0) -> bool:
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    if dest_path.exists():
        dest_path.unlink()

    cmd = ["screencapture"]
    if delay > 0:
        cmd.extend(["-T", str(delay)])
    
    if window_mode:
        cmd.extend(["-i", "-w", "-x"])
    else:
        cmd.extend(["-i", "-s", "-x"])
    
    cmd.append(str(dest_path))

    try:
        res = subprocess.run(cmd)
        return res.returncode == 0 and dest_path.exists() and dest_path.stat().st_size > 0
    except Exception as e:
        sys.stderr.write(f"Error running screencapture: {e}\n")
        return False

def install_symlink():
    target_bin = Path.home() / ".local" / "bin"
    target_bin.mkdir(parents=True, exist_ok=True)
    source_dir = (Path(__file__).resolve().parent.parent / "bin").resolve()

    commands = ["agy_client", "agy-screenshot", "agy_screenshot"]
    for cmd_name in commands:
        source_script = source_dir / cmd_name
        if not source_script.exists():
            if cmd_name == "agy_screenshot":
                source_script = source_dir / "agy-screenshot"
            elif cmd_name == "agy-screenshot":
                source_script = source_dir / "agy-screenshot"
        
        symlink_path = target_bin / cmd_name
        try:
            if symlink_path.is_symlink() or symlink_path.exists():
                symlink_path.unlink()
            if source_script.exists():
                os.chmod(source_script, 0o755)
                symlink_path.symlink_to(source_script)
                print(f"✔ Successfully linked {cmd_name} -> {symlink_path}")
        except Exception as e:
            sys.stderr.write(f"Failed to install symlink for {cmd_name}: {e}\n")
    print("  Ensure ~/.local/bin is in your PATH to run commands globally.")

def get_session_screenshot_path(cid: str) -> Path:
    from datetime import datetime
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    brain_dir = get_antigravity_base() / "brain" / cid / "screenshots"
    if brain_dir.parent.exists():
        dest_dir = brain_dir
    else:
        dest_dir = get_registry_base() / "screenshots"
    dest_dir.mkdir(parents=True, exist_ok=True)
    return dest_dir / f"screenshot_{ts}.png"

def run_screenshot_cli():
    parser = argparse.ArgumentParser(
        prog="agy-screenshot",
        description="Capture a screen rectangle or grab from clipboard and pipe directly into Antigravity.",
        add_help=False
    )
    parser.add_argument("prompt", nargs="*", help="Optional user message or question about the screenshot.")
    parser.add_argument("-s", "--session", type=str, help="Target specific conversation ID or session index (1, 2, ...).")
    parser.add_argument("-c", "--clipboard", action="store_true", help="Grab screenshot image currently in clipboard instead of interactive selection.")
    parser.add_argument("-t", "--title", type=str, default="Screen Capture", help="Message title shown in agent.")
    parser.add_argument("-d", "--delay", type=int, default=0, help="Delay in seconds before capturing.")
    parser.add_argument("-w", "--window", action="store_true", help="Window capture mode instead of rectangle.")
    parser.add_argument("-o", "--output", type=str, help="Custom output image file path.")
    parser.add_argument("-h", "--help", action="store_true", help="Show help and examples.")
    parser.add_argument("-v", "--version", action="store_true", help="Show version.")

    args = parser.parse_args()

    if args.help:
        print("""Usage: agy-screenshot [options] [question or instruction]

Captures an interactive screen rectangle or clipboard image and queues it for your Antigravity session.

Options:
    -s, --session <id|num> Target specific conversation ID or session index from --list
    -c, --clipboard        Grab screenshot image currently copied in clipboard
    -t, --title <title>    Custom message title (default: 'Screen Capture')
    -d, --delay <secs>     Delay before capture (seconds)
    -w, --window           Capture a window instead of rectangle selection
    -o, --output <path>    Save screenshot to custom path
    -h, --help             Show this help message
    -v, --version          Show version

Examples:
    agy-screenshot "Is this layout correct?"
    agy-screenshot -c "Inspect this screenshot in my clipboard"
    agy-screenshot --window "Look at this error dialog"
""")
        sys.exit(0)

    if args.version:
        print(f"agy-screenshot (agy_client version {VERSION})")
        sys.exit(0)

    session = resolve_target_session(args.session)
    if not session:
        sys.stderr.write("Error: No active Antigravity session found.\n")
        sys.stderr.write("Make sure Antigravity (`agy`) is running in a terminal.\n")
        sys.exit(1)

    cid = session.get("conversation_id", "default")
    dest_path = Path(args.output).resolve() if args.output else get_session_screenshot_path(cid)

    source_note = ""
    if args.clipboard:
        clip_type = check_clipboard()
        if clip_type != "IMAGE":
            sys.stderr.write("Error: No image found in clipboard.\n")
            sys.stderr.write("Copy a screenshot (Cmd+Ctrl+Shift+4) or run `agy-screenshot` without `-c` to capture interactively.\n")
            sys.exit(1)
        success = extract_clipboard_image(dest_path)
        if not success:
            sys.stderr.write("Error: Failed to extract image from clipboard.\n")
            sys.exit(1)
        source_note = "macOS Clipboard"
    else:
        if args.window:
            print("📸 Click a window to capture (press Esc to cancel)...")
        else:
            print("📸 Drag to select a rectangle on your screen (press Esc to cancel)...")

        success = capture_screenshot(dest_path, window_mode=args.window, delay=args.delay)
        if not success or not dest_path.exists():
            print("Capture cancelled or no image taken.")
            sys.exit(0)
        source_note = "Screen Selection"

    dims = get_png_dimensions(dest_path)
    user_note = " ".join(args.prompt).strip()
    payload = format_screenshot_payload(dest_path, user_note, os.getcwd(), dimensions=dims, source_note=source_note)

    short_id = cid[:8] if cid else "unknown"
    ws = session.get("workspaces", [""])[0]
    ws_name = Path(ws).name if ws else ""

    queued = queue_message(session, title=args.title, payload=payload)
    if queued:
        dim_str = f"{dims[0]}x{dims[1]}" if dims else "image"
        size_kb = dest_path.stat().st_size / 1024
        print(f"✔ Screenshot captured ({dim_str}, {size_kb:.1f} KB)")
        print(f"✔ Queued for Antigravity session [{short_id}] ({ws_name})")
        print("  (Screenshot will be included automatically with your next prompt in agy)")
        sys.exit(0)
    else:
        sys.exit(1)

def run_cli():
    if len(sys.argv) > 1 and sys.argv[1] == "screenshot":
        sys.argv.pop(1)
        run_screenshot_cli()
        return

    parser = argparse.ArgumentParser(
        prog="agy_client",
        description="Pipe external CLI context, clipboard, or files directly into a running Antigravity agent.",
        add_help=False
    )
    parser.add_argument("prompt", nargs="*", help="Optional user message or instruction to accompany the piped context.")
    parser.add_argument("-s", "--session", type=str, help="Target specific conversation ID or session index (1, 2, ...).")
    parser.add_argument("-c", "--clipboard", action="store_true", help="Pipe content directly from macOS clipboard (auto-detects image or text).")
    parser.add_argument("-t", "--title", type=str, default="Piped Context", help="Message title shown in agent.")
    parser.add_argument("-l", "--list", action="store_true", help="List active Antigravity sessions.")
    parser.add_argument("--status", action="store_true", help="Check active session status.")
    parser.add_argument("--raw", action="store_true", help="Send raw text without formatting.")
    parser.add_argument("--install", action="store_true", help="Install symlink to ~/.local/bin/agy_client.")
    parser.add_argument("-h", "--help", action="store_true", help="Show help and examples.")
    parser.add_argument("-v", "--version", action="store_true", help="Show version.")

    args = parser.parse_args()

    if args.help:
        print("""Usage:
    ls | agy_client
    git diff | agy_client "Please review these changes"
    pytest 2>&1 | agy_client "Why did this test fail?"
    cat error.log | agy_client
    agy_client "Run a quick health check"
    agy_client -c "Check this screenshot / text in my clipboard"

Options:
    --session, -s <id|num> Target specific conversation ID or session index from --list
    --clipboard, -c       Pipe image or text directly from clipboard
    --title, -t <title>   Custom message title (default: 'Piped Context')
    --list, -l            List active Antigravity sessions
    --status              Check connection status to active Antigravity session
    --raw                 Send raw content without Markdown block formatting
    --install             Symlink agy_client into ~/.local/bin
    --help, -h            Show this help message
    --version, -v         Show version information
""")
        sys.exit(0)

    if args.version:
        print(f"agy_client version {VERSION}")
        sys.exit(0)

    if args.install:
        install_symlink()
        sys.exit(0)

    if args.list:
        sessions = load_active_sessions()
        if not sessions:
            print("No active Antigravity sessions found.")
            sys.exit(0)
        cwd = Path.cwd().resolve()
        print(f"Active Antigravity Sessions ({len(sessions)}):")
        for idx, s in enumerate(sessions, 1):
            cid = s.get("conversation_id", "unknown")
            workspaces = ", ".join(s.get("workspaces", [])) or "None"
            alive = "active" if test_connection(s) else "inactive"
            
            matches_cwd = False
            for ws in s.get("workspaces", []):
                try:
                    ws_path = Path(ws).resolve()
                    if cwd == ws_path or ws_path in cwd.parents:
                        matches_cwd = True
                        break
                except Exception:
                    pass

            marker = " *" if matches_cwd or (idx == 1 and not any(cwd == Path(w).resolve() for s_other in sessions for w in s_other.get("workspaces", []))) else "  "
            hint = " (matches current dir)" if matches_cwd else ""
            print(f"{marker}[{idx}] {cid[:12]}... (Status: {alive}){hint}")
            print(f"      Workspaces: {workspaces}")
        print("\nTip: Use -s <num> (e.g. `agy_client -s 1`) to target a session by number.")
        sys.exit(0)

    session = resolve_target_session(args.session)
    if not session:
        sys.stderr.write("Error: No active Antigravity session found.\n")
        sys.stderr.write("Make sure Antigravity (`agy`) is running in a terminal.\n")
        sys.stderr.write("Run 'agy_client --list' to check active sessions.\n")
        sys.exit(1)

    if args.status:
        cid = session.get("conversation_id", "unknown")
        connected = test_connection(session)
        status_str = "ACTIVE" if connected else "INACTIVE"
        print(f"Session ID : {cid}")
        print(f"Workspaces : {', '.join(session.get('workspaces', []))}")
        print(f"Status     : {status_str}")
        sys.exit(0 if connected else 1)

    # Read stdin if piped
    stdin_data = ""
    if not sys.stdin.isatty():
        stdin_data = sys.stdin.read()

    user_note = " ".join(args.prompt).strip()

    is_clipboard_request = args.clipboard
    if not stdin_data and not user_note:
        is_clipboard_request = True

    if is_clipboard_request and not stdin_data:
        clip_type = check_clipboard()
        if clip_type == "IMAGE":
            cid = session.get("conversation_id", "default")
            dest_path = get_session_screenshot_path(cid)
            success = extract_clipboard_image(dest_path)
            if success:
                dims = get_png_dimensions(dest_path)
                dim_str = f"{dims[0]}x{dims[1]}" if dims else "image"
                size_kb = dest_path.stat().st_size / 1024
                payload = format_screenshot_payload(dest_path, user_note, os.getcwd(), dimensions=dims, source_note="macOS Clipboard")
                short_id = cid[:8] if cid else "unknown"
                ws = session.get("workspaces", [""])[0]
                ws_name = Path(ws).name if ws else ""
                queued = queue_message(session, title="Clipboard Screenshot", payload=payload)
                if queued:
                    print(f"✔ Queued clipboard image for Antigravity session [{short_id}] ({ws_name}) | ({dim_str}, {size_kb:.1f} KB)")
                    print("  (Image will be included automatically with your next prompt in agy)")
                    sys.exit(0)
                else:
                    sys.exit(1)
        elif clip_type == "TEXT":
            stdin_data = get_clipboard_text()
            if stdin_data:
                print("📋 Captured text from clipboard.")

    if not stdin_data and not user_note:
        sys.stderr.write("Error: No input provided.\n")
        sys.stderr.write("Usage examples:\n")
        sys.stderr.write("  ls | agy_client\n")
        sys.stderr.write("  git diff | agy_client \"Review changes\"\n")
        sys.stderr.write("  agy_client -c \"Check clipboard\"\n")
        sys.stderr.write("  agy_client \"Run health check\"\n")
        sys.exit(1)

    data_size = len(stdin_data.encode("utf-8"))
    if data_size > MAX_PAYLOAD_BYTES:
        sys.stderr.write(f"Warning: Piped content is large ({data_size / 1024 / 1024:.2f} MB). Truncating to 1 MB...\n")
        stdin_data = stdin_data[:MAX_PAYLOAD_BYTES] + "\n\n... [Truncated by agy_client: Exceeded 1MB limit] ..."

    payload = format_payload(stdin_data, user_note, os.getcwd(), raw=args.raw)
    cid = session.get("conversation_id", "")
    short_id = cid[:8] if cid else "unknown"
    ws = session.get("workspaces", [""])[0]
    ws_name = Path(ws).name if ws else ""

    success = queue_message(session, title=args.title, payload=payload)
    if success:
        line_count = len(stdin_data.splitlines()) if stdin_data else 0
        size_kb = len(payload.encode("utf-8")) / 1024
        print(f"✔ Queued for Antigravity session [{short_id}] ({ws_name}) | {line_count} lines ({size_kb:.1f} KB)")
        print("  (Context will be included automatically with your next prompt in agy)")
        sys.exit(0)
    else:
        sys.exit(1)

if __name__ == "__main__":
    run_cli()

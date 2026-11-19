"""Command line: ./callrec record | process | ui | list | calendar | doctor | keys | notes"""

import argparse
import getpass
import json
import subprocess
import sys
import threading
from datetime import datetime

from . import HELPER, config, integrations, meetings, pipeline
from .transcribe import ts


def _current_event(cfg):
    if cfg.get("calendar", {}).get("source", "macos") == "off":
        return None
    try:
        from .calendar_src import current_and_next, fetch
        current, _ = current_and_next(fetch(cfg))
        return current if current and current.get("is_call") else None
    except Exception as e:
        print(f"(calendar unavailable: {e})")
        return None


def cmd_record(args, cfg):
    from .capture import CaptureError
    from .session import Session

    event = None if args.title else _current_event(cfg)
    title = args.title or (event or {}).get("title") or f"Call {datetime.now():%-I:%M %p}"

    def on_event(kind, data):
        if kind == "segment":
            print(f"\r  [{ts(data['start'])}] {data['speaker']}: {data['text']}")
        elif kind == "notice":
            print(f"  ⚠ {data['message']}")
        elif kind == "capture_ended":
            print(f"\n  Capture ended: {data.get('message', 'helper stopped')} — press Enter to finish.")

    session = Session(cfg, title, event, on_event, live=not args.no_live, auto_title=not args.title and not event)
    try:
        session.start()
    except CaptureError as e:
        print(f"✗ {e}\n  {e.hint}" if e.hint else f"✗ {e}")
        return 1
    print(f"● Recording “{title}” → {session.meeting.path}")
    if event:
        print(f"  Calendar: {len(event.get('attendees', []))} attendees")
    print("  Type notes and press Enter (they're saved as you go). Ctrl-C or /stop to finish.\n")
    try:
        while not session.ended_unexpectedly.is_set():
            line = input()
            if line.strip() in ("/stop", "/q"):
                break
            if line.strip():
                with open(session.meeting.my_notes_path, "a") as f:
                    f.write(f"- {line.strip()}\n")
    except (KeyboardInterrupt, EOFError):
        pass
    meeting = session.stop()
    print(f"\n■ Stopped after {ts(meeting.meta().get('duration', 0))}.")
    if args.no_process:
        print(f"  Process later with: ./callrec process \"{meeting.id}\"")
        return 0
    return _process(meeting, cfg)


def _process(meeting, cfg):
    try:
        pipeline.process(meeting, cfg, progress=lambda step, msg: print(f"  {msg}"))
    except Exception as e:
        print(f"✗ {e}")
        return 1
    integrations.run_auto(meeting, cfg, log=lambda m: print(f"  {m}"))
    print(f"✓ Notes: {meeting.notes_path}")
    return 0


def cmd_process(args, cfg):
    meeting = meetings.resolve(cfg, args.meeting)
    if not meeting:
        print(f"No meeting found for {args.meeting!r}. Try ./callrec list")
        return 1
    return _process(meeting, cfg)


def cmd_list(args, cfg):
    for m in meetings.list_all(cfg)[: args.n]:
        started = datetime.fromisoformat(m["started_at"]).strftime("%a %b %-d %-I:%M %p") if m.get("started_at") else ""
        print(f"{started:>20}  {ts(m.get('duration') or 0):>7}  {m.get('status', ''):<12} {m['id']}")


def cmd_calendar(args, cfg):
    from .calendar_src import fetch
    for ev in fetch(cfg):
        start = datetime.fromisoformat(ev["start"])
        tag = "📞" if ev["is_call"] else "  "
        when = "all day " if ev["all_day"] else f"{start:%-I:%M %p}"
        print(f"{tag} {when:>8}  {ev['title']}  ({len(ev['attendees'])} attendees)")


def cmd_doctor(args, cfg):
    from .summarize import backend, cli_status
    ok = lambda b: "✓" if b else "✗"
    print(f"{ok(HELPER.exists())} capture helper: {HELPER}")
    if HELPER.exists():
        perms = json.loads(subprocess.run([str(HELPER), "permissions"], capture_output=True, text=True).stdout)
        for key in ("microphone", "system_audio", "calendar"):
            print(f"  {key}: {perms[key]}")
    try:
        from huggingface_hub import try_to_load_from_cache
        cached = try_to_load_from_cache(cfg["whisper_model"], "config.json")
        print(f"{ok(isinstance(cached, str))} whisper model: {cfg['whisper_model']}")
    except Exception:
        print(f"? whisper model: {cfg['whisper_model']}")
    secrets = config.secret_status()
    if backend(cfg) == "api":
        print(f"{ok(secrets['openai_api_key'])} OpenAI API key "
              f"{'set' if secrets['openai_api_key'] else 'not set — add one with ./callrec keys'}, "
              f"model {cfg['openai_model']}")
    else:
        status = cli_status()
        if status["logged_in"]:
            print(f"✓ Claude: signed in{' as ' + status['account'] if status['account'] else ''}, "
                  f"model {cfg['claude_model']}")
        elif status["found"]:
            print("✗ Claude: not signed in — run ./callrec login")
        else:
            print("✗ Claude: Claude Code not found — install it from https://claude.com/product/claude-code")
    print(f"  recordings: {config.recordings_dir(cfg)}")
    if args.test:
        import time
        from .session import Session
        print("\nRecording a 5-second test (macOS may ask for permissions — allow them)…")
        session = Session(cfg, "Doctor test", None, live=False)
        session.start()
        time.sleep(5)
        meeting = session.stop()
        levels = session.capture.levels
        for track in ("mic", "system"):
            info = levels.get(track)
            print(f"  {track}: {info['seconds']:.1f}s captured" if info else f"  {track}: no audio received")
        print(f"  test files: {meeting.path}")


def cmd_login(args, cfg):
    from .summarize import cli_env, cli_status, find_claude_cli
    cli = find_claude_cli()
    if not cli:
        print("Claude Code wasn't found. Install it with:\n"
              "  curl -fsSL https://claude.ai/install.sh | bash\n"
              "Then run ./callrec login again.")
        return 1
    print("Sign in to Claude so Call Recorder can write your call notes with your Claude account.\n"
          "Your browser will open; sign in there and come back here.\n")
    subprocess.run([cli, "auth", "login"], env=cli_env())
    status = cli_status()
    if not status["logged_in"]:
        print("\n✗ Not signed in yet. Run ./callrec login to try again.")
        return 1
    print(f"\n✓ Signed in{' as ' + status['account'] if status['account'] else ''}. "
          "Call Recorder will write notes with your Claude account. You can close this window.")


def cmd_keys(args, cfg):
    print("Secrets are stored in your macOS Keychain (service “callrec”). Leave blank to keep the current value.")
    for name, label in (("openai_api_key", "OpenAI API key (only if Settings → Claude uses a key)"),):
        value = getpass.getpass(f"{label} ({'set' if config.get_secret(name) else 'not set'}): ").strip()
        if value:
            config.set_secret(name, value)
            print("  saved")


def cmd_notes(args, cfg):
    meeting = meetings.resolve(cfg, args.meeting)
    if not meeting:
        print("No such meeting")
        return 1
    integrations.save_to_apple_notes(meeting, cfg)
    print(f"✓ Saved to Apple Notes folder “{cfg['apple_notes']['folder']}”")


def cmd_ui(args, cfg):
    from .server import serve
    serve(cfg, port=args.port or cfg.get("port", 8765), open_browser=not args.no_browser)


def cmd_import_granola(args, cfg):
    from .granola import import_csv
    stats = import_csv(args.csv, cfg, dry_run=args.dry_run)
    verb = "Would import" if args.dry_run else "Imported"
    print(f"{verb} {len(stats['imported'])} of {stats['rows']} Granola meetings into {config.recordings_dir(cfg)}")
    if stats["already_imported"]:
        print(f"  {stats['already_imported']} were imported before")
    for line in stats["recorded_already"]:
        print(f"  skipped, already recorded: {line}")


def cmd_mcp(args, cfg):
    from .mcp_server import main as serve_mcp
    serve_mcp()


def main(argv=None):
    parser = argparse.ArgumentParser(prog="callrec", description="Record calls → Whisper → notes with Claude.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("record", help="record a call from the terminal")
    p.add_argument("--title")
    p.add_argument("--no-live", action="store_true", help="skip live transcription")
    p.add_argument("--no-process", action="store_true", help="don't transcribe/summarize afterwards")
    p = sub.add_parser("process", help="(re)transcribe and summarize a recorded call")
    p.add_argument("meeting", help="meeting id, folder path, or 'last'")
    p = sub.add_parser("list", help="recent calls")
    p.add_argument("-n", type=int, default=20)
    sub.add_parser("calendar", help="today's calendar events")
    p = sub.add_parser("doctor", help="check setup and permissions")
    p.add_argument("--test", action="store_true", help="also record a 5-second test")
    sub.add_parser("login", help="sign in to Claude (notes are written with your Claude account)")
    sub.add_parser("keys", help="set an OpenAI API key (optional)")
    p = sub.add_parser("notes", help="save a call's notes to Apple Notes")
    p.add_argument("meeting")
    p = sub.add_parser("ui", help="open the app UI in your browser")
    p.add_argument("--port", type=int)
    p.add_argument("--no-browser", action="store_true")
    sub.add_parser("mcp", help="MCP server that lets Claude search your calls (Claude starts it itself)")
    p = sub.add_parser("import-granola", help="import meetings from a Granola CSV export (notes + transcripts)")
    p.add_argument("csv")
    p.add_argument("--dry-run", action="store_true", help="show what would be imported")
    args = parser.parse_args(argv)
    cfg = config.load()
    handler = {"record": cmd_record, "process": cmd_process, "list": cmd_list, "calendar": cmd_calendar,
               "doctor": cmd_doctor, "login": cmd_login, "keys": cmd_keys, "notes": cmd_notes, "ui": cmd_ui,
               "mcp": cmd_mcp, "import-granola": cmd_import_granola}
    return handler[args.cmd](args, cfg) or 0


if __name__ == "__main__":
    sys.exit(main())

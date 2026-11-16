"""Call Recorder: record calls, transcribe them with Whisper, and turn them into notes with Claude."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HELPER = ROOT / "bin" / "callrec-capture"
STATIC = Path(__file__).resolve().parent / "static"

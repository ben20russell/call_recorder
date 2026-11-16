"""Settings (~/.callrec/config.json) and secrets (macOS Keychain, overridable by env vars)."""

import copy
import json
import os
import subprocess
import threading
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("CALLREC_HOME") or Path.home() / ".callrec")
CONFIG_PATH = CONFIG_DIR / "config.json"
KEYCHAIN_SERVICE = "callrec"

DEFAULTS = {
    "my_name": "Me",
    "my_email": "",
    "other_label": "Them",
    "recordings_dir": "~/Documents/Call Recordings",
    "mic_device": "",
    "capture_system": True,
    "live_transcription": True,
    "detect_calls": True,  # popup when Zoom, Teams, Meet… turns on the mic
    "stop_when_call_ends": True,  # stop recording once you leave the call (its app lets go of the mic)
    "whisper_model": "mlx-community/whisper-large-v3-turbo",
    "language": "en",
    "keep_raw_pcm": False,
    "claude_backend": "cli",  # cli: your Claude login via Claude Code | api: Anthropic API key
    "claude_model": "claude-opus-5-5",
    "claude_effort": "medium",
    "port": 8765,
    "todo_people": "",  # Live To-Dos: whose to-dos to show besides yours ("Tyler, Luwam, Margot")
    "calendar": {
        "source": "macos",  # macos | ics | off
        "ics_url": "",
        "notify": True,
        "auto_record": False,
        "auto_stop_silence_minutes": 3,
        "days_ahead": 7,
        "calendars": [],  # calendars whose meetings count (macOS Calendar names); empty = all
    },
    "apple_notes": {
        "folder": "Call Notes",
        "auto_save": False,
    },
}

# Secret name -> environment variable that overrides the Keychain value.
SECRETS = {
    "anthropic_api_key": "ANTHROPIC_API_KEY",
}

_lock = threading.Lock()


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def load() -> dict:
    with _lock:
        if CONFIG_PATH.exists():
            try:
                return _merge(DEFAULTS, json.loads(CONFIG_PATH.read_text()))
            except json.JSONDecodeError:
                pass
        return copy.deepcopy(DEFAULTS)


def save(cfg: dict) -> None:
    with _lock:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        tmp = CONFIG_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(cfg, indent=2))
        tmp.replace(CONFIG_PATH)


def update(changes: dict) -> dict:
    cfg = _merge(load(), changes)
    save(cfg)
    return cfg


def recordings_dir(cfg: dict) -> Path:
    path = Path(os.path.expanduser(cfg["recordings_dir"]))
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_secret(name: str) -> str:
    env = os.environ.get(SECRETS[name], "")
    if env:
        return env
    result = subprocess.run(
        ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", name, "-w"],
        capture_output=True, text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def set_secret(name: str, value: str) -> None:
    if name not in SECRETS:
        raise KeyError(name)
    if not value:
        subprocess.run(["security", "delete-generic-password", "-s", KEYCHAIN_SERVICE, "-a", name],
                       capture_output=True)
        return
    subprocess.run(
        ["security", "add-generic-password", "-U", "-s", KEYCHAIN_SERVICE, "-a", name, "-w", value],
        check=True, capture_output=True,
    )


def secret_status() -> dict:
    return {name: bool(get_secret(name)) for name in SECRETS}

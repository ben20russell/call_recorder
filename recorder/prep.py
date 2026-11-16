"""Notes written ahead of a call, from Upcoming: an agenda, questions to ask. They wait here, one file per calendar
event in a hidden folder inside the recordings folder, and are copied into the call's own notes (My notes) when
it's recorded."""

import hashlib
import json
import threading
from collections import Counter
from datetime import datetime, timedelta

from . import config

FOLDER = ".upcoming-notes"
KEEP_DAYS = 30  # notes already copied into a recorded call are dropped from here this long after the call
_lock = threading.Lock()


def _dir(cfg: dict):
    return config.recordings_dir(cfg) / FOLDER


def _path(cfg: dict, event_id: str):
    return _dir(cfg) / (hashlib.sha1(event_id.encode()).hexdigest()[:16] + ".json")


def _base(event_id: str) -> str:
    return event_id.rsplit("@", 1)[0]  # an event's id is "<calendar id>@<start>"


def load_all(cfg: dict) -> dict[str, dict]:
    out = {}
    for path in _dir(cfg).glob("*.json"):
        try:
            item = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if item.get("event_id") and item.get("text", "").strip():
            out[item["event_id"]] = item
    return out


def for_events(cfg: dict, events: list[dict], notes: dict | None = None) -> dict[str, dict]:
    """Each event's notes, by event id. An event's id changes with its start time, so a call that was moved
    keeps its notes: a note whose time is still on the calendar's days but no longer on the calendar goes to
    the one event with the same calendar id."""
    notes = load_all(cfg) if notes is None else notes
    out = {e["id"]: notes[e["id"]] for e in events if e["id"] in notes}
    if not events:
        return out
    ids = {e["id"] for e in events}
    first = min(e["start"] for e in events)
    moved = {_base(k): v for k, v in notes.items() if k not in ids and v.get("start", "") >= first[:10]}
    count = Counter(_base(e["id"]) for e in events)
    for e in events:
        base = _base(e["id"])
        if e["id"] not in out and base in moved and count[base] == 1:
            out[e["id"]] = moved[base]
    return out


def save(cfg: dict, event: dict, text: str, was: dict | None = None) -> None:
    """Keep `text` as the notes for `event` (none: forget them). `was`: the notes it had under an old id."""
    with _lock:
        path = _path(cfg, event["id"])
        if text.strip():
            path.parent.mkdir(parents=True, exist_ok=True)
            item = {"event_id": event["id"], "title": event.get("title", ""), "start": event.get("start", ""),
                    "end": event.get("end", ""), "text": text, "updated": datetime.now().astimezone().isoformat(),
                    "copied_to": (was or {}).get("copied_to", [])}
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(item, indent=1))
            tmp.replace(path)
        else:
            path.unlink(missing_ok=True)
        if was and was.get("event_id") != event["id"]:
            _path(cfg, was["event_id"]).unlink(missing_ok=True)
        _prune(cfg)


def copied(cfg: dict, item: dict, meeting_id: str) -> None:
    """Note that these notes went into a recorded call (so they can be dropped from here after a while)."""
    with _lock:
        path = _path(cfg, item["event_id"])
        if path.exists():
            item = {**item, "copied_to": [*item.get("copied_to", []), meeting_id]}
            path.write_text(json.dumps(item, indent=1))


def _prune(cfg: dict) -> None:
    cutoff = (datetime.now().astimezone() - timedelta(days=KEEP_DAYS)).isoformat()
    for event_id, item in load_all(cfg).items():
        if item.get("copied_to") and (item.get("end") or item.get("start") or cutoff) < cutoff:
            _path(cfg, event_id).unlink(missing_ok=True)

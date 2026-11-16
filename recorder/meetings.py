"""A meeting is a folder under the recordings dir holding audio, notes, transcript and meta.json."""

import json
import os
import re
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path

from . import config
from .calendar_src import display_name

_lock = threading.Lock()


def slugify(text: str) -> str:
    slug = re.sub(r"[^\w\s-]", "", text).strip()
    return re.sub(r"\s+", " ", slug)[:60] or "Call"


class Meeting:
    def __init__(self, path: Path):
        self.path = path
        self.id = path.name

    # -- files -----------------------------------------------------------------
    @property
    def meta_path(self) -> Path:
        return self.path / "meta.json"

    @property
    def my_notes_path(self) -> Path:
        return self.path / "my-notes.md"

    @property
    def notes_path(self) -> Path:
        return self.path / "notes.md"

    @property
    def transcript_path(self) -> Path:
        return self.path / "transcript.json"

    @property
    def live_path(self) -> Path:
        return self.path / "live-transcript.jsonl"

    @property
    def summary_path(self) -> Path:
        return self.path / "summary.json"

    # -- meta ------------------------------------------------------------------
    def meta(self) -> dict:
        try:
            return json.loads(self.meta_path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return {"id": self.id, "title": self.id}

    def update(self, **changes) -> dict:
        with _lock:
            meta = self.meta()
            meta.update(changes)
            tmp = self.meta_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(meta, indent=2, default=str))
            tmp.replace(self.meta_path)
            return meta

    def read(self, path: Path, default=""):
        try:
            text = path.read_text()
        except FileNotFoundError:
            return default
        if path.suffix == ".json":
            return json.loads(text)
        return text

    def summary(self) -> dict | None:
        return self.read(self.summary_path, None)

    def transcript(self) -> list[dict]:
        return self.read(self.transcript_path, [])

    def my_notes(self) -> str:
        return self.read(self.my_notes_path, "")


def create(cfg: dict, title: str, event: dict | None = None, started: datetime | None = None,
           auto_title: bool = False) -> Meeting:
    started = started or datetime.now().astimezone()
    title = title.strip() or "Call"
    root = config.recordings_dir(cfg)
    base = f"{started:%Y-%m-%d %H%M} {slugify(title)}"
    path = root / base
    n = 2
    while path.exists():
        path = root / f"{base} ({n})"
        n += 1
    path.mkdir(parents=True)
    meeting = Meeting(path)
    attendees = (event or {}).get("attendees", [])
    meeting.update(id=meeting.id, title=title, started_at=started.isoformat(), ended_at=None, duration=0,
                   status="recording", event=event, attendees=attendees, error="", auto_title=auto_title)
    meeting.my_notes_path.touch()
    return meeting


def get(cfg: dict, meeting_id: str) -> Meeting | None:
    root = config.recordings_dir(cfg)
    path = (root / meeting_id).resolve()
    if path.parent != root.resolve() or not (path / "meta.json").exists():
        return None
    return Meeting(path)


def resolve(cfg: dict, ref: str) -> Meeting | None:
    """Accept a meeting id, a folder path, or 'last'."""
    if ref == "last":
        items = list_all(cfg)
        return get(cfg, items[0]["id"]) if items else None
    path = Path(ref).expanduser()
    if path.is_dir() and (path / "meta.json").exists():
        return Meeting(path.resolve())
    return get(cfg, ref)


def _preview(notes_markdown: str) -> str:
    """The first few lines of notes, as plain text: a preview for imported calls that come without a summary."""
    lines = [re.sub(r"^\s*(#+|[-*])\s*|\*\*", "", line).strip() for line in notes_markdown.splitlines()]
    text = " · ".join(line for line in lines if line)
    return text[:240].rsplit(" ", 1)[0] + "…" if len(text) > 240 else text


def _summary_item(meeting: Meeting) -> dict:
    meta = meeting.meta()
    summary = meeting.summary() or {}
    people = summary.get("participants") or [display_name(a) for a in meta.get("attendees") or []
                                             if not a.get("is_me")]
    return {
        "id": meeting.id,
        **{k: meta.get(k) for k in ("title", "started_at", "duration", "status", "error")},
        "event_id": (meta.get("event") or {}).get("id"),
        "summary": summary.get("summary") or _preview(summary.get("notes_markdown", "")),
        "action_items": len(summary.get("action_items", [])),
        "participants": [p for p in people if p],
        "source": meta.get("source", ""),
    }


# Deleted calls wait in this folder (inside the recordings folder, so they're out of every list and search) until
# undone, and are removed for good after KEEP_DELETED_DAYS.
DELETED = "Recently Deleted"
KEEP_DELETED_DAYS = 30


def delete(cfg: dict, meeting_id: str) -> None:
    meeting = get(cfg, meeting_id)
    if not meeting:
        raise KeyError(meeting_id)
    bin_ = config.recordings_dir(cfg) / DELETED
    bin_.mkdir(exist_ok=True)
    dest = bin_ / meeting.id
    if dest.exists():  # deleted once before (then re-created): keep both
        dest = bin_ / f"{meeting.id} · deleted {datetime.now():%Y-%m-%d %H%M%S}"
    meeting.path.rename(dest)
    os.utime(dest)  # starts the KEEP_DELETED_DAYS clock


def restore(cfg: dict, meeting_id: str) -> Meeting:
    """Bring back the most recently deleted call with this id."""
    root = config.recordings_dir(cfg)
    found = [p for p in (root / DELETED).glob("*")
             if p.is_dir() and (p.name == meeting_id or p.name.startswith(f"{meeting_id} · deleted "))]
    if not found:
        raise KeyError(meeting_id)
    dest = root / meeting_id
    if dest.exists():
        raise FileExistsError(meeting_id)
    max(found, key=lambda p: p.stat().st_mtime).rename(dest)
    return Meeting(dest)


def deleted(cfg: dict) -> list[Meeting]:
    return [Meeting(p.parent) for p in (config.recordings_dir(cfg) / DELETED).glob("*/meta.json")]


def purge_deleted(cfg: dict) -> int:
    """Remove calls deleted more than KEEP_DELETED_DAYS ago. Returns how many."""
    cutoff = time.time() - KEEP_DELETED_DAYS * 86400
    old = [p for p in (config.recordings_dir(cfg) / DELETED).glob("*") if p.is_dir() and p.stat().st_mtime < cutoff]
    for path in old:
        shutil.rmtree(path, ignore_errors=True)
    return len(old)


def list_all(cfg: dict) -> list[dict]:
    root = config.recordings_dir(cfg)
    me = cfg.get("my_name", "").lower()
    out = []
    for meta_file in root.glob("*/meta.json"):
        item = _summary_item(Meeting(meta_file.parent))
        item["participants"] = [p for p in item["participants"] if p.lower() not in (me, "me")]
        out.append(item)
    return sorted(out, key=lambda m: m.get("started_at") or "", reverse=True)

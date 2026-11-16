"""Import calls from a Granola CSV export (Settings → Export in Granola): each meeting becomes a call in your
recordings folder, with Granola's summary as its notes, your Granola notes as My notes, and its transcript,
so it shows up in All notes, in search and for Claude. No audio, and nothing is sent to Claude.

The export's transcripts have no timestamps and usually no speakers, so lines are grouped into paragraphs
and given approximate times from their word count ("approx" on each line, "times_estimated" on the call).
Meetings Call Recorder already recorded properly are skipped; re-importing skips what's already in."""

import csv
import json
import re
from datetime import datetime, timedelta
from pathlib import Path

from . import config
from .meetings import Meeting, deleted, list_all, slugify
from .pipeline import render_notes
from .transcribe import render_transcript

WORDS_PER_SECOND = 2.5   # typical conversational pace, for the estimated times
PARAGRAPH_WORDS = 70     # aim for paragraphs about this long, ending at a sentence
SPEAKER = re.compile(r"^\s*(Speaker [A-Z]|Me|Them)\s*:\s*(.*)$")


def _norm(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()


def _paragraphs(transcript: str) -> list[dict]:
    """Transcript lines → paragraphs with estimated times. A "Speaker A:" prefix starts a new speaker."""
    segments, words, speaker, buffer = [], 0, "", []

    def flush():
        nonlocal words, buffer
        text = " ".join(buffer).strip()
        if text:
            n = len(text.split())
            start = words / WORDS_PER_SECOND
            # end 3 s short of the next start, so paragraphs aren't merged back together for display
            segments.append({"start": round(start, 1), "end": round(start + max(0.5, n / WORDS_PER_SECOND - 3), 1),
                             "text": text, "speaker": speaker, "approx": True})
            words += n
        buffer = []

    for line in transcript.splitlines():
        line = line.strip()
        if not line:
            continue
        labeled = SPEAKER.match(line)
        if labeled:
            if labeled.group(1) != speaker:
                flush()
                speaker = labeled.group(1)
            line = labeled.group(2)
        buffer.append(line)
        if len(" ".join(buffer).split()) >= PARAGRAPH_WORDS and line[-1:] in ".?!":
            flush()
        elif len(" ".join(buffer).split()) >= 2 * PARAGRAPH_WORDS:
            flush()
    flush()
    return segments


def _notes_markdown(summary: str) -> str:
    """Granola's summary under Call Recorder's "Notes" heading: its "# Topic" headings become "### Topic"."""
    return re.sub(r"^(#{1,4})(\s)", lambda m: "#" * min(len(m.group(1)) + 2, 6) + m.group(2), summary.strip(), flags=re.M)


def _covered(start: datetime, title: str, existing: list[dict]) -> str | None:
    """A call Call Recorder already has for this meeting (same title within an hour, with a real transcript)."""
    for call in existing:
        if call["granola_id"] or abs((call["start"] - start).total_seconds()) > 3600:
            continue
        a, b = _norm(call["title"]), _norm(title)
        if a and b and (a in b or b in a) and call["transcript_chars"] >= 2000:
            return call["id"]
    return None


def import_csv(path: str | Path, cfg: dict, dry_run: bool = False) -> dict:
    csv.field_size_limit(1 << 30)
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    root = config.recordings_dir(cfg)
    existing = []
    for item in list_all(cfg):
        meeting = Meeting(root / item["id"])
        meta = meeting.meta()
        try:
            start = datetime.fromisoformat(item["started_at"])
        except (TypeError, ValueError):
            continue
        existing.append({"id": item["id"], "title": item.get("title") or "", "start": start,
                         "granola_id": meta.get("granola_id"),
                         "transcript_chars": sum(len(s.get("text", "")) for s in meeting.transcript())})
    already = {c["granola_id"] for c in existing if c["granola_id"]}
    already |= {m.meta().get("granola_id") for m in deleted(cfg)} - {None}  # deleted stays deleted

    stats = {"rows": len(rows), "imported": [], "already_imported": 0, "recorded_already": []}
    for row in rows:
        gid = row["document_id"].strip()
        if gid in already:
            stats["already_imported"] += 1
            continue
        title = (row.get("document_title") or "").strip() or "Untitled meeting"
        start = datetime.fromisoformat(row["document_created"].replace("Z", "+00:00")).astimezone()
        covered = _covered(start, title, existing)
        if covered:
            stats["recorded_already"].append(f"{start:%b %-d} {title} (kept {covered})")
            continue
        stats["imported"].append(f"{start:%b %-d} {title}")
        if dry_run:
            continue

        base = f"{start:%Y-%m-%d %H%M} {slugify(title)}"
        folder, n = root / base, 2
        while folder.exists():
            folder, n = root / f"{base} ({n})", n + 1
        folder.mkdir(parents=True)
        meeting = Meeting(folder)
        segments = _paragraphs(row.get("transcript") or "")
        my_notes = (row.get("notes") or "").strip()
        meta = meeting.update(
            id=meeting.id, title=title, started_at=start.isoformat(), ended_at=None, duration=0, status="done",
            event=None, attendees=[], error="", auto_title=False, source="granola", granola_id=gid,
            transcript_source="Granola", times_estimated=True, system_audio=None,
            imported_at=datetime.now().astimezone().isoformat(), processed_at=datetime.now().astimezone().isoformat())
        summary = {"title": title, "summary": "", "notes_markdown": _notes_markdown(row.get("summary") or ""),
                   "decisions": [], "action_items": [], "open_questions": [], "participants": [], "_backend": "granola"}
        meeting.summary_path.write_text(json.dumps(summary, indent=2))
        meeting.my_notes_path.write_text(my_notes + ("\n" if my_notes else ""))
        meeting.transcript_path.write_text(json.dumps(segments, indent=1))
        (folder / "transcript.md").write_text(render_transcript(segments))
        meeting.notes_path.write_text(render_notes(meta, summary, my_notes, segments))
        existing.append({"id": meeting.id, "title": title, "start": start, "granola_id": gid,
                         "transcript_chars": sum(len(s["text"]) for s in segments)})
    return stats

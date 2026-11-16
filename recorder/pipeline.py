"""After a call: archive audio → transcribe → tell voices apart → summarize with Claude (which also names
the voices) → write notes.md → integrations."""

import json
import traceback
from datetime import datetime

from . import audio, speakers, transcribe
from .calendar_src import display_name
from .meetings import Meeting
from .summarize import summarize
from .transcribe import ts


def render_notes(meta: dict, summary: dict, my_notes: str, segments: list[dict]) -> str:
    started = datetime.fromisoformat(meta["started_at"]) if meta.get("started_at") else None
    title = meta.get("title") or summary.get("title") or "Call"
    out = [f"# {title}", ""]
    details = []
    if started:
        details.append(f"**When:** {started:%a %b %-d, %Y · %-I:%M %p}")
    if meta.get("duration"):
        details.append(f"**Length:** {ts(meta['duration'])}")
    if meta.get("transcript_source"):
        details.append(f"**Transcript:** from {meta['transcript_source']}")
    people = summary.get("participants") or [display_name(a) for a in meta.get("attendees") or []]
    if people:
        details.append(f"**Participants:** {', '.join(p for p in people if p)}")
    out += ["  \n".join(details), ""]

    if summary.get("summary", "").strip():  # imported notes (Granola) come without one
        out += ["## Summary", "", summary["summary"].strip(), ""]
    if summary.get("notes_markdown", "").strip():
        out += ["## Notes", "", summary["notes_markdown"].strip(), ""]
    if summary.get("decisions"):
        out += ["## Decisions", ""] + [f"- {d}" for d in summary["decisions"]] + [""]
    if summary.get("action_items"):
        out += ["## Action items", ""]
        for item in summary["action_items"]:
            due = f" — due {item['due']}" if item.get("due") else ""
            out.append(f"- [ ] **{item['owner']}:** {item['task']}{due}")
        out.append("")
    if summary.get("open_questions"):
        out += ["## Open questions", ""] + [f"- {q}" for q in summary["open_questions"]] + [""]
    if my_notes.strip():
        out += ["## My notes (raw)", "", my_notes.strip(), ""]
    if segments:
        out += ["## Transcript", "", transcribe.render_transcript(segments), ""]
    return "\n".join(out)


def render_transcript_file(meta: dict, summary: dict, segments: list[dict]) -> str:
    """A call's transcript as a Markdown file of its own (Download transcript on the call page)."""
    started = datetime.fromisoformat(meta["started_at"]) if meta.get("started_at") else None
    title = meta.get("title") or summary.get("title") or "Call"
    details = []
    if started:
        length = meta.get("duration") or 0
        took = f" ({round(length / 60)} min)" if length >= 60 else f" ({int(length)} sec)" if length else ""
        details.append(f"**When:** {started:%a %b %-d, %Y · %-I:%M %p}{took}")
    people = summary.get("participants") or [display_name(a) for a in meta.get("attendees") or []]
    if people:
        details.append(f"**With:** {', '.join(p for p in people if p)}")
    source = meta.get("transcript_source")
    details.append("**Source:** " + (f"imported from {source.title()}; times are approximate" if source else "Call Recorder"))
    return "\n".join([f"# {title} — Transcript", "", "  \n".join(details), "", "---", "",
                      transcribe.render_transcript(segments), ""])


def process(meeting: Meeting, cfg: dict, progress=lambda step, msg: None, cancelled=None) -> dict:
    """Run the whole post-call pipeline. Safe to re-run on an existing meeting. Setting `cancelled` (to record
    more onto the call) stops it at the next step, 30-second Whisper window or second of Claude, raising
    transcribe.Cancelled; saving the audio, the first step, always finishes."""
    def step(name, msg):
        if cancelled is not None and cancelled.is_set():
            raise transcribe.Cancelled()  # before touching the call, which is about to be recorded again
        meeting.update(status=name, step_message=msg)
        progress(name, msg)

    transcribe.cancel_with(cancelled)
    try:
        step("processing", "Saving audio…")
        info = audio.archive(meeting.path, keep_raw=cfg.get("keep_raw_pcm", False))
        meta = meeting.meta()
        if info["duration"]:
            meeting.update(duration=round(info["duration"], 1))
        if meta.get("system_audio") and "system" not in info["tracks"]:
            # System audio was on but nothing arrived, so the mic may hold both sides.
            meeting.update(system_audio=False)

        if meta.get("transcript_source"):  # imported (e.g. from Granola): keep it rather than redo it from the audio
            segments = json.loads(meeting.transcript_path.read_text())
        else:
            step("transcribing", "Transcribing with Whisper…")
            segments = transcribe.transcribe_meeting(meeting, cfg, progress=lambda m: step("transcribing", m))

        table = {}
        if not meta.get("transcript_source"):
            step("transcribing", "Telling voices apart…")
            try:
                segments, table = speakers.identify(meeting, segments, cfg)
            except Exception:  # names are a nicety: keep the plain labels rather than lose the notes
                traceback.print_exc()
            if not table:
                (meeting.path / "speakers.json").unlink(missing_ok=True)

        step("summarizing", "Writing notes with Claude…")
        meta = meeting.meta()
        summary = summarize(meta, meeting.my_notes(), segments, cfg, table, cancelled=cancelled)
        meeting.summary_path.write_text(json.dumps(summary, indent=2))
        if table:
            try:
                segments = speakers.apply_names(meeting, segments, table, summary.get("speakers"), cfg)
            except Exception:
                traceback.print_exc()
        # Replace a placeholder title ("Call 2:05 PM") with Claude's descriptive one.
        if meta.get("auto_title") and summary.get("title"):
            meta = meeting.update(title=summary["title"], auto_title=False)
        meeting.notes_path.write_text(render_notes(meta, summary, meeting.my_notes(), segments))
        meeting.update(status="done", step_message="", error="", processed_at=datetime.now().astimezone().isoformat())
        progress("done", "Notes ready")
        return summary
    except transcribe.Cancelled:
        progress("cancelled", "")
        raise
    except Exception as e:  # surface every failure in the UI rather than losing it in a thread
        meeting.update(status="error", error=str(e), step_message="")
        progress("error", str(e))
        traceback.print_exc()
        raise
    finally:
        transcribe.cancel_with(None)

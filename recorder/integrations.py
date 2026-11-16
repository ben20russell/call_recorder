"""Where finished notes can go: Apple Notes (the full notes)."""

import subprocess
import tempfile
from pathlib import Path

import markdown as md

from .meetings import Meeting


class IntegrationError(RuntimeError):
    pass


# -- Apple Notes -----------------------------------------------------------------------------------

APPLESCRIPT = '''
on run argv
    set folderName to item 1 of argv
    set noteBody to read (POSIX file (item 2 of argv)) as «class utf8»
    set existingId to item 3 of argv
    tell application "Notes"
        if existingId is not "" then
            try
                set body of note id existingId to noteBody
                return existingId
            end try
        end if
        if not (exists folder folderName of default account) then
            make new folder at default account with properties {name:folderName}
        end if
        set newNote to make new note at folder folderName of default account with properties {body:noteBody}
        return id of newNote
    end tell
end run
'''


def save_to_apple_notes(meeting: Meeting, cfg: dict) -> str:
    """Create (or update, if saved before) a note with the call notes. Returns the note id."""
    if not meeting.notes_path.exists():
        raise IntegrationError("This call has no notes yet.")
    meta = meeting.meta()
    text = meeting.notes_path.read_text()
    # Notes takes its title from the first line; keep the transcript out of Notes to stay readable.
    text = text.split("\n## Transcript\n")[0]
    html = md.markdown(text.replace("<", "&lt;"), extensions=["extra", "sane_lists"])
    folder = cfg.get("apple_notes", {}).get("folder") or "Call Notes"
    with tempfile.TemporaryDirectory() as tmp:
        script, body = Path(tmp) / "save.applescript", Path(tmp) / "body.html"
        script.write_text(APPLESCRIPT)
        body.write_text(html)
        result = subprocess.run(["osascript", str(script), folder, str(body), meta.get("apple_note_id", "")],
                                capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise IntegrationError(f"Apple Notes: {result.stderr.strip()}")
    note_id = result.stdout.strip()
    meeting.update(apple_note_id=note_id)
    return note_id


def run_auto(meeting: Meeting, cfg: dict, log=print) -> None:
    """Integrations configured to run automatically after each call."""
    if cfg.get("apple_notes", {}).get("auto_save"):
        try:
            save_to_apple_notes(meeting, cfg)
            log("Saved to Apple Notes")
        except Exception as e:
            log(f"Apple Notes failed: {e}")

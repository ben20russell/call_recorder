"""One active recording: capture + live transcription + the meeting folder they write into.

A recording can be paused and resumed, and a finished call can be recorded onto again (what's said is added to
the end of it). The first stretch of a new recording is captured straight into the call's folder. Every later
stretch is captured into a hidden ".part<N>" folder inside it and appended to the call's own tracks as it
arrives, so whatever reads them (live transcription, the leave and silence checks, processing) sees one call
that simply goes on. Nothing is kept from a pause: the call's timeline is its audio, and `clock` (the wall time
its first second would have been) moves on by the length of each pause.
"""

import shutil
import threading
from datetime import datetime, timedelta
from pathlib import Path

from . import audio, live as live_mod, meetings
from .capture import CaptureError, make_capture
from .live import LiveTranscriber
from .meetings import Meeting

TRACKS = ("mic", "system")
SR = audio.SAMPLE_RATE


def _pad(file: Path, samples: int) -> None:
    with open(file, "ab") as f:
        while samples > 0:
            n = min(samples, 60 * SR)
            f.write(bytes(2 * n))
            samples -= n


def even_tracks(path: Path) -> int:
    """Pad the shorter of the call's tracks with silence so both end together, and return that length (in
    samples). A track with nothing in it is left empty: that's how processing tells a side that never came."""
    lengths = {t: audio.pcm_samples(path / f"{t}.pcm") for t in TRACKS}
    longest = max(lengths.values())
    for t, n in lengths.items():
        if 0 < n < longest:
            _pad(path / f"{t}.pcm", longest - n)
    return longest


def join_part(part: Path, main: Path, base: int) -> None:
    """Append what's new in a stretch's tracks (`part`) to the call's own (`main`), which were `base` samples
    long when it started. How much is new follows from the call's track lengths, so this can be repeated, or
    picked up after a crash, without adding anything twice. A track the call didn't have yet is first filled
    with silence up to `base`, so both sides stay in step."""
    for t in TRACKS:
        src = part / f"{t}.pcm"
        size = src.stat().st_size // 2 * 2 if src.exists() else 0
        if not size:
            continue
        dst = main / f"{t}.pcm"
        have = audio.pcm_samples(dst)
        if have < base:
            _pad(dst, base - have)
            have = base
        pos = (have - base) * 2
        if pos >= size:
            continue
        with open(src, "rb") as f, open(dst, "ab") as out:
            f.seek(pos)
            while pos < size:
                chunk = f.read(min(1 << 20, size - pos))
                if not chunk:
                    break
                out.write(chunk)
                pos += len(chunk)


def finish_parts(main: Path) -> None:
    """After a crash or quit mid-stretch: add what's left in any ".part" folder to the call and remove it."""
    for part in sorted(main.glob(".part*")):
        if part.is_dir():
            try:
                base = int((part / "base").read_text())
            except (OSError, ValueError):
                base = even_tracks(main)
            join_part(part, main, base)
            shutil.rmtree(part, ignore_errors=True)


class Session:
    def __init__(self, cfg: dict, title: str, event: dict | None = None, on_event=lambda kind, data: None,
                 live: bool | None = None, auto_title: bool = False, watching=lambda: True,
                 meeting: Meeting | None = None):
        """`meeting`: a finished call to record onto, instead of a new one."""
        self.cfg = cfg
        self.event = event
        self.on_event = on_event
        self.continuing = meeting is not None
        if meeting:
            self.meeting = meeting
            started = meeting.meta().get("started_at")
            self.started_at = datetime.fromisoformat(started) if started else datetime.now().astimezone()
        else:
            self.started_at = datetime.now().astimezone()
            self.meeting: Meeting = meetings.create(cfg, title, event, self.started_at, auto_title)
        self.clock = self.started_at          # wall time of the call's first second of audio (moves on by pauses)
        self.paused_at: datetime | None = None
        self.capture = None
        self._stretch = 0                     # 0: captured straight into the call's folder; then .part1, .part2…
        self._part: Path | None = None
        self._joiner: threading.Thread | None = None
        self._join_halt = threading.Event()
        self._op = threading.Lock()           # one start / pause / resume / stop at a time
        use_live = cfg.get("live_transcription", True) if live is None else live
        self.live = LiveTranscriber(self.meeting, cfg, on_event, watching) if use_live else None
        self.started = False
        self.stopping = False
        self.ended_unexpectedly = threading.Event()

    @property
    def paused(self) -> bool:
        return self.paused_at is not None

    def _events_from(self, stretch: int):
        return lambda event: self._capture_event(event, stretch)

    def _capture_event(self, event: dict, stretch: int):
        kind = event.get("event")
        if kind == "levels":
            self.on_event("levels", event)
        elif kind in ("source_restarted", "source_info"):
            self.on_event(kind, event)
        elif (kind in ("stopped", "error") and stretch == self._stretch and self.started
              and not self.stopping and not self.paused):
            self.ended_unexpectedly.set()
            self.on_event("capture_ended", event)

    def start(self) -> Meeting:
        with self._op:
            if self.continuing:
                self._record_onto()
            else:
                self.capture = make_capture(self.meeting.path, self.cfg, self._events_from(0))
                try:
                    self.capture.start()
                except Exception:
                    self._discard_if_empty()
                    raise
            self.started = True
            tracks = getattr(self.capture, "tracks", {}) or {"mic": True, "system": True}
            meta = self.meeting.meta() if self.continuing else {}
            self.meeting.update(mic_audio=bool(tracks.get("mic") or meta.get("mic_audio")),
                                system_audio=bool(tracks.get("system") or meta.get("system_audio")))
            for warning in getattr(self.capture, "warnings", []):
                self.on_event("notice", {"level": "error", "message": warning})
            if self.live:
                if self.continuing:  # its earlier lines stay; carry on after them
                    self.live.cursors = {t: audio.pcm_samples(self.meeting.path / f"{t}.pcm") for t in TRACKS}
                    live_mod.reserve_ids(self.meeting)
                self.live.started = self.clock.timestamp()
                self.live.start()
            return self.meeting

    def _record_onto(self):
        """Turn a finished call's saved tracks back into raw audio and start a stretch after it. If that fails,
        the call is left as it was."""
        path, made = self.meeting.path, []
        try:
            for t in TRACKS:
                pcm, m4a = path / f"{t}.pcm", path / f"{t}.m4a"
                if not pcm.exists() and m4a.exists():
                    audio.decode_to_pcm(m4a, pcm)
                    made.append(pcm)
            if not any(audio.pcm_samples(path / f"{t}.pcm") for t in TRACKS):
                raise CaptureError("This call has no recording to add to.")
            self._begin_stretch()
        except Exception:
            for pcm in made:
                pcm.unlink(missing_ok=True)
            raise
        for t in TRACKS:  # the saved copies are out of date now; they're made again when the call is processed
            if (path / f"{t}.pcm").exists():
                (path / f"{t}.m4a").unlink(missing_ok=True)
        resumed = self.meeting.meta().get("recorded_onto") or []
        self.meeting.update(status="recording", step_message="", error="", recorded_onto=resumed + [
            {"at": round(self.elapsed(), 1), "on": datetime.now().astimezone().isoformat()}])

    def _begin_stretch(self):
        """Start capturing a stretch after what the call has so far (into .part<N>, appended as it arrives)."""
        base = even_tracks(self.meeting.path)
        self._stretch += 1
        part = self.meeting.path / f".part{self._stretch}"
        shutil.rmtree(part, ignore_errors=True)
        part.mkdir()
        (part / "base").write_text(str(base))
        capture = make_capture(part, self.cfg, self._events_from(self._stretch))
        try:
            capture.start()
        except Exception:
            shutil.rmtree(part, ignore_errors=True)
            self._stretch -= 1
            raise
        self.capture, self._part = capture, part
        self.clock = datetime.now().astimezone() - timedelta(seconds=base / SR)
        self._join_halt = threading.Event()
        self._joiner = threading.Thread(target=self._join, args=(part, base, self._join_halt), daemon=True)
        self._joiner.start()

    def _join(self, part: Path, base: int, halt: threading.Event):
        while not halt.wait(0.1):
            try:
                join_part(part, self.meeting.path, base)
            except OSError:
                pass
        join_part(part, self.meeting.path, base)  # the rest, now that capture has stopped

    def _end_capture(self):
        if self.capture:
            self.capture.stop()
        if self._joiner:
            self._join_halt.set()
            self._joiner.join(30)
            self._joiner = None
            shutil.rmtree(self._part, ignore_errors=True)
            self._part = None

    def pause(self) -> bool:
        """Stop capturing until resume(). Whether it paused (False: it already was, or it's stopping)."""
        with self._op:
            if self.paused or self.stopping or not self.started:
                return False
            self.paused_at = datetime.now().astimezone()
            self._end_capture()
            length = even_tracks(self.meeting.path)
            self.clock = self.paused_at - timedelta(seconds=length / SR)  # the timer shows what was recorded
            return True

    def resume(self) -> bool:
        """Capture again after a pause, carrying on from where it stopped. Raises CaptureError (still paused)."""
        with self._op:
            if not self.paused or self.stopping:
                return False
            self._begin_stretch()
            self.paused_at = None
            if self.live:
                self.live.started = self.clock.timestamp()
            return True

    def _discard_if_empty(self):
        """A recording that never started leaves only empty files behind; don't list it as a call."""
        files = [p for p in self.meeting.path.iterdir() if p.name != "meta.json"]
        if all(p.is_file() and p.stat().st_size == 0 for p in files):
            shutil.rmtree(self.meeting.path, ignore_errors=True)
        else:
            self.meeting.update(status="error", error="Recording didn't start.")

    def elapsed(self) -> float:
        """Seconds of the call recorded so far (pauses don't count)."""
        return ((self.paused_at or datetime.now().astimezone()) - self.clock).total_seconds()

    def stop(self) -> Meeting:
        with self._op:
            self.stopping = True
            try:
                self._end_capture()
            finally:
                if self.live:
                    try:
                        self.live.stop()
                    except Exception:
                        pass  # live text is a nicety; the recording itself is what matters
            ended = datetime.now().astimezone()
            self.meeting.update(status="recorded", ended_at=ended.isoformat(), duration=round(self.elapsed(), 1))
            return self.meeting

    def state(self) -> dict:
        meta = self.meeting.meta()
        return {"id": self.meeting.id, "title": meta.get("title"), "started_at": self.started_at.isoformat(),
                "clock": self.clock.isoformat(), "paused": self.paused, "continuing": self.continuing,
                "elapsed": self.elapsed(), "event": self.event, "levels": getattr(self.capture, "levels", {}),
                "live": self.live is not None}

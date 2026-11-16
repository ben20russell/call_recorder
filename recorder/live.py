"""Live transcription while recording: tails the growing .pcm files, cuts them at natural pauses,
and runs Whisper on each chunk. Segments are streamed to the UI and logged to live-transcript.jsonl.
Lines from the other side are named by who Google Meet showed talking (when the Chrome extension is
reporting; see meet.py), or else when their voice matches someone in the voice library.
While someone is still talking and you're looking at the live transcript, the words so far are
re-transcribed about once a second and sent as a "partial" line, which the finished segment replaces at the
next pause. That keeps the GPU busy, so it only happens while the transcript is on screen.
"""

import itertools
import json
import threading
import time
from datetime import datetime

import numpy as np

from . import audio, meet, speakers, transcribe
from .meetings import Meeting

SR = audio.SAMPLE_RATE
WINDOW = 20 * SR       # most untranscribed audio looked at per step
MAX_CHUNK = 10 * SR    # cut even without a pause once a chunk is this long
PAUSE = 0.5            # seconds of quiet that end an utterance
TAIL = 0.3             # quiet kept after the last word of a chunk
PARTIAL_EVERY = 0.8    # seconds between updates of the line still being spoken
PARTIAL_MIN = 0.6      # seconds of speech before it's shown

_ids = itertools.count(1)


def plan_chunk(samples: np.ndarray) -> tuple[int, int]:
    """Decide what to do with untranscribed audio. Returns (skip, cut): drop samples[:skip] (leading
    silence) and transcribe samples[skip:cut]. cut == 0 means wait for more audio. Chunks start just
    before speech and end at a real pause, so Whisper never sees a sliver of a word."""
    regions = audio.speech_regions(samples, min_gap=PAUSE, pad=0.0, min_len=0.15)
    total = len(samples) / SR
    if not regions:
        return max(0, len(samples) - SR // 2), 0  # all quiet; keep the last 0.5 s in case speech starts
    skip = max(0, int((regions[0][0] - 0.3) * SR))
    ends = [b for _, b in regions if total - b >= PAUSE and b * SR - skip <= MAX_CHUNK]
    if ends:
        return skip, int(min(total, ends[-1] + TAIL) * SR)
    if len(samples) - skip >= MAX_CHUNK:
        return skip, skip + MAX_CHUNK
    return skip, 0


def reserve_ids(meeting: Meeting) -> None:
    """New lines' ids come after the ones a call already has (one being recorded onto keeps its earlier lines,
    and the page finds lines by id)."""
    global _ids
    try:
        top = max((json.loads(line).get("id") or 0 for line in meeting.live_path.read_text().splitlines()
                   if line.strip()), default=0)
    except (OSError, ValueError):
        return
    _ids = itertools.count(max(next(_ids), top + 1))


class LiveTranscriber(threading.Thread):
    def __init__(self, meeting: Meeting, cfg: dict, on_event=lambda kind, data: None, watching=lambda: True):
        super().__init__(daemon=True)
        self.meeting = meeting
        self.cfg = cfg
        self.on_event = on_event
        self.watching = watching  # whether the live transcript is on screen (the line being spoken is shown)
        self.cursors = {"system": 0, "mic": 0}
        self.recent = {"system": [], "mic": []}
        self.thanks = {"system": [], "mic": []}  # ids of standalone "Thank you." lines shown so far
        self.partials: dict[str, dict | None] = {"system": None, "mic": None}  # the line still being spoken
        self.started = datetime.fromisoformat(meeting.meta()["started_at"]).timestamp()
        self.prompt = transcribe.vocabulary_prompt(meeting.meta(), cfg)
        self.meet = meet.Timeline(meeting.path / meet.FILE, cfg["my_name"])
        try:
            self.voices = {n: v for n, v in speakers.known_voices().items() if n != cfg["my_name"]}
        except Exception:
            self.voices = {}
        self._halt = threading.Event()  # (not `_stop`: that name belongs to threading.Thread)

    def stop(self, timeout: float = 30):
        self._halt.set()
        self.join(timeout)

    def run(self):
        while not self._halt.is_set():
            worked = False
            try:  # finished lines first; the line still being spoken when there's nothing to finish
                for track in ("system", "mic"):
                    if not self._halt.is_set():
                        worked |= self._step(track)
                if not worked and not self._halt.is_set():
                    if self.watching():
                        worked = self._partial()
                    else:
                        for track in ("system", "mic"):
                            self._partial_done(track)
            except Exception as e:  # never let live transcription take down the recording
                self.on_event("live_error", {"message": str(e)})
                self._halt.wait(5)
            if not worked:
                self._halt.wait(0.2)

    def _step(self, track: str) -> bool:
        path = self.meeting.path / f"{track}.pcm"
        cursor = self.cursors[track]
        available = audio.pcm_samples(path) - cursor
        if available < int(0.8 * SR):
            return False
        samples = audio.read_pcm(path, cursor, min(available, WINDOW))
        skip, cut = plan_chunk(samples)
        if cut <= skip:
            self.cursors[track] += skip
            if skip:
                self._partial_done(track)
            return skip > SR // 10
        self.cursors[track] += cut
        self._partial_done(track)
        offset = (cursor + skip) / SR
        chunk = samples[skip:cut]
        for seg in transcribe.whisper(chunk, self.cfg, self.prompt, live=True):
            line = {"start": seg["start"] + offset, "end": seg["end"] + offset, "text": seg["text"]}
            if track == "system":
                line["speaker"] = self._who(line) or self._voice(chunk, seg)
            self._emit(track, line)
        return True

    def _partial(self) -> bool:
        """Re-transcribe the speech since the last finished line of whichever side spoke last, at most about
        once a second (less often when Whisper is slow), and show it as the line still being spoken. Skipped
        while transcription is behind, so it never delays finished lines."""
        best = None
        for track in ("system", "mic"):
            path = self.meeting.path / f"{track}.pcm"
            cursor = self.cursors[track]
            available = audio.pcm_samples(path) - cursor
            if available < PARTIAL_MIN * SR or available > MAX_CHUNK + 2 * SR:
                continue  # nothing to show yet, or behind
            last = self.partials[track]
            if last and (time.monotonic() - last["at"] < max(PARTIAL_EVERY, 1.5 * last["took"])
                         or available - last["size"] < SR // 3):
                continue
            samples = audio.read_pcm(path, cursor, available)
            regions = audio.speech_regions(samples, min_gap=PAUSE, pad=0.0, min_len=0.15)
            if not regions or sum(b - a for a, b in regions) < PARTIAL_MIN:
                continue
            quiet_for = len(samples) / SR - regions[-1][1]
            if best is None or quiet_for < best[0]:
                best = (quiet_for, track, cursor, samples, regions)
        if not best:
            return False
        _, track, cursor, samples, regions = best
        skip = max(0, int((regions[0][0] - 0.3) * SR))
        began = time.monotonic()
        segs = transcribe.whisper(samples[skip:], self.cfg, self.prompt, live=True)
        shown = bool(self.partials[track] and self.partials[track]["shown"])
        self.partials[track] = {"at": time.monotonic(), "took": time.monotonic() - began, "size": len(samples),
                                "shown": shown}
        start = (cursor + skip) / SR
        line = {"track": track, "start": start, "end": (cursor + len(samples)) / SR,
                "text": " ".join(s["text"] for s in segs).strip()}
        if (not line["text"] or transcribe.is_thanks(line["text"])
                or track == "mic" and transcribe.is_echo(line, self.recent["system"])):
            self._partial_done(track)
            return True
        line["speaker"] = (self._who(line) if track == "system" else None) or self._label(track)
        self.partials[track]["shown"] = True
        self.on_event("partial", line)
        return True

    def _partial_done(self, track: str):
        """The line being spoken is finished (or turned out to be nothing): take it off the screen."""
        last = self.partials[track]
        self.partials[track] = None
        if last and last["shown"]:
            self.on_event("partial_done", {"track": track})

    def _label(self, track: str) -> str:
        if track == "system":
            return self.cfg["other_label"]
        return self.cfg["my_name"] if self.meeting.meta().get("system_audio", True) else transcribe.MIC_ONLY_LABEL

    def _who(self, line: dict) -> str | None:
        """Who Google Meet showed talking through this line, if anyone clearly."""
        try:
            return self.meet.refresh().who(line["start"], line["end"])
        except Exception:  # live names are a nicety
            return None

    def _voice(self, chunk: np.ndarray, seg: dict) -> str | None:
        """The name of a known person whose voice this line matches."""
        if not self.voices:
            return None
        try:
            vector = speakers.fingerprint(speakers.clip(chunk, seg["start"], seg["end"]))
            return speakers.recognize(vector, self.voices, speakers.KNOWN_LINE)[0]
        except Exception:  # live names are a nicety
            return None

    def _emit(self, track: str, seg: dict):
        seg["id"] = next(_ids)
        seg["track"] = track
        seg["speaker"] = (seg.get("speaker") if track == "system" else None) or self._label(track)
        seg["lag"] = round(time.time() - self.started - seg["end"], 1)  # seconds after the words were said
        # Echo handling: the mic can pick up the other side when you're on speakers.
        if track == "mic" and transcribe.is_echo(seg, self.recent["system"]):
            return
        # Repeated "Thank you." lines are Whisper filling silence: retract the ones shown and hide the rest.
        # They're still written to the file, where load_live drops them by the same rule.
        hidden = False
        if transcribe.is_thanks(seg["text"]):
            shown = self.thanks[track]
            if shown is None:  # already past the limit for this track
                hidden = True
            elif len(shown) + 1 >= transcribe.THANKS_LIMIT:
                for seg_id in shown:
                    self.on_event("segment_removed", {"id": seg_id})
                self.recent[track] = [s for s in self.recent[track] if s["id"] not in shown]
                self.thanks[track] = None
                hidden = True
            else:
                shown.append(seg["id"])
        if hidden:
            with open(self.meeting.live_path, "a") as f:
                f.write(json.dumps(seg) + "\n")
            return
        if track == "system":
            for mic_seg in list(self.recent["mic"]):
                if transcribe.is_echo(mic_seg, [seg]):
                    self.recent["mic"].remove(mic_seg)
                    self.on_event("segment_removed", {"id": mic_seg["id"]})
        self.recent[track] = [s for s in self.recent[track] if s["end"] > seg["start"] - 60] + [seg]
        with open(self.meeting.live_path, "a") as f:
            f.write(json.dumps(seg) + "\n")
        self.on_event("segment", seg)


def load_live(meeting: Meeting) -> list[dict]:
    """Live segments so far, minus any later retracted as echo (re-derived on load)."""
    try:
        lines = meeting.live_path.read_text().splitlines()
    except FileNotFoundError:
        return []
    segs = [json.loads(line) for line in lines if line.strip()]
    system = transcribe.drop_repeated_thanks([s for s in segs if s["track"] == "system"])
    mic = transcribe.drop_repeated_thanks([s for s in segs if s["track"] == "mic"])
    return sorted(system + [s for s in mic if not transcribe.is_echo(s, system)], key=lambda s: s["start"])

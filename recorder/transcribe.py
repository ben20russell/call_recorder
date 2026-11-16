"""Whisper transcription (mlx-whisper, runs on the Apple Silicon GPU) and transcript assembly.

The mic track is "me" and the system-audio track is everyone else, so speakers come for free.
When you're on speakers instead of headphones the mic also hears the other side; those echoed
mic segments are detected by text overlap with the system track and dropped.
"""

import json
import re
import threading
import time
import unicodedata
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np

from . import audio
from .meetings import Meeting

_model_lock = threading.Lock()
_live_waiting = threading.Event()  # a live transcription call wants the model: call processing lets it go first
_processing = threading.local()    # .active while this thread transcribes a finished call; .cancel stops it
ON_A_CALL = threading.Event()      # set while recording: processing an earlier call then goes at half speed


class Cancelled(Exception):
    """Processing a call was called off (to record more onto it)."""


def cancel_with(event: threading.Event | None) -> None:
    """While `event` is set, this thread's transcription of a finished call stops at its next 30-second window."""
    _processing.cancel = event

# Memory. MLX keeps GPU memory it's done with for reuse, by default up to most of the Mac's memory (it reached
# 17 GB over a few days of calls), so that's capped; the spare memory goes back after each call is processed,
# and the model itself (1.5 GB) is let go when nothing has been transcribed for a while.
CACHE_LIMIT = 512 * 1024 ** 2  # bytes of spare GPU memory MLX may keep
IDLE_UNLOAD = 300              # seconds without transcribing before the model is let go (reloading takes ~2 s)
_last_used = 0.0
_janitor: threading.Thread | None = None
MIC_ONLY_LABEL = "Mic"

# Things Whisper likes to "hear" in silence or noise.
HALLUCINATIONS = {
    "thanks for watching", "thank you for watching", "thanks for watching!", "please subscribe",
    "subtitles by the amara.org community", "you", "bye", "so", "uh", "um",
    "see you next time", "we'll see you next time", "see you in the next video", "i'll see you in the next video",
    "see you in the next one", "don't forget to like and subscribe", "like and subscribe", "bye-bye",
}
HALLUCINATION_PREFIXES = ("subtitles by", "subtitled by", "transcribed by", "translated by")
# Whisper's favourite line over a quiet mic. A real one is rare, so once a track has a few, they're all noise.
THANKS = {"thank you", "thanks", "thank you very much", "thank you so much", "thanks so much"}
THANKS_LIMIT = 3

# Languages written in Latin script: in these, a segment that is partly Cyrillic, CJK, Hangul… is noise.
LATIN_LANGUAGES = {"en", "es", "fr", "de", "it", "pt", "nl", "sv", "da", "no", "nb", "fi", "pl", "cs", "sk", "sl",
                   "ro", "hu", "hr", "tr", "id", "ms", "vi", "ca", "et", "lv", "lt", "tl", "sw", "af", "cy", "ga"}


def whisper(samples: np.ndarray, cfg: dict, prompt: str = "",
            clips: list[tuple[float, float]] | None = None, live: bool = False) -> list[dict]:
    """Transcribe float32 16 kHz audio. `clips` limits decoding to those (start, end) regions.
    Live calls decode once (no retries at higher temperatures) and go ahead of call processing, which
    hands them the model between its 30-second windows, so a live transcript never waits long."""
    import mlx_whisper
    from mlx_whisper import whisper as mlx_model

    global _last_used
    _setup_mlx()
    if not getattr(mlx_model.Whisper.decode, "yields_to_live", False):
        mlx_model.Whisper.decode = _yielding(mlx_model.Whisper.decode)
    kwargs = {"temperature": 0.0} if live else {}
    if clips:
        kwargs["clip_timestamps"] = [t for clip in clips for t in clip]
    if cfg.get("language"):
        kwargs["language"] = cfg["language"]
    _last_used = time.monotonic()
    if live:
        _live_waiting.set()
    else:
        while _live_waiting.is_set():
            time.sleep(0.01)
    with _model_lock:
        _live_waiting.clear()
        _processing.active = not live
        try:
            result = mlx_whisper.transcribe(
                samples,
                path_or_hf_repo=cfg["whisper_model"],
                initial_prompt=prompt or None,
                condition_on_previous_text=False,
                verbose=None,
                **kwargs,
            )
        finally:
            _processing.active = False
            _last_used = time.monotonic()
            if not live:  # a finished call's track: what it needed is spare now
                import mlx.core as mx
                mx.clear_cache()
    prompt_words = set(_words(prompt))
    segments = []
    for seg in result.get("segments", []):
        text = seg["text"].strip()
        if not text or noise_reason(text, seg, prompt_words, cfg.get("language") or ""):
            continue
        segments.append({"start": float(seg["start"]), "end": float(seg["end"]), "text": text})
    return refine_times(segments, samples)


def _yielding(decode):
    """Whisper's decode, which runs once per 30-second window: while a finished call is being transcribed, a
    live transcription that's waiting gets the model first, and while you're on another call it rests as long
    as each window took, so it doesn't compete with the call. The windows themselves are untouched."""
    def wrapper(model, *args, **kwargs):
        global _last_used
        _last_used = time.monotonic()
        processing = getattr(_processing, "active", False)
        cancel = getattr(_processing, "cancel", None)
        if processing and cancel is not None and cancel.is_set():
            raise Cancelled()  # whisper()'s `with _model_lock` lets go of the model
        if processing and _live_waiting.is_set():
            _model_lock.release()
            while _live_waiting.is_set():  # until the live call has the model…
                time.sleep(0.01)
            _model_lock.acquire()          # …and is done with it
        began = time.monotonic()
        result = decode(model, *args, **kwargs)
        if processing and ON_A_CALL.is_set():
            _model_lock.release()  # live transcription can have the model meanwhile
            time.sleep(min(time.monotonic() - began, 3))
            _model_lock.acquire()
        return result
    wrapper.yields_to_live = True
    return wrapper


def _setup_mlx():
    """Cap MLX's spare GPU memory, and start letting go of the model when it's idle (once)."""
    global _janitor
    if _janitor:
        return
    import mlx.core as mx
    mx.set_cache_limit(CACHE_LIMIT)
    _janitor = threading.Thread(target=_unload_when_idle, name="whisper-unload", daemon=True)
    _janitor.start()


def _unload_when_idle():
    from mlx_whisper.transcribe import ModelHolder
    while True:
        time.sleep(30)
        if ModelHolder.model is None or time.monotonic() - _last_used < IDLE_UNLOAD:
            continue
        with _model_lock:
            if ModelHolder.model is not None and time.monotonic() - _last_used >= IDLE_UNLOAD:
                release_model()


def release_model():
    """Let go of the Whisper model and MLX's spare GPU memory. Call with _model_lock held."""
    import gc
    import mlx.core as mx
    from mlx_whisper.transcribe import ModelHolder
    ModelHolder.model = ModelHolder.model_path = None
    gc.collect()
    mx.clear_cache()


def refine_times(segments: list[dict], samples: np.ndarray) -> list[dict]:
    """Whisper's segment starts tend to begin where the previous segment ended, swallowing the
    silence in between. Snap each segment to the first/last voiced frame inside it; drop segments
    with no audio energy under them at all (hallucinations)."""
    rms = audio.frame_rms(samples)
    if not len(rms):
        return []
    voiced = rms > audio.speech_threshold(rms)
    step = audio.FRAME / audio.SAMPLE_RATE
    out = []
    for seg in segments:
        a, b = int(seg["start"] / step), min(len(voiced), int(np.ceil(seg["end"] / step)) + 1)
        hits = np.flatnonzero(voiced[a:b])
        if not len(hits):
            continue
        start = max(seg["start"], (a + hits[0]) * step - 0.15)
        end = min(seg["end"], (a + hits[-1] + 1) * step + 0.15)
        out.append({"start": round(start, 2), "end": round(max(end, start + 0.2), 2), "text": seg["text"]})
    return out


def noise_reason(text: str, seg: dict, prompt_words: set[str] = frozenset(), language: str = "") -> str:
    """Why a Whisper segment looks like something it made up from silence or noise ("" if it looks real)."""
    normalized = text.lower().strip(" .!?,")
    if normalized in HALLUCINATIONS or normalized.startswith(HALLUCINATION_PREFIXES):
        return "stock phrase"
    no_speech, logprob = seg.get("no_speech_prob", 0), seg.get("avg_logprob", 0)
    if no_speech > 0.6 and logprob < -0.8:
        return "no speech"
    if normalized in {"thank you", "thanks"} and no_speech > 0.3:
        return "stock phrase"
    if logprob < -1.0:
        return "low confidence"
    if seg.get("compression_ratio", 0) > 2.4:
        return "repetition"
    if "\ufffd" in text:
        return "garbled"
    if language in LATIN_LANGUAGES:
        letters = [c for c in text if c.isalpha()]
        foreign = sum(1 for c in letters if not unicodedata.name(c, "").startswith("LATIN"))
        if letters and foreign / len(letters) > 0.1:
            return "wrong script"
    # Over silence Whisper tends to repeat its prompt (the names on the invite).
    words = _words(text)
    if prompt_words and len(words) >= 2:
        matched = sum(1 for w in words if w in prompt_words)
        if matched >= 2 and matched / len(words) >= 0.7:
            return "repeats the prompt"
    return ""


def is_thanks(text: str) -> bool:
    return " ".join(_words(text)) in THANKS


def drop_repeated_thanks(segments: list[dict]) -> list[dict]:
    """Drop standalone "Thank you." lines from a track that has THANKS_LIMIT or more of them."""
    if sum(1 for s in segments if is_thanks(s["text"])) < THANKS_LIMIT:
        return segments
    return [s for s in segments if not is_thanks(s["text"])]


def vocabulary_prompt(meta: dict, cfg: dict) -> str:
    """Seed Whisper with the names it should spell right: the people on the invite and the meeting title.
    Plain words only. Whisper sometimes repeats its prompt over silence, and labels or email addresses
    make those repeats look like speech."""
    from .calendar_src import display_name
    names = [cfg.get("my_name", "")] + [display_name(a) for a in meta.get("attendees") or []]
    names = [n for n in dict.fromkeys(names) if n and n != "Me"]
    title = re.sub(r"^\s*((fwd?|re)\s*:\s*)+", "", meta.get("title") or "", flags=re.I).strip()
    return ", ".join(names + ([title] if title else [])) + "." if names or title else ""


# -- assembling a transcript ---------------------------------------------------------------------

def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


def is_echo(mic_seg: dict, system_segs: list[dict], tolerance: float = 2.0) -> bool:
    """True when a mic segment is just the other side leaking through the speakers."""
    nearby = [s for s in system_segs
              if s["start"] - tolerance <= mic_seg["end"] and s["end"] + tolerance >= mic_seg["start"]]
    if not nearby:
        return False
    mic_words = _words(mic_seg["text"])
    if not mic_words:
        return True
    sys_words = _words(" ".join(s["text"] for s in nearby))
    matcher = SequenceMatcher(None, mic_words, sys_words, autojunk=False)
    matched = sum(block.size for block in matcher.get_matching_blocks())
    return matched / len(mic_words) >= 0.6


def merge_tracks(mic: list[dict], system: list[dict], me: str, them: str) -> list[dict]:
    mic = [s for s in mic if not is_echo(s, system)]
    combined = ([{**s, "speaker": me, "track": "mic"} for s in mic]
                + [{**s, "speaker": them, "track": "system"} for s in system])
    return sorted(combined, key=lambda s: s["start"])


def transcribe_meeting(meeting: Meeting, cfg: dict, progress=lambda msg: None) -> list[dict]:
    meta = meeting.meta()
    prompt = vocabulary_prompt(meta, cfg)
    results = {}
    for name in ("system", "mic"):
        samples = audio.load_track(meeting.path, name)
        if samples is None or not len(samples):
            results[name] = []
            continue
        regions = audio.speech_regions(samples)
        speech = sum(b - a for a, b in regions)
        label = "their side" if name == "system" else "your side"
        progress(f"Transcribing {label} ({speech / 60:.1f} min of speech)…")
        results[name] = drop_repeated_thanks(whisper(samples, cfg, prompt, clips=regions)) if regions else []

    # Without a system-audio track the mic may carry both sides (speakers), so don't claim it's all you.
    me = cfg["my_name"] if meta.get("system_audio", True) else MIC_ONLY_LABEL
    segments = merge_tracks(results["mic"], results["system"], me, cfg["other_label"])
    meeting.transcript_path.write_text(json.dumps(segments, indent=1))
    (meeting.path / "transcript.md").write_text(render_transcript(segments))
    return segments


def ts(seconds: float) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def paragraphs(segments: list[dict], max_gap: float = 2.5) -> list[dict]:
    """Merge consecutive segments from the same speaker into paragraphs."""
    out: list[dict] = []
    for seg in segments:
        if out and out[-1]["speaker"] == seg["speaker"] and seg["start"] - out[-1]["end"] <= max_gap:
            out[-1]["text"] += " " + seg["text"]
            out[-1]["end"] = max(out[-1]["end"], seg["end"])
        else:
            out.append(dict(seg))
    return out


def when(p: dict) -> str:
    """A line's time; "~" marks an estimate (imported transcripts without timestamps)."""
    return ("~" if p.get("approx") else "") + ts(p["start"])


def render_transcript(segments: list[dict]) -> str:
    return "\n\n".join(f"[{when(p)}] " + (f"**{p['speaker']}:** " if p["speaker"] else "") + p["text"]
                       for p in paragraphs(segments))


def plain_transcript(segments: list[dict]) -> str:
    return "\n".join(f"[{when(p)}] " + (f"{p['speaker']}: " if p["speaker"] else "") + p["text"]
                     for p in paragraphs(segments))

"""Who said what: tells the voices on a call apart and names them.

After a call, every transcript line from the other side (the system-audio track, or the microphone when
that's all there is) gets a voice fingerprint from an on-device speaker model, and similar fingerprints are
grouped into speakers. A speaker whose voice matches someone from earlier calls gets their name; the others
are "Speaker 1", "Speaker 2"… until Claude names them from the invite list and the conversation (see
summarize.py), or you do in the app. Named voices are saved in a voice library (~/.callrec/voices.json), so
people you talk to regularly are recognized on later calls, and live while recording.

On Google Meet calls, the Call Recorder extension for Chrome reports whose tile showed them talking (see
meet.py). That names voices directly: a voice is whoever Meet showed talking through most of its lines, and
a line Meet clearly gives to someone else moves to them. Other apps' participant lists and highlights can't
be read without Screen Recording or Accessibility access, so there the invite list stands in for who's there.
"""

import json
import re
import threading
import urllib.request
from datetime import datetime
from pathlib import Path

import numpy as np

from . import audio, config, meet, transcribe

SR = audio.SAMPLE_RATE
MODEL_NAME = "3dspeaker_speech_eres2net_sv_en_voxceleb_16k.onnx"
MODEL_URL = "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/" + MODEL_NAME

# Tuned on real calls: the same person on different calls scores ~0.8 (single lines ~0.65); different people
# are usually below ~0.45 (single lines below ~0.35), but two similar voices reached 0.62.
GROUP_DISTANCE = 0.65  # cosine distance up to which groups of lines are merged into one voice
MIN_LINE = 1.0         # seconds; shorter lines are fingerprinted with a little surrounding audio
MIN_VOICE = 6.0        # seconds; a "voice" with less speech than this is usually a fragment of another
KNOWN_VOICE = 0.68     # similarity to a saved voice that names a whole speaker
STRANGER_VOICE = 0.78  # …when that person isn't on the invite
HINT_VOICE = 0.6       # below the bar but close: Claude is told who it sounds like
KNOWN_LINE = 0.6       # a single line while recording (a wrong name is worse than "Them")
MAX_SAMPLES = 12       # saved samples per person (the ones you named are kept first)
MEET_VOICE = 4.0       # seconds of a voice's speech Meet must attribute to one person to name the voice
MEET_SHARE = 0.6       # …and that person's share of what Meet attributed in the voice
MEET_LINE = 1.5        # seconds of speech a line needs before Meet alone can move it to another voice

_model_lock = threading.Lock()
_library_lock = threading.Lock()
_extractor = None


# -- fingerprints --------------------------------------------------------------------------------------

def _model_path() -> Path:
    path = config.CONFIG_DIR / "models" / MODEL_NAME
    if not path.exists():  # first use: ~26 MB from the sherpa-onnx releases
        path.parent.mkdir(parents=True, exist_ok=True)
        part = path.with_suffix(".part")
        urllib.request.urlretrieve(MODEL_URL, part)
        part.replace(path)
    return path


def fingerprint(samples: np.ndarray) -> np.ndarray:
    """A unit-length voice embedding for some 16 kHz audio."""
    global _extractor
    with _model_lock:  # one model for the whole app; live transcription and processing can overlap
        if _extractor is None:
            import sherpa_onnx
            _extractor = sherpa_onnx.SpeakerEmbeddingExtractor(
                sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(_model_path()), num_threads=2))
        stream = _extractor.create_stream()
        stream.accept_waveform(SR, np.ascontiguousarray(samples, dtype=np.float32))
        stream.input_finished()
        vector = np.array(_extractor.compute(stream), dtype=np.float32)
    return _unit(vector)


def _unit(v: np.ndarray) -> np.ndarray:
    return v / (np.linalg.norm(v) or 1.0)


def clip(samples: np.ndarray, start: float, end: float) -> np.ndarray:
    """The audio of a line, widened to at least MIN_LINE seconds."""
    mid, half = (start + end) / 2, max(end - start, MIN_LINE) / 2
    return samples[int(max(0.0, mid - half) * SR): int(min(len(samples) / SR, mid + half) * SR)]


def voiced(samples: np.ndarray, regions: list, start: float, end: float) -> tuple[np.ndarray, float]:
    """Just the voiced part of a line (Whisper's timings often include seconds of background noise around
    a "Yeah."), widened to MIN_LINE when there's less, and how many seconds of speech it holds."""
    parts = [(max(a, start), min(b, end)) for a, b in regions if b > start and a < end]
    speech = sum(b - a for a, b in parts)
    if speech >= MIN_LINE:
        return np.concatenate([samples[int(a * SR): int(b * SR)] for a, b in parts]), speech
    mid = (parts[0][0] + parts[-1][1]) / 2 if parts else (start + end) / 2
    return clip(samples, mid, mid), speech


def speech_regions(samples: np.ndarray) -> list:
    return audio.speech_regions(samples, min_gap=0.3, pad=0.15, min_len=0.15)


# -- voice library -------------------------------------------------------------------------------------
# {name: [{"call", "speaker", "source": you|claude|voice|mic, "seconds", "at", "embedding"}]}

def _library_path() -> Path:
    return config.CONFIG_DIR / "voices.json"


def load_library() -> dict:
    try:
        return json.loads(_library_path().read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_library(library: dict) -> None:
    path = _library_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(library))
    tmp.replace(path)


def known_voices(library: dict | None = None) -> dict[str, np.ndarray]:
    """One averaged fingerprint per saved person; longer samples count for more."""
    out = {}
    for name, samples in (load_library() if library is None else library).items():
        if samples:
            weights = np.sqrt([max(s["seconds"], 1.0) for s in samples])[:, None]
            out[name] = _unit((np.array([s["embedding"] for s in samples]) * weights).sum(0))
    return out


def recognize(vector: np.ndarray, voices: dict[str, np.ndarray], threshold: float,
              exclude: tuple = ()) -> tuple[str | None, float]:
    """The saved person this voice belongs to, if it clearly matches one."""
    scores = sorted(((float(vector @ v), name) for name, v in voices.items() if name not in exclude), reverse=True)
    if not scores:
        return None, 0.0
    best, name = scores[0]
    runner_up = scores[1][0] if len(scores) > 1 else 0.0
    return (name if best >= threshold and best - runner_up >= 0.08 else None), best


def remember(name: str, call_id: str, speaker_id: str, vector: np.ndarray, seconds: float, source: str) -> None:
    with _library_lock:
        library = _without(load_library(), call_id, speaker_id)
        library.setdefault(name, []).append({
            "call": call_id, "speaker": speaker_id, "source": source, "seconds": round(seconds, 1),
            "at": datetime.now().astimezone().isoformat(), "embedding": [round(float(x), 5) for x in vector]})
        library[name] = sorted(library[name], key=lambda s: (s["source"] == "you", s["at"]))[-MAX_SAMPLES:]
        _save_library(library)


def forget(call_id: str, speaker_id: str) -> None:
    with _library_lock:
        _save_library(_without(load_library(), call_id, speaker_id))


def _without(library: dict, call_id: str, speaker_id: str) -> dict:
    trimmed = {name: [s for s in samples if not (s["call"] == call_id and s["speaker"] == speaker_id)]
               for name, samples in library.items()}
    return {name: samples for name, samples in trimmed.items() if samples}


# -- after a call ----------------------------------------------------------------------------------------

def _track_of(seg: dict, cfg: dict) -> str:
    if seg.get("track"):
        return seg["track"]
    return "mic" if seg["speaker"] in (cfg["my_name"], transcribe.MIC_ONLY_LABEL) else "system"


def _invited(name: str, attendees: list[dict]) -> bool:
    """Whether someone with this first name is on the invite (by name or email address)."""
    first = name.lower().split()[0]
    return any(first in re.split(r"[\s._@+-]+", f"{a.get('name') or ''} {a.get('email') or ''}".lower())
               for a in attendees)


def _group(vectors: np.ndarray) -> np.ndarray:
    if len(vectors) < 2:
        return np.zeros(len(vectors), dtype=int)
    from scipy.cluster.hierarchy import fcluster, linkage
    return fcluster(linkage(vectors, method="average", metric="cosine"), t=GROUP_DISTANCE, criterion="distance") - 1


def _meet_lines(meeting, segments: list[dict], lines: list[int], speech: list, length: float,
                cfg: dict) -> tuple[list, dict | None]:
    """Who Google Meet showed talking during each of these lines (None where it isn't clear), and how well
    Meet's talking lights matched the audio. All None when Meet wasn't watched or didn't match."""
    timeline = meet.load(meeting.path, cfg["my_name"])
    if not timeline:
        return [None] * len(lines), None
    lag, checked = meet.check(timeline, speech, length)
    print(f"{datetime.now().astimezone():%Y-%m-%d %H:%M:%S %Z} Meet names for {meeting.id}: {checked}", flush=True)
    if lag is None:
        return [None] * len(lines), checked
    return [timeline.who(segments[i]["start"], segments[i]["end"], lag) for i in lines], checked


def _meet_voices(groups: np.ndarray, voices: list, told: list, seconds: np.ndarray,
                 vectors: np.ndarray) -> tuple[np.ndarray, list, dict]:
    """Names from Meet: a voice is whoever Meet showed talking through most of its lines (when that's clear),
    voices given the same name are one person split in two, and a line Meet clearly gives to someone else
    moves to them, which splits two similar voices that were grouped as one. Then the lines Meet didn't settle
    go to the closest voice again, now that each named voice sounds like just its person. Returns the
    regrouped lines, the voices, and {voice: name}."""
    groups, named = groups.copy(), {}
    for v in voices:
        tally = {}
        for i in np.flatnonzero(groups == v):
            if told[i]:
                tally[told[i]] = tally.get(told[i], 0.0) + seconds[i]
        if tally:
            name = max(tally, key=tally.get)
            if tally[name] >= MEET_VOICE and tally[name] >= MEET_SHARE * sum(tally.values()):
                named[v] = name
    home = {}  # name → its voice (the one with the most speech, when Meet gave two voices the same name)
    for v in sorted(named, key=lambda v: -seconds[groups == v].sum()):
        home.setdefault(named[v], v)
        groups[groups == v] = home[named[v]]
    spare = max(voices) + 1
    for i, name in enumerate(told):
        if name and seconds[i] >= MEET_LINE and name != next((n for n, v in home.items() if v == groups[i]), None):
            if name not in home:
                home[name], spare = spare, spare + 1
            groups[i] = home[name]
    voices = [v for v in dict.fromkeys([*voices, *home.values()]) if (groups == v).any()]
    named = {v: name for name, v in home.items()}
    settled = np.array([bool(t) and named.get(g) == t for t, g in zip(told, groups)])
    if settled.any() and not settled.all():
        heard = lambda v: (groups == v) & settled if ((groups == v) & settled).any() else groups == v
        centers = np.array([_unit(vectors[heard(v)].mean(0)) for v in voices])
        groups[~settled] = np.array(voices)[np.argmax(vectors[~settled] @ centers.T, axis=1)]
        voices = [v for v in voices if (groups == v).any()]
    return groups, voices, named


def identify(meeting, segments: list[dict], cfg: dict) -> tuple[list[dict], dict]:
    """Label the other side's lines by voice ("Speaker 1"…, or a recognized name). Returns the relabeled
    segments and the speaker table (saved later as speakers.json). Your microphone's lines stay yours,
    unless the microphone is all there is, in which case it's sorted out like the other side."""
    me = cfg["my_name"]
    meta = meeting.meta()
    attendees = meta.get("attendees") or []
    track = "mic" if meta.get("system_audio") is False else "system"
    samples = audio.load_track(meeting.path, track)
    lines = [i for i, s in enumerate(segments) if _track_of(s, cfg) == track]
    if samples is None or not lines:
        return segments, {}
    if track == "system":
        _learn_my_voice(meeting, segments, cfg)

    regions = speech_regions(samples)
    told, checked = _meet_lines(meeting, segments, lines, regions, len(samples) / SR, cfg) if track == "system" \
        else ([None] * len(lines), None)
    heard = [voiced(samples, regions, segments[i]["start"], segments[i]["end"]) for i in lines]
    vectors = np.array([fingerprint(part) for part, _ in heard])
    seconds = np.array([speech for _, speech in heard])
    # Only lines with real speech form voices; "Yeah."s just join the closest one.
    solid = (seconds >= MIN_LINE) & np.array([len(segments[i]["text"].split()) >= 3 for i in lines])
    groups = np.full(len(lines), -1)
    groups[solid] = _group(vectors[solid])
    if not solid.any():
        groups[:] = 0
    # Fold fragments into the closest real voice, then give every line (short ones too) its closest voice.
    totals = {g: seconds[groups == g].sum() for g in set(groups) - {-1}}
    centers = {g: _unit(vectors[groups == g].mean(0)) for g in totals}
    voices = [g for g in totals if totals[g] >= MIN_VOICE] or [max(totals, key=totals.get)]
    for g in set(totals) - set(voices):
        closest = max(voices, key=lambda v: float(centers[g] @ centers[v]))
        if float(centers[g] @ centers[closest]) >= 0.35 or totals[g] < 3:
            groups[groups == g] = closest
        else:
            voices.append(g)  # a distinct voice that just didn't say much
    matrix = np.array([centers[v] for v in voices])
    groups = np.array(voices)[np.argmax(vectors @ matrix.T, axis=1)]
    named = {}
    if any(told):
        groups, voices, named = _meet_voices(groups, voices, told, seconds, vectors)

    # Number voices in order of first appearance; name them from Meet, or the ones the library recognizes.
    library = known_voices()
    table, ids = [], {}
    for k in np.argsort([segments[lines[np.flatnonzero(groups == v)[0]]]["start"] for v in voices]):
        v = voices[k]
        members = groups == v
        center = _unit(vectors[members].mean(0))
        closest, score = recognize(center, library, 0.0, exclude=() if track == "mic" else (me,))
        expected = closest == me or (closest and _invited(closest, attendees))
        share = None
        if v in named:
            name, source = _known_as(named[v]), "meet"
            share = sum(seconds[i] for i in np.flatnonzero(members) if told[i] == named[v]) / seconds[members].sum()
        else:
            name = closest if score >= (KNOWN_VOICE if expected else STRANGER_VOICE) else None
            name = None if name in named.values() else name  # Meet already found that person elsewhere
            source = "voice" if name else ""
        sid = f"S{len(table) + 1}"
        ids[v] = sid
        table.append({"id": sid, "label": f"Speaker {len(table) + 1}", "name": name,
                      "source": source, "score": round(score, 3),
                      "meet": None if share is None else round(float(share), 2),  # Meet's share of its speech
                      # close, but not enough to be sure: left for Claude to weigh
                      "sounds_like": closest if not name and score >= HINT_VOICE else None,
                      "seconds": round(float(seconds[members].sum()), 1),
                      "first": segments[lines[np.flatnonzero(members)[0]]]["start"],
                      "embedding": [round(float(x), 5) for x in center]})
    out = [dict(s) for s in segments]
    for i, s in enumerate(out):
        s["track"] = _track_of(s, cfg)
        if s["track"] != track:
            s["speaker_id"] = "me"
    by_id = {sp["id"]: sp for sp in table}
    for i, g in zip(lines, groups):
        sp = by_id[ids[g]]
        out[i]["speaker_id"] = sp["id"]
        out[i]["speaker"] = sp["name"] or sp["label"]
    return out, {"track": track, "speakers": table, **({"meet": checked} if checked else {})}


def _learn_my_voice(meeting, segments: list[dict], cfg: dict) -> None:
    """Your microphone is you, so each call refreshes your own voice sample (used for mic-only calls)."""
    mine = sorted((s for s in segments if _track_of(s, cfg) == "mic" and len(s["text"].split()) >= 3),
                  key=lambda s: s["start"] - s["end"])[:40]
    mic = audio.load_track(meeting.path, "mic") if mine else None
    if mic is None or not mine:
        return
    regions = speech_regions(mic)
    heard = [h for h in (voiced(mic, regions, s["start"], s["end"]) for s in mine) if h[1] >= 1.5]
    if heard:
        vector = _unit(np.mean([fingerprint(part) for part, _ in heard], axis=0))
        remember(cfg["my_name"], meeting.id, "me", vector, sum(speech for _, speech in heard), "mic")


def apply_names(meeting, segments: list[dict], table: dict, suggestions: list[dict], cfg: dict) -> list[dict]:
    """Take Claude's names for the unrecognized voices (when it's at least fairly sure), save the speaker
    table, the relabeled transcript, and teach the voice library the voices that are now named."""
    if not table:
        return segments
    by_label = {sp["label"]: sp for sp in table["speakers"]}
    for item in suggestions or []:
        sp = by_label.get((item.get("label") or "").strip())
        name = (item.get("name") or "").strip()
        if sp and not sp["name"] and name and item.get("confidence") in ("high", "medium"):
            sp.update(name=_known_as(name), source="claude", confidence=item["confidence"])
    segments = _relabel(segments, table)
    save(meeting, segments, table)
    for sp in table["speakers"]:
        if sp["name"] and (sp["source"] in ("voice", "meet") or sp.get("confidence") == "high"):
            remember(sp["name"], meeting.id, sp["id"], np.array(sp["embedding"]), sp["seconds"], sp["source"])
    return segments


def _known_as(name: str) -> str:
    """One name per person in the library: "Ashley" and "Ashley Richardson-George" are the same person
    (when nobody else in the library shares that first name), filed under the fuller name."""
    words = name.lower().split()
    with _library_lock:
        library = load_library()
        same = [k for k in library if (lambda w: w[: len(words)] == words or words[: len(w)] == w)(k.lower().split())]
        if len(same) != 1 or same[0] == name:
            return name
        full = max(same[0], name, key=len)
        if full != same[0]:  # the library had the short form: move its samples under the full name
            library[full] = library.pop(same[0])
            _save_library(library)
        return full


def _relabel(segments: list[dict], table: dict) -> list[dict]:
    by_id = {sp["id"]: sp for sp in table["speakers"]}
    for s in segments:
        sp = by_id.get(s.get("speaker_id"))
        if sp:
            s["speaker"] = sp["name"] or sp["label"]
    return segments


def _speakers_path(meeting) -> Path:
    return meeting.path / "speakers.json"


def load(meeting) -> dict:
    try:
        return json.loads(_speakers_path(meeting).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save(meeting, segments: list[dict], table: dict) -> None:
    _speakers_path(meeting).write_text(json.dumps(table, indent=1))
    meeting.transcript_path.write_text(json.dumps(segments, indent=1))
    (meeting.path / "transcript.md").write_text(transcribe.render_transcript(segments))


def rename(meeting, speaker_id: str, name: str, cfg: dict) -> dict:
    """You named (or un-named, with "") a voice: relabel the call, fix "Speaker N" in its notes, and teach
    the voice library."""
    table = load(meeting)
    sp = next((s for s in table.get("speakers", []) if s["id"] == speaker_id), None)
    if not sp:
        raise KeyError(speaker_id)
    old = sp["name"] or sp["label"]
    name = name.strip()
    sp.update(name=name or None, source="you" if name else "", confidence="you" if name else None)
    segments = _relabel(meeting.transcript(), table)
    save(meeting, segments, table)
    forget(meeting.id, speaker_id)
    if name:
        remember(name, meeting.id, speaker_id, np.array(sp["embedding"]), sp["seconds"], "you")
    if old == sp["label"] and name:  # the notes can only have said "Speaker N" if nobody had named it yet
        _rename_in_notes(meeting, old, name, segments, cfg)
    return table


def _rename_in_notes(meeting, old: str, new: str, segments: list[dict], cfg: dict) -> None:
    import re

    from .pipeline import render_notes

    summary = meeting.summary()
    if not summary:
        return
    pattern = re.compile(rf"\b{re.escape(old)}\b(?!\d)")
    swap = lambda v: pattern.sub(new, v) if isinstance(v, str) else (
        [swap(x) for x in v] if isinstance(v, list) else {k: swap(x) for k, x in v.items()} if isinstance(v, dict) else v)
    summary = {k: (swap(v) if k != "speakers" else v) for k, v in summary.items()}
    meeting.summary_path.write_text(json.dumps(summary, indent=2))
    meeting.notes_path.write_text(render_notes(meeting.meta(), summary, meeting.my_notes(), segments))

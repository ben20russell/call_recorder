"""Raw PCM helpers: reading tracks, a simple energy VAD, and encoding to .m4a with afconvert."""

import subprocess
import tempfile
import wave
from pathlib import Path

import numpy as np

SAMPLE_RATE = 16_000
FRAME = 480  # 30 ms


def read_pcm(path: Path, start: int = 0, count: int = -1) -> np.ndarray:
    """Read 16 kHz mono s16le samples [start, start+count) as float32 in [-1, 1]."""
    with open(path, "rb") as f:
        f.seek(start * 2)
        data = f.read(-1 if count < 0 else count * 2)
    data = data[: len(data) // 2 * 2]
    return np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0


def pcm_samples(path: Path) -> int:
    return path.stat().st_size // 2 if path.exists() else 0


def load_track(meeting_dir: Path, name: str) -> np.ndarray | None:
    """Load a track from its raw .pcm (during/just after recording) or its archived .m4a."""
    pcm = meeting_dir / f"{name}.pcm"
    if pcm.exists():
        return read_pcm(pcm)
    m4a = meeting_dir / f"{name}.m4a"
    if m4a.exists():
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "track.wav"
            subprocess.run(["afconvert", "-f", "WAVE", "-d", f"LEI16@{SAMPLE_RATE}", "-c", "1", str(m4a), str(wav)],
                           check=True, capture_output=True)
            with wave.open(str(wav)) as w:
                return np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768.0
    return None


def decode_to_pcm(m4a: Path, pcm: Path) -> None:
    """Turn a saved .m4a track back into a raw 16 kHz .pcm one (to record more onto a finished call)."""
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "track.wav"
        subprocess.run(["afconvert", "-f", "WAVE", "-d", f"LEI16@{SAMPLE_RATE}", "-c", "1", str(m4a), str(wav)],
                       check=True, capture_output=True)
        part = pcm.with_name(pcm.name + ".tmp")
        with wave.open(str(wav)) as w, open(part, "wb") as out:
            while frames := w.readframes(SAMPLE_RATE * 60):
                out.write(frames)
        part.replace(pcm)


def frame_rms(audio: np.ndarray) -> np.ndarray:
    n = len(audio) // FRAME
    if n == 0:
        return np.zeros(0, dtype=np.float32)
    frames = audio[: n * FRAME].reshape(n, FRAME)
    return np.sqrt((frames ** 2).mean(axis=1))


def speech_threshold(rms: np.ndarray) -> float:
    """Adaptive energy threshold: well above the noise floor, never below ~-48 dBFS."""
    if len(rms) == 0:
        return 0.004
    return max(0.004, float(np.percentile(rms, 20)) * 3)


def speech_regions(audio: np.ndarray, min_gap: float = 1.5, pad: float = 0.4,
                   min_len: float = 0.25) -> list[tuple[float, float]]:
    """Energy-based speech regions in seconds. Good enough to skip long silences before Whisper,
    which both saves time and avoids Whisper's habit of hallucinating text over silence."""
    rms = frame_rms(audio)
    if len(rms) == 0:
        return []
    voiced = rms > speech_threshold(rms)
    step = FRAME / SAMPLE_RATE
    regions: list[list[float]] = []
    start = None
    for i, v in enumerate(voiced):
        if v and start is None:
            start = i
        elif not v and start is not None:
            regions.append([start * step, i * step])
            start = None
    if start is not None:
        regions.append([start * step, len(voiced) * step])

    merged: list[list[float]] = []
    for r in regions:
        if merged and r[0] - merged[-1][1] < min_gap:
            merged[-1][1] = r[1]
        else:
            merged.append(r)
    total = len(audio) / SAMPLE_RATE
    return [(max(0.0, a - pad), min(total, b + pad)) for a, b in merged if b - a >= min_len]


def has_speech(audio: np.ndarray) -> bool:
    return bool(speech_regions(audio, min_len=0.3))


def encode_m4a(samples: np.ndarray, out: Path, bitrate: int = 32_000, channels: int = 1) -> None:
    """Encode float samples (mono, or interleaved stereo when channels=2) to AAC with afconvert.
    AAC at 16 kHz mono tops out at 48 kbps."""
    pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2")
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "in.wav"
        with wave.open(str(wav), "wb") as w:
            w.setnchannels(channels)
            w.setsampwidth(2)
            w.setframerate(SAMPLE_RATE)
            w.writeframes(pcm.tobytes())
        result = subprocess.run(["afconvert", "-f", "m4af", "-d", "aac", "-b", str(bitrate), str(wav), str(out)],
                                capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"afconvert failed for {out.name}: {result.stderr.strip() or result.stdout.strip()}")


def archive(meeting_dir: Path, keep_raw: bool = False) -> dict:
    """Write call.m4a (both sides mixed, for listening) plus mic.m4a / system.m4a (separate sides,
    for re-transcription). Raw .pcm files are removed afterwards unless keep_raw is set."""
    tracks = {name: load_track(meeting_dir, name) for name in ("mic", "system")}
    tracks = {k: v for k, v in tracks.items() if v is not None and len(v)}
    for name in ("mic", "system"):  # a source that never delivered audio leaves an empty file
        pcm = meeting_dir / f"{name}.pcm"
        if name not in tracks and pcm.exists() and pcm.stat().st_size == 0 and not keep_raw:
            pcm.unlink()
    if not tracks:
        return {"duration": 0.0, "tracks": []}
    length = max(len(v) for v in tracks.values())
    mix = np.zeros(length, dtype=np.float32)
    for name, samples in tracks.items():
        mix[: len(samples)] += samples
        if not (meeting_dir / f"{name}.m4a").exists():
            encode_m4a(samples, meeting_dir / f"{name}.m4a")
    peak = float(np.abs(mix).max()) if length else 0
    if peak > 0.99:
        mix *= 0.99 / peak
    encode_m4a(mix, meeting_dir / "call.m4a", bitrate=48_000)
    if not keep_raw:
        for name in tracks:
            pcm, m4a = meeting_dir / f"{name}.pcm", meeting_dir / f"{name}.m4a"
            if pcm.exists() and m4a.exists() and m4a.stat().st_size > 0:
                pcm.unlink()
    return {"duration": length / SAMPLE_RATE, "tracks": sorted(tracks)}

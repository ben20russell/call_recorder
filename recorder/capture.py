"""Starts/stops audio capture. The real thing is the Swift helper; SimulatedCapture replays two WAV
files in real time (set CALLREC_SIMULATE=/path/to/dir-with-mic.wav-and-system.wav) so the whole app
can be exercised without permissions or a live call."""

import json
import os
import subprocess
import sys
import threading
import time
import wave
from pathlib import Path

import numpy as np

from . import HELPER


class CaptureError(RuntimeError):
    def __init__(self, message: str, hint: str = ""):
        super().__init__(message)
        self.hint = hint


class HelperCapture:
    def __init__(self, out_dir: Path, cfg: dict, on_event=lambda e: None):
        self.out_dir = out_dir
        self.cfg = cfg
        self.on_event = on_event
        self.proc: subprocess.Popen | None = None
        self.levels: dict = {}
        self.tracks: dict = {}
        self.warnings: list[str] = []
        self._started = threading.Event()
        self._error: dict | None = None
        self._stderr: list[str] = []

    def start(self, timeout: float = 120) -> None:  # long enough to answer permission prompts
        if sys.platform != "darwin":
            raise CaptureError(
                "Live audio recording requires Call Recorder to run on macOS.",
                "This server is running on Linux and cannot access your Mac's microphone or system audio. "
                "Run Call Recorder on your Mac to record live audio.",
            )
        if not HELPER.exists():
            raise CaptureError("The capture helper isn't built.", "Run ./swift/build.sh")
        cmd = [str(HELPER), "record", "--out", str(self.out_dir)]
        if self.cfg.get("mic_device"):
            cmd += ["--mic-device", self.cfg["mic_device"]]
        if not self.cfg.get("capture_system", True):
            cmd.append("--no-system")
        # Own process group, so a Ctrl-C in the terminal reaches us (and we stop cleanly) rather than
        # killing the helper out from under us.
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     text=True, bufsize=1, start_new_session=True)
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()
        if not self._started.wait(timeout) or self._error:
            err = self._error or {"message": "The capture helper didn't start in time."}
            self.stop()
            hint = err.get("hint", "")
            if os.environ.get("__CFBundleIdentifier") == "local.callrec.app":  # running inside Call Recorder.app
                hint = (hint.replace("the app you launched Call Recorder from (e.g. Terminal)", "Call Recorder")
                            .replace("the app you run Call Recorder from", "Call Recorder")
                            .replace("then quit and reopen that app", "then quit Call Recorder from the menu bar and open it again"))
            raise CaptureError(err.get("message", "Capture failed"), hint)

    def _read_stdout(self):
        kind = None
        for line in self.proc.stdout:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            kind = event.get("event")
            if kind == "levels":
                self.levels = event
            elif kind == "started":
                self.tracks = {"mic": event.get("mic", False), "system": event.get("system", False)}
                self.warnings = event.get("warnings", [])
            elif kind == "error":
                self._error = event
            if kind in ("started", "error"):
                self._started.set()
            self.on_event(event)
        # The helper crashed or was killed: say so, or the call would look like it's still being recorded.
        if kind not in ("stopped", "error"):
            event = {"event": "error", "message": f"Audio capture quit unexpectedly (exit code {self.proc.wait()})."}
            if not self._started.is_set():
                self._error = event
            self.on_event(event)
        self._started.set()

    def _read_stderr(self):
        for line in self.proc.stderr:
            self._stderr.append(line.rstrip())
            del self._stderr[:-50]

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def stop(self, timeout: float = 10) -> None:
        if not self.proc:
            return
        if self.proc.poll() is None:
            try:
                self.proc.stdin.write("stop\n")
                self.proc.stdin.flush()
            except (BrokenPipeError, ValueError):
                pass
            try:
                self.proc.wait(timeout)
            except subprocess.TimeoutExpired:
                self.proc.terminate()
                try:
                    self.proc.wait(5)
                except subprocess.TimeoutExpired:
                    self.proc.kill()


class SimulatedCapture:
    """Streams mic.wav/system.wav from a folder into the meeting's .pcm files at real-time speed."""

    def __init__(self, out_dir: Path, cfg: dict, on_event=lambda e: None, source: Path | None = None,
                 speed: float = 1.0):
        self.out_dir = out_dir
        self.on_event = on_event
        self.source = Path(source or os.environ["CALLREC_SIMULATE"])
        self.speed = speed
        self.levels: dict = {}
        self.tracks: dict = {}
        self.warnings: list[str] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @staticmethod
    def _load(path: Path) -> np.ndarray:
        with wave.open(str(path)) as w:
            return np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")

    def start(self) -> None:
        self._audio = {name: self._load(self.source / f"{name}.wav") for name in ("mic", "system")
                       if (self.source / f"{name}.wav").exists()}
        if not self._audio:
            raise CaptureError(f"No mic.wav/system.wav in {self.source}")
        self.tracks = {"mic": "mic" in self._audio, "system": "system" in self._audio}
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self.on_event({"event": "started", "simulated": True, **self.tracks})

    def _run(self):
        chunk = 1600  # 100 ms
        files = {name: open(self.out_dir / f"{name}.pcm", "ab") for name in self._audio}
        pos, t0 = 0, time.monotonic()
        last_level = 0.0
        while not self._stop.is_set():
            level_event = {"event": "levels"}
            for name, data in self._audio.items():
                piece = data[pos:pos + chunk]
                if len(piece) < chunk:  # source exhausted: keep the clock running with silence
                    piece = np.concatenate([piece, np.zeros(chunk - len(piece), dtype="<i2")])
                files[name].write(piece.tobytes())
                files[name].flush()
                f = piece.astype(np.float32) / 32768
                level_event[name] = {"rms": float(np.sqrt((f ** 2).mean())), "peak": float(np.abs(f).max()),
                                     "seconds": (pos + chunk) / 16000}
            pos += chunk
            if time.monotonic() - last_level > 0.25:
                self.levels = level_event
                self.on_event(level_event)
                last_level = time.monotonic()
            delay = t0 + pos / 16000 / self.speed - time.monotonic()
            if delay > 0:
                self._stop.wait(delay)
        for f in files.values():
            f.close()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def stop(self, timeout: float = 10) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)
        self.on_event({"event": "stopped", "simulated": True})


def make_capture(out_dir: Path, cfg: dict, on_event=lambda e: None):
    if os.environ.get("CALLREC_SIMULATE"):
        return SimulatedCapture(out_dir, cfg, on_event)
    return HelperCapture(out_dir, cfg, on_event)

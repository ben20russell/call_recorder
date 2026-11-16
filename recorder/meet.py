"""Names from Google Meet. The Call Recorder extension for Chrome (chrome-extension/) watches a Meet call and
reports the names on its tiles and whose tile shows them talking. While a call is being recorded, the reports
go into the call's meet.jsonl, timed from the start of the recording, and name the other side's lines: live
while recording, and after the call together with the voice fingerprints (see speakers.py). Before they're
trusted after a call, the times Meet showed someone talking are checked against the call's own audio.
"""

import json
import shutil
import subprocess
import threading
import time
from pathlib import Path

from . import ROOT, config

FILE = "meet.jsonl"
HEARTBEAT = 5.0   # the extension reports at least this often during a call (seconds)
LAG = 0.4         # Meet lights a tile about this long after the sound starts (used until a call is checked)
CLEAR = 0.6       # a line is someone's when Meet shows them talking for this much of it…
OTHERS = 0.25     # …and nobody else for more than this
SOURCE = ROOT / "chrome-extension"
INSTALLED = Path.home() / "Library" / "Application Support" / "Call Recorder" / "Chrome Extension"

_lock = threading.Lock()
_written: dict[str, str] = {}  # call id → the participant list last written to its timeline
status: dict = {}              # the extension's last report: at, title, in_call, people


# -- reports from the extension ---------------------------------------------------------------------------

def report(meeting, started: float | None, item: dict) -> None:
    """Note the extension's report and, while recording (`meeting`), add it to the call's timeline."""
    saved = status.get("at") or 0
    status.update(at=time.time(), title=item.get("title") or "", in_call=bool(item.get("in_call")),
                  people=len(item.get("participants") or []))
    if status["at"] - saved > 60:  # remembered across restarts, so Settings knows it's set up
        (config.CONFIG_DIR / "meet.json").write_text(json.dumps(status))
    if meeting is None or started is None:
        return
    at = lambda ms: round(ms / 1000 - started, 2)
    lines = []
    people = item.get("participants") or []
    roster = json.dumps([people, item.get("self"), item.get("code")])
    if item.get("in_call") and _written.get(meeting.id) != roster:
        lines.append({"t": at(item["t"]), "people": people, "self": item.get("self"), "code": item.get("code")})
    for event in item.get("events") or []:
        lines.append({"t": at(event["t"]), "talking": event.get("talking") or [], "me": bool(event.get("me")),
                      "seen": event.get("seen", True)})
    if not item.get("events") and item.get("in_call"):  # nothing changed: still going
        lines.append({"t": at(item["t"]), "talking": item.get("talking") or [], "me": bool(item.get("me")),
                      "seen": item.get("seen", True)})
    if not item.get("in_call"):
        lines.append({"t": at(item["t"]), "left": True})
    if item.get("check"):
        lines.append({"t": at(item["t"]), "check": item["check"]})
    with _lock:
        with open(meeting.path / FILE, "a") as f:
            f.writelines(json.dumps(line) + "\n" for line in lines)
        if item.get("in_call") and _written.get(meeting.id) != roster:
            _written[meeting.id] = roster
            known = meeting.meta().get("meet_people") or []
            if set(people) - set(known):
                meeting.update(meet_people=sorted(set(known) | set(people)))


def connection() -> dict:
    """For Settings: whether the extension has been heard from, and where to install it from."""
    if not status:
        try:
            status.update(json.loads((config.CONFIG_DIR / "meet.json").read_text()), in_call=False)
        except (FileNotFoundError, ValueError):
            pass
    at = status.get("at")
    return {**status, "seconds_ago": round(time.time() - at) if at else None, "folder": str(INSTALLED)}


def install() -> Path:
    """Keep a copy of the extension where Chrome can always read it (~/Downloads and ~/Documents can be
    off-limits to apps), refreshed when this one changes. Chrome picks up a change the next time it starts."""
    INSTALLED.mkdir(parents=True, exist_ok=True)
    for src in SOURCE.iterdir():
        dst = INSTALLED / src.name
        if src.is_file() and (not dst.exists() or dst.read_bytes() != src.read_bytes()):
            shutil.copy2(src, dst)
    return INSTALLED


def reveal() -> None:
    subprocess.run(["open", "-R", str(install())], capture_output=True)


# -- a call's timeline ------------------------------------------------------------------------------------

class Timeline:
    """Who Meet showed talking when, from a call's meet.jsonl. refresh() reads what's been added since."""

    def __init__(self, path: Path, my_name: str = ""):
        self.path = path
        self.my_name = my_name
        self.offset = 0
        self.people: set[str] = set()
        self.me = ""                                 # your name on Meet
        self.spans: dict[str | None, list] = {}      # name (None: you) → [(start, end), …]
        self.blind: list[tuple[float, float]] = []   # when the Meet window was hidden or quiet (nothing known)
        self.first: float | None = None
        self.last: float | None = None
        self._open: dict[str | None, float] = {}
        self._blind_since: float | None = None

    def refresh(self) -> "Timeline":
        try:
            with open(self.path, "rb") as f:
                f.seek(self.offset)
                data = f.read()
        except FileNotFoundError:
            return self
        whole = data[: data.rfind(b"\n") + 1]  # a line still being written waits for the next refresh
        self.offset += len(whole)
        for line in whole.decode().splitlines():
            try:
                self._add(json.loads(line))
            except (ValueError, KeyError, TypeError):
                continue
        return self

    def _yours(self, name: str) -> bool:
        if self.me:
            return name == self.me
        mine = self.my_name.lower().split()  # Meet didn't mark your tile: go by your name
        return bool(mine) and name.lower().split()[: len(mine)] == mine

    def _add(self, item: dict) -> None:
        t = item["t"]
        if self.last is not None and t - self.last > 2.5 * HEARTBEAT:  # reports stopped for a while
            self._close_all(self.last + HEARTBEAT)
            self.blind.append((self.last + HEARTBEAT, t))
        if self.first is None:
            self.first = t
        self.last = max(t, self.last or t)
        if "people" in item:
            self.people |= set(item["people"])
            self.me = item.get("self") or self.me
        if item.get("left"):
            self._close_all(t)
        elif "talking" in item:
            seen = item.get("seen", True)
            if not seen and self._blind_since is None:
                self._blind_since = t
            elif seen and self._blind_since is not None:
                self.blind.append((self._blind_since, t))
                self._blind_since = None
            now = {n for n in item["talking"] if not self._yours(n)} if seen else set()
            now |= {None} if seen and item.get("me") else set()
            for name in set(self._open) - now:
                self.spans.setdefault(name, []).append((self._open.pop(name), t))
            for name in now - set(self._open):
                self._open[name] = t

    def _close_all(self, t: float) -> None:
        for name, start in self._open.items():
            self.spans.setdefault(name, []).append((start, max(start, t)))
        self._open.clear()

    def intervals(self) -> dict[str | None, list]:
        """Everyone's talking spans; spans still going last until shortly after the latest report."""
        out = {name: list(spans) for name, spans in self.spans.items()}
        for name, start in self._open.items():
            out.setdefault(name, []).append((start, max(start, (self.last or start) + HEARTBEAT)))
        return out

    def who(self, start: float, end: float, lag: float = LAG) -> str | None:
        """The one person (not you) Meet showed talking through most of start…end, when that's clear."""
        length = end - start
        if length <= 0:
            return None
        cover = {}
        for name, spans in self.intervals().items():
            if name is not None:
                got = sum(max(0.0, min(b - lag, end) - max(a - lag, start)) for a, b in spans)
                if got > 0:
                    cover[name] = got
        ranked = sorted(cover.values(), reverse=True)
        if not ranked or ranked[0] < CLEAR * length or (len(ranked) > 1 and ranked[1] > OTHERS * length):
            return None
        return max(cover, key=cover.get)


def load(path: Path, my_name: str = "") -> Timeline | None:
    """A call's timeline, or None when Meet wasn't watched during it."""
    timeline = Timeline(path / FILE, my_name).refresh()
    return timeline if timeline.last is not None and (timeline.spans or timeline._open) else None


# -- checking it against the audio ----------------------------------------------------------------------

def _merge(spans) -> list[tuple[float, float]]:
    out = []
    for a, b in sorted(spans):
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        elif b > a:
            out.append((a, b))
    return out


def _total(spans) -> float:
    return sum(b - a for a, b in spans)


def _overlap(xs, ys) -> float:
    """Seconds two sorted, non-overlapping span lists have in common."""
    i = j = 0
    got = 0.0
    while i < len(xs) and j < len(ys):
        got += max(0.0, min(xs[i][1], ys[j][1]) - max(xs[i][0], ys[j][0]))
        if xs[i][1] < ys[j][1]:
            i += 1
        else:
            j += 1
    return got


def _minus(within: tuple[float, float], holes) -> list[tuple[float, float]]:
    out, at = [], within[0]
    for a, b in _merge(holes):
        if a > at:
            out.append((at, min(a, within[1])))
        at = max(at, b)
    if at < within[1]:
        out.append((at, within[1]))
    return [(a, b) for a, b in out if b > a]


def check(timeline: Timeline, speech: list, length: float) -> tuple[float | None, dict]:
    """Whether Meet's talking lights match the other side's audio (`speech`: when there was speech on the
    system-audio track), and how far they trail it. Returns (lag, numbers): lag is None when they don't match
    well enough to name anyone by them, or when there's too little to tell."""
    spans = timeline.intervals()
    lit = _merge(s for name, ss in spans.items() if name is not None for s in ss)
    speech = _merge(speech)
    known = _minus((max(0.0, timeline.first or 0.0), min(length, (timeline.last or 0.0) + HEARTBEAT)), timeline.blind)
    numbers = {"lit": round(_total(lit), 1), "known": round(_total(known), 1), "people": len(timeline.people)}
    if _total(lit) < 15:
        return None, {**numbers, "verdict": "too little"}
    best = None
    for step in range(-5, 16):  # Meet trailing the audio by -0.5…1.5 s
        lag = step / 10
        shifted = [(a - lag, b - lag) for a, b in lit]
        hit = _overlap(shifted, speech) / _total(shifted)
        dark = _merge(x for k in known for x in _minus(k, shifted))
        miss = _overlap(dark, speech) / _total(dark) if _total(dark) >= 20 else None
        score = hit - (miss or 0.0)
        if best is None or score > best[0]:
            best = (score, lag, hit, miss)
    _, lag, hit, miss = best
    numbers.update(lag=lag, speech_when_lit=round(hit, 2), speech_when_dark=None if miss is None else round(miss, 2))
    good = hit >= 0.6 and (miss is None or hit - miss >= 0.2)
    return (lag if good else None), {**numbers, "verdict": "matches" if good else "doesn't match the audio"}

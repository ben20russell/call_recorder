"""Search across every call: titles, people, AI notes, your own notes and full transcripts. Used by the app's
search bar and by Claude's search_calls tool (mcp_server.py), so both find the same things."""

import json
import re
import threading
from datetime import date, datetime
from pathlib import Path

from . import config
from .calendar_src import display_name
from .meetings import Meeting
from .transcribe import paragraphs

STOPWORDS = set("""a an and are as at be but by did do does for from had has have he her his how i in is it its
me my of on or our say said she so that the their them they this to was we were what when where which who why
will with you your about any anything anyone call calls meeting meetings talk talked tell""".split())
FILES = ("meta.json", "summary.json", "notes.md", "transcript.json", "my-notes.md", "live-transcript.jsonl")

_cache: dict[Path, tuple[tuple, dict]] = {}


class SearchError(ValueError):
    pass


# -- loading calls ------------------------------------------------------------------------------------

def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except FileNotFoundError:
        return 0


def _live_segments(meeting: Meeting) -> list[dict]:
    try:
        lines = meeting.live_path.read_text().splitlines()
    except FileNotFoundError:
        return []
    return [json.loads(line) for line in lines if line.strip()]


def load(path: Path) -> dict:
    """Everything searchable about one call, re-read only when its files change."""
    signature = tuple(_mtime(path / name) for name in FILES)
    cached = _cache.get(path)
    if cached and cached[0] == signature:
        return cached[1]
    meeting = Meeting(path)
    meta = meeting.meta()
    summary = meeting.summary() or {}
    attendees = meta.get("attendees") or []
    people = summary.get("participants") or [display_name(a) for a in attendees]
    call = {
        "id": meeting.id,
        "title": meta.get("title") or meeting.id,
        "started_at": meta.get("started_at") or "",
        "duration": meta.get("duration") or 0,
        "status": meta.get("status", ""),
        "people": [p for p in people if p],
        "emails": [a.get("email", "") for a in attendees],
        "summary": summary.get("summary", ""),
        "action_items": summary.get("action_items", []),
        # notes.md ends with the full transcript, which is searched separately
        "notes": meeting.read(meeting.notes_path, "").split("\n## Transcript", 1)[0].strip(),
        "my_notes": meeting.my_notes().strip(),
        "paragraphs": paragraphs(meeting.transcript() or _live_segments(meeting)),
        "audio": str(path / "call.m4a") if (path / "call.m4a").exists() else "",
        "source": meta.get("source", ""),  # "granola" for calls imported from a Granola export
    }
    _cache[path] = (signature, call)
    return call


def _day(value: str, name: str) -> str:
    if not value:
        return ""
    try:
        return date.fromisoformat(value[:10]).isoformat()
    except ValueError:
        raise SearchError(f"{name} must be a date like 2026-10-28, not {value!r}")


def calls(cfg: dict, since: str = "", until: str = "", person: str = "") -> list[dict]:
    """Calls newest first, optionally limited to a date range and a participant (name or email)."""
    since, until, person = _day(since, "since"), _day(until, "until"), person.lower().strip()
    out = []
    for meta_file in config.recordings_dir(cfg).glob("*/meta.json"):
        call = load(meta_file.parent)
        day = call["started_at"][:10]
        if (since and day < since) or (until and day > until):
            continue
        if person and not any(person in p.lower() for p in call["people"] + call["emails"]):
            continue
        out.append(call)
    return sorted(out, key=lambda c: c["started_at"], reverse=True)


# -- matching -----------------------------------------------------------------------------------------

def terms(query: str) -> list[str]:
    """The words worth matching: lowercase, without filler like "what did … say about"."""
    words = re.findall(r"[\w'’-]+", query.lower())
    kept = [w for w in words if w not in STOPWORDS and len(w) > 1] or words
    return list(dict.fromkeys(kept))


def compile_terms(words: list[str]) -> list[re.Pattern]:
    return [re.compile(r"\b" + re.escape(w)) for w in words]  # prefix match: "launch" finds "launches"


def _matched(text: str, patterns: list[re.Pattern]) -> set[int]:
    lower = text.lower()
    return {i for i, p in enumerate(patterns) if p.search(lower)}


def excerpt(text: str, patterns: list[re.Pattern], width: int = 420) -> str:
    """The stretch of `text` around its first match, about `width` characters, without markdown emphasis."""
    text = re.sub(r"\s+", " ", re.sub(r"\*\*|__|`", "", text)).strip()
    if len(text) <= width:
        return text
    lower = text.lower()
    first = min((m.start() for p in patterns if (m := p.search(lower))), default=0)
    start = max(0, first - width // 3)
    if start:
        start = text.find(" ", start) + 1 or start  # begin on a word
    return ("…" if start else "") + text[start:start + width].rstrip() + "…"


def _score(call: dict, patterns: list[re.Pattern], phrase: str) -> tuple[float, list[dict], int]:
    fields = [(call["title"], 4), (" ".join(call["people"] + call["emails"]), 3),
              ("\n".join((call["summary"], call["notes"], call["my_notes"])), 2)]
    found: set[int] = set()
    score = 0.0
    for text, weight in fields:
        hits = _matched(text, patterns)
        found |= hits
        score += weight * len(hits)

    # Notes lines (with the section they're under) and transcript paragraphs that mention the query.
    matches = []
    section = ""  # the title and details above the first section aren't worth quoting
    for line in (call["notes"] + ("\n## My notes\n" + call["my_notes"] if call["my_notes"] else "")).splitlines():
        heading = re.match(r"#{2,6}\s+(.*)", line)
        if heading:
            section = heading.group(1).strip()
            continue
        if not section or section == "Summary":  # the summary is shown with every result already
            continue
        hits = _matched(line, patterns)
        if hits:
            bullet = re.sub(r"^\s*[-*]\s+(\[[ xX]\]\s*)?", "", line).lstrip("# ")
            matches.append({"kind": "notes", "section": section, "text": excerpt(bullet, patterns),
                            "_rank": len(hits) + 0.5})
    spoken: set[int] = set()
    for p in call["paragraphs"]:
        hits = _matched(f"{p['speaker']}: {p['text']}", patterns)  # "Candice budget" finds Candice saying it
        if hits:
            spoken |= hits
            matches.append({"kind": "transcript", "start": round(p["start"], 1), "speaker": p["speaker"],
                            "approx": bool(p.get("approx")),
                            "text": excerpt(p["text"], patterns), "_rank": len(hits)})
    found |= spoken
    score += len(spoken) + 0.2 * min(len(matches), 10)

    if not found:
        return 0, [], 0
    score *= len(found) / len(patterns)  # calls that mention more of the query rank higher
    blob = "\n".join([call["title"], call["notes"], call["my_notes"]] + [p["text"] for p in call["paragraphs"]])
    if " " in phrase and phrase in re.sub(r"\s+", " ", blob.lower()):
        score += 5
    matches.sort(key=lambda m: -m["_rank"])  # stable: notes before transcript on ties
    for m in matches:
        del m["_rank"]
    return score, matches, len(found)


def search(cfg: dict, query: str, since: str = "", until: str = "", person: str = "") -> dict:
    """Best-matching calls first, each with the passages that match: {"terms", "results"}. Each result is
    a call (see load(), minus its full text) plus "matches": notes lines and transcript paragraphs."""
    words = terms(query)
    if not words:
        return {"terms": [], "results": []}
    patterns = compile_terms(words)
    phrase = re.sub(r"\s+", " ", query.lower()).strip()
    ranked = []
    for call in calls(cfg, since, until, person):
        score, matches, covered = _score(call, patterns, phrase)
        if score:
            brief = {k: v for k, v in call.items() if k not in ("notes", "my_notes", "paragraphs", "emails")}
            ranked.append({**brief, "action_items": len(call["action_items"]), "score": round(score, 2), "_covered": covered,
                           "matches": matches})
    # Only the calls that mention the most of the query: all of it when any call does.
    most = max((r["_covered"] for r in ranked), default=0)
    ranked = [r for r in ranked if r.pop("_covered") == most]
    ranked.sort(key=lambda r: r["started_at"], reverse=True)
    ranked.sort(key=lambda r: -r["score"])  # stable: newest first among equally good matches
    return {"terms": words, "results": ranked}


# -- recent searches ------------------------------------------------------------------------------------
# What you searched for in the search bar, newest first, with Claude's answer when you asked it (Enter), so you
# can go back to a search, or read an answer again without asking Claude again. Kept in ~/.callrec/searches.json.

RECENT_MAX = 30
_recent_lock = threading.Lock()


def _recent_path() -> Path:
    return config.CONFIG_DIR / "searches.json"


def recent() -> list[dict]:
    """[{q, at, answer?, answered_at?}], newest first."""
    try:
        items = json.loads(_recent_path().read_text())
        return items if isinstance(items, list) else []
    except (FileNotFoundError, ValueError):
        return []


def _write(items: list[dict]) -> None:
    path = _recent_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(items[:RECENT_MAX], indent=1))
    tmp.replace(path)


def remember(query: str, answer: str | None = None) -> None:
    """Put a search at the top of the recent ones (`answer`: Claude's, when it was asked). A search that only
    grew from the last one (you kept typing) replaces it; an earlier answer to the same words is kept."""
    query = " ".join(query.split())[:300]
    if not query:
        return
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    with _recent_lock:
        items = recent()
        same = next((i for i in items if i["q"].lower() == query.lower()), None)
        items = [i for i in items if i is not same]
        if items and not answer and not items[0].get("answer") and query.lower().startswith(items[0]["q"].lower()) \
                and _seconds_since(items[0].get("at")) < 60:
            items.pop(0)  # still typing the same search
        item = {**(same or {}), "q": query, "at": now}
        if answer:
            item.update(answer=answer, answered_at=now)
        _write([item] + items)


def forget(query: str | None = None) -> None:
    """Drop one recent search, or (None) all of them."""
    with _recent_lock:
        _write([] if query is None else [i for i in recent() if i["q"].lower() != " ".join(query.split()).lower()])


def _seconds_since(iso: str | None) -> float:
    try:
        return (datetime.now().astimezone() - datetime.fromisoformat(iso)).total_seconds()
    except (TypeError, ValueError):
        return float("inf")

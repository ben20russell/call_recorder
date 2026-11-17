"""Live To-Dos: the to-dos assigned to you, gathered from every call's notes. Recorded calls have the
action items Claude wrote; notes imported from Granola have them in their "Next Steps" section, as
"**Task** (Owner)", "Owner: task" or "Owner to do something". The list is re-read from the notes each
time, so it follows new and re-processed calls; what you tick off is remembered in ~/.callrec/todos.json.
"""

import hashlib
import json
import re
import threading
from datetime import datetime, timedelta

from . import config, search

_lock = threading.Lock()

NEXT_STEPS = re.compile(r"^(#{2,4}) [^\n]*(?:next steps?|action items?|to-?dos?|follow[- ]ups?)[^\n]*\n(.*?)(?=^#{1,4} |^---|\Z)",
                        re.M | re.S | re.I)
BOLD_OWNER = re.compile(r"^\*\*(.+?)\*\*\s*\(([^)]+)\)\s*$")         # **Task** (Owner, Owner)
OWNER_COLON = re.compile(r"^\*{0,2}([A-Z][\w.'’ /&,+-]{0,40}?)\*{0,2}:\*{0,2}\s+(.+)$")  # Owner: task
OWNER_TO = re.compile(r"^([A-Z][\w'’]+(?:\s*(?:/|,|&|and)\s*[A-Z][\w'’]+)*) to (.+)$")  # Owner to do something
SAME_CALL = 15 * 60  # a Granola note this close to a recorded call is the same call (the recording wins)


def people(cfg: dict) -> list[str]:
    """Whose to-dos to show: just the current user."""
    return [cfg.get("my_name") or "Me"]


def _first(name: str) -> str:
    return re.sub(r"[^\w]", "", (name.split() or [""])[0]).lower()


def _owners(owner: str, team: list[str]) -> list[str]:
    """Which of the team an owner string ("Ben", "Luwam Gezai", "Ben/Luwam", "Ty") means."""
    found = []
    for part in re.split(r"\s*(?:/|,|&|\+|\band\b)\s*", owner or ""):
        word = _first(part)
        if len(word) < 2:
            continue
        for name in team:
            first = _first(name)
            short = len(part.split()) == 1 and first.startswith(word)  # a nickname on its own: "Ty" is Tyler
            if (word == first or short) and name not in found:
                found.append(name)
    return found


def _plain(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"\*\*|__|`", "", text)).strip()


def from_next_steps(notes: str) -> list[dict]:
    """{task, owner, context} for each to-do with an owner in a note's Next Steps."""
    items = []
    for section in NEXT_STEPS.finditer(notes):
        lines = section.group(2).splitlines()
        for i, line in enumerate(lines):
            nested = re.match(r"^\s+[-*] ", line)
            if not re.match(r"^[-*] ", line) and not nested:
                continue
            text = line.strip()[2:].strip()
            task = owner = None
            if not nested and (m := BOLD_OWNER.match(text)):
                task, owner = m.group(1), m.group(2)
            elif m := OWNER_COLON.match(text):
                owner, task = m.group(1), m.group(2)
            elif m := OWNER_TO.match(_plain(text)):
                owner, task = m.group(1), m.group(2)
            if not task:
                continue
            context = []  # the indented paragraph under a "**Task** (Owner)" bullet
            for more in lines[i + 1:]:
                if re.match(r"^\s*[-*] ", more) or re.match(r"^\S", more):
                    break
                if more.strip():
                    context.append(more.strip())
            task = _plain(task)
            items.append({"task": task[0].upper() + task[1:], "owner": _plain(owner), "context": _plain(" ".join(context))})
    return items


def _key(call_id: str, task: str) -> str:
    return hashlib.sha1(f"{call_id}\n{task}".encode()).hexdigest()[:12]


def _done_path():
    return config.CONFIG_DIR / "todos.json"


def _load_done() -> dict:
    try:
        return json.loads(_done_path().read_text())
    except (FileNotFoundError, ValueError):
        return {}


def set_done(key: str, done: bool) -> None:
    with _lock:
        marks = _load_done()
        if done:
            marks[key] = {"done_at": datetime.now().astimezone().isoformat()}
        else:
            marks.pop(key, None)
        tmp = _done_path().with_suffix(".tmp")
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(marks, indent=1))
        tmp.replace(_done_path())


def collect(cfg: dict, days: int | None = 14) -> dict:
    """Everyone's to-dos from calls in the last `days` days (None: all of them), newest call first."""
    team = people(cfg)
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d") if days else ""
    calls = search.calls(cfg, since=since)
    recorded = [datetime.fromisoformat(c["started_at"]).timestamp() for c in calls
                if c["source"] != "granola" and c["action_items"] and c["started_at"]]
    marks = _load_done()
    items = []
    for call in calls:
        if call["source"] == "granola":
            when = datetime.fromisoformat(call["started_at"]).timestamp() if call["started_at"] else 0
            if any(abs(when - t) < SAME_CALL for t in recorded):
                continue
            found = from_next_steps(call["notes"])
        else:
            found = [{"task": i.get("task", ""), "owner": i.get("owner", ""), "context": i.get("context", ""),
                      "due": i.get("due", "")} for i in call["action_items"]]
        for item in found:
            owners = _owners(item["owner"], team)
            if not owners or not item["task"]:
                continue
            key = _key(call["id"], item["task"])
            items.append({**item, "key": key, "people": owners, "call_id": call["id"], "call_title": call["title"],
                          "call_at": call["started_at"], "source": call["source"] or "recorded",
                          "done": key in marks, "done_at": (marks.get(key) or {}).get("done_at")})
    items.sort(key=lambda i: i["call_at"], reverse=True)
    return {"people": team, "days": days, "items": items}

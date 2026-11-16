"""MCP server that lets Claude search and read your calls: notes, transcripts, your own notes and action
items. Read-only. Claude starts it itself (`./callrec mcp`) once it's registered — see README →
"Ask Claude about your calls"."""

from datetime import datetime

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from . import config, search
from .meetings import resolve
from .transcribe import when

READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=False)


def _instructions() -> str:
    cfg = config.load()
    me, them = cfg["my_name"], cfg["other_label"]
    return (f"{me}'s recorded calls from Call Recorder: AI-written notes, full transcripts (who said what, with "
            f"timestamps), {me}'s own notes, and action items. Use search_calls to find where something was "
            "discussed, then get_call for the full notes or transcript. In transcripts, "
            f"\"{me}\" is {me}'s microphone. Other people are labeled by name when their voice was recognized or "
            f"named; \"Speaker 1\", \"Speaker 2\"… are voices nobody has named yet, and \"{them}\" is everyone else "
            "mixed together (older calls). \"Mic\" means only the microphone was recorded, so it can be either "
            "side. Quote the call title and date when you answer from a call.")


server = MCPServer("Call Recorder", instructions=_instructions())


def _calls(since: str = "", until: str = "", person: str = "") -> list[dict]:
    try:
        return search.calls(config.load(), since, until, person)
    except search.SearchError as e:
        raise ToolError(str(e))


def _when(call: dict) -> str:
    try:
        started = datetime.fromisoformat(call["started_at"])
    except ValueError:
        return "unknown date"
    length = f" ({round(call['duration'] / 60)} min)" if call["duration"] else ""
    return f"{started:%a %b %-d, %Y %-I:%M %p}{length}"


def _heading(call: dict) -> list[str]:
    me = config.load()["my_name"].lower()
    lines = [f"## {call['title']} — {_when(call)}", f"id: {call['id']}"]
    others = [p for p in call["people"] if p.lower() not in (me, "me")]
    if others:
        lines.append("With: " + ", ".join(others))
    if call["source"] == "granola":
        lines.append("Source: imported from Granola (no audio; transcript times are approximate, speakers mostly unknown)")
    if call["status"] not in ("done", ""):
        lines.append(f"Status: {call['status']}" + (" (still recording; transcript so far)" if call["status"] == "recording" else ""))
    return lines


@server.tool(annotations=READ_ONLY)
def search_calls(query: str, since: str = "", until: str = "", person: str = "", limit: int = 8) -> str:
    """Search all recorded calls — titles, participants, AI notes, your own notes and full transcripts — and
    return the best-matching calls with the passages that match. Use keywords (names, projects, topics);
    words are matched as prefixes, so "launch" also finds "launches". since/until are dates (YYYY-MM-DD);
    person limits results to calls with that participant (name or email). Follow up with get_call to read
    a whole call."""
    try:
        found = search.search(config.load(), query, since, until, person)
    except search.SearchError as e:
        raise ToolError(str(e))
    if not found["terms"]:
        return "Give some words to search for."
    ranked = found["results"]
    if not ranked:
        return f"No calls mention {query!r}" + (" in that range." if since or until or person else ".")
    patterns = search.compile_terms(found["terms"])
    out = [f"{len(ranked)} call(s) match {query!r}; best matches first."]
    for call in ranked[:max(1, min(limit, 25))]:
        out += [""] + _heading(call)
        if call["summary"]:
            out.append("Summary: " + search.excerpt(call["summary"], patterns, 500))
        out.append("Matches:")
        for m in call["matches"][:5]:
            where = f"Notes › {m['section']}" if m["kind"] == "notes" else f"[{when(m)}] {m['speaker']}".rstrip()
            out.append(f"- {where}: {m['text']}")
    if len(ranked) > limit:
        out.append(f"\n({len(ranked) - limit} more; narrow the search or raise limit.)")
    return "\n".join(out)


@server.tool(annotations=READ_ONLY)
def list_calls(since: str = "", until: str = "", person: str = "", limit: int = 20) -> str:
    """List recorded calls, newest first, with who was on them and a one-paragraph summary. since/until are
    dates (YYYY-MM-DD); person filters by participant name or email."""
    calls = _calls(since, until, person)
    if not calls:
        return "No recorded calls" + (" in that range." if since or until or person else " yet.")
    out = [f"{len(calls)} call(s); newest first."]
    for call in calls[:max(1, min(limit, 100))]:
        out += [""] + _heading(call)
        if call["summary"]:
            out.append("Summary: " + call["summary"])
        if call["action_items"]:
            out.append(f"Action items: {len(call['action_items'])}")
    if len(calls) > limit:
        out.append(f"\n({len(calls) - limit} older calls not shown; raise limit or set since/until.)")
    return "\n".join(out)


def _find(call_id: str) -> dict | str:
    """A call by id (as search_calls/list_calls show it), "last", or a unique part of its title."""
    meeting = resolve(config.load(), call_id.strip())
    if meeting:
        return search.load(meeting.path)
    wanted = call_id.lower().strip()
    matches = [c for c in _calls() if wanted in c["title"].lower() or wanted in c["id"].lower()]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        return f"No call matches {call_id!r}. Use list_calls or search_calls to find its id."
    return "Several calls match; pass one of these ids:\n" + "\n".join(f"- {c['id']}" for c in matches[:15])


@server.tool(annotations=READ_ONLY)
def get_call(call_id: str, part: str = "notes", offset: int = 0, max_chars: int = 30000) -> str:
    """Read one call. call_id is the id from search_calls/list_calls, "last" for the most recent call, or a
    unique part of its title. part="notes" gives the AI notes (summary, notes, decisions, action items,
    open questions) plus your own notes; part="transcript" gives the full timestamped transcript, in pages
    of max_chars — pass the next_offset it reports to continue."""
    call = _find(call_id)
    if isinstance(call, str):
        return call
    head = _heading(call)
    if call["audio"]:
        head.append(f"Audio: {call['audio']}")
    if part == "transcript":
        lines = [f"[{when(p)}] " + (f"{p['speaker']}: " if p["speaker"] else "") + p["text"] for p in call["paragraphs"]]
        if not lines:
            return "\n".join(head + ["", "No transcript for this call."])
        text = "\n".join(lines)
        page = text[offset:offset + max(1000, max_chars)]
        if offset + len(page) < len(text):
            page = page[: page.rfind("\n") + 1 or len(page)]  # end at a whole line
        end = offset + len(page)
        more = f"\n\n(next_offset={end} of {len(text)} characters)" if end < len(text) else ""
        return "\n".join(head + [""]) + page.rstrip() + more
    if part != "notes":
        return 'part must be "notes" or "transcript".'
    body = call["notes"] or "(No notes yet: this call hasn't been processed. Read its transcript instead.)"
    if call["my_notes"] and "## My notes" not in body:
        body += "\n\n## My notes (raw)\n\n" + call["my_notes"]
    return "\n".join(head + ["", body])


@server.tool(annotations=READ_ONLY)
def list_action_items(since: str = "", until: str = "", owner: str = "", limit: int = 60) -> str:
    """Action items from recorded calls, newest calls first. owner filters by who owns the item (a name, or
    "me" for your own); since/until are dates (YYYY-MM-DD)."""
    cfg = config.load()
    wanted = owner.lower().strip()
    if wanted in ("me", "mine", "i"):
        wanted = cfg["my_name"].lower()
    out = []
    for call in _calls(since, until):
        for item in call["action_items"]:
            if wanted and wanted not in (item.get("owner") or "").lower():
                continue
            due = f" (due {item['due']})" if item.get("due") else ""
            out.append(f"- {item.get('owner') or '?'}: {item.get('task', '')}{due} — {call['title']}, "
                       f"{_when(call).split(' (')[0]} [id: {call['id']}]")
    if not out:
        return "No action items found."
    shown = out[:max(1, min(limit, 300))]
    return "\n".join(shown + ([f"({len(out) - len(shown)} more; raise limit or narrow the dates.)"] if len(out) > len(shown) else []))


def main():
    server.run("stdio")

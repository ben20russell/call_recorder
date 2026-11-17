"""Calendar awareness: today's meetings from macOS Calendar (EventKit, via the Swift helper) or an
ICS feed URL (e.g. Google Calendar → Settings → "Secret address in iCal format")."""

import base64
import binascii
import json
import re
import subprocess
import threading
import time
import urllib.request
from datetime import datetime, timedelta, timezone

from . import HELPER

CONFERENCE = re.compile(
    r"https?://(?:[\w-]+\.)?(?:zoom\.us/(?:j|my|w)/[^\s<>\"]+|meet\.google\.com/[a-z\-]+|"
    r"teams\.microsoft\.com/l/meetup-join/[^\s<>\"]+|teams\.live\.com/meet/[^\s<>\"]+|"
    r"[\w-]+\.webex\.com/[^\s<>\"]+|facetime\.apple\.com/join[^\s<>\"]*|app\.slack\.com/huddle/[^\s<>\"]+|"
    r"meet\.granola\.ai/j/[^\s<>\"]+)",
    re.I,
)
# Granola wraps a call's real link ("Google Meet (with Granola)"): https://meet.granola.ai/j/<id>?uri=<base64 of it>
GRANOLA_URI = re.compile(r"[?&]uri=([A-Za-z0-9+/_=-]+)")


class CalendarError(RuntimeError):
    pass


def _conference_url(*texts: str) -> str:
    for text in texts:
        match = CONFERENCE.search(text or "")
        if match:
            url = match.group(0).rstrip(").,>")
            return _unwrap_granola(url) if "meet.granola.ai" in url else url
    return ""


def _unwrap_granola(url: str) -> str:
    """The Meet/Zoom/Teams link inside a Granola link, so Join works without Granola."""
    match = GRANOLA_URI.search(url)
    if match:
        try:
            inner = base64.urlsafe_b64decode(match.group(1) + "=" * (-len(match.group(1)) % 4)).decode()
        except (binascii.Error, UnicodeDecodeError):
            return url
        if CONFERENCE.fullmatch(inner):
            return inner
    return url


EK_DECLINED = 3  # EKParticipantStatus.declined


def display_name(att: dict) -> str:
    """The attendee's name. Calendars often give only an email address ("luwam.gezai@…"), so make
    one from that ("Luwam Gezai")."""
    name = (att.get("name") or "").strip()
    if name and "@" not in name:
        return name
    local = (att.get("email") or name).split("@")[0]
    words = [w for w in re.split(r"[._+]+", local) if w.replace("-", "").isalpha()]
    return " ".join(w.upper() if len(w) <= 2 else "-".join(p.capitalize() for p in w.split("-")) for w in words)


def _normalize(ev: dict, cfg: dict) -> dict:
    my_name, my_email = cfg.get("my_name", "").lower(), cfg.get("my_email", "").lower()
    ev["title"] = (ev.get("title") or "").strip()
    for att in ev.get("attendees", []):
        att["name"] = display_name(att)
        att["is_me"] = bool(att.get("is_me") or (my_email and att.get("email", "").lower() == my_email)
                            or (my_name and att.get("name", "").lower() == my_name))
    ev["conference_url"] = _conference_url(ev.get("url", ""), ev.get("location", ""), ev.get("notes", ""))
    ev["others"] = [a for a in ev.get("attendees", []) if not a["is_me"]]
    ev["declined"] = any(a["is_me"] and (a.get("status") == EK_DECLINED or a.get("partstat") == "DECLINED")
                         for a in ev.get("attendees", []))
    # A call worth recording: someone other than you is on the invite and you haven't declined.
    ev["is_call"] = bool(ev["others"]) and not ev["declined"] and not ev.get("all_day")
    return ev


def _from_macos(start: datetime, end: datetime, sync: bool = False) -> list[dict]:
    """sync: have Calendar pull from Google/iCloud first (a few seconds), not just read what it has."""
    fmt = lambda d: d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    result = subprocess.run([str(HELPER), "events", "--from", fmt(start), "--to", fmt(end)] + (["--sync"] if sync else []),
                            capture_output=True, text=True, timeout=60)
    try:
        payload = json.loads(result.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        raise CalendarError(result.stderr.strip() or "Couldn't read macOS Calendar.")
    if payload.get("event") == "error":
        raise CalendarError(payload.get("message", "") + (" " + payload["hint"] if payload.get("hint") else ""))
    events = []
    for ev in payload["events"]:
        if ev.get("cancelled"):
            continue
        ev["start"] = datetime.fromisoformat(ev["start"].replace("Z", "+00:00")).astimezone().isoformat()
        ev["end"] = datetime.fromisoformat(ev["end"].replace("Z", "+00:00")).astimezone().isoformat()
        ev["id"] = f"{ev['id']}@{ev['start']}"  # repeating events share an id; make it per occurrence
        events.append(ev)
    return events


def _from_ics(url: str, start: datetime, end: datetime, my_email: str = "") -> list[dict]:
    import icalendar
    import recurring_ical_events

    url = url.replace("webcal://", "https://", 1)
    with urllib.request.urlopen(url, timeout=30) as resp:
        cal = icalendar.Calendar.from_ical(resp.read())
    events = []
    for comp in recurring_ical_events.of(cal).between(start, end):
        dtstart, dtend = comp.get("DTSTART").dt, comp.get("DTEND").dt if comp.get("DTEND") else None
        all_day = not isinstance(dtstart, datetime)
        if all_day:
            s = datetime.combine(dtstart, datetime.min.time()).astimezone()
            e = datetime.combine(dtend, datetime.min.time()).astimezone() if dtend else s + timedelta(days=1)
        else:
            s = dtstart.astimezone() if dtstart.tzinfo else dtstart.astimezone()
            e = (dtend.astimezone() if dtend else s + timedelta(hours=1))
        if str(comp.get("STATUS", "")).upper() == "CANCELLED":
            continue
        attendees = []
        raw = comp.get("ATTENDEE") or []
        for att in raw if isinstance(raw, list) else [raw]:
            email = str(att).removeprefix("mailto:").removeprefix("MAILTO:")
            name = str(att.params.get("CN", "")) if hasattr(att, "params") else ""
            params = att.params if hasattr(att, "params") else {}
            attendees.append({"name": name if name != email else "", "email": email,
                              "is_me": bool(my_email) and email.lower() == my_email.lower(),
                              "partstat": str(params.get("PARTSTAT", "")).upper()})
        events.append({
            "id": f"{comp.get('UID')}@{s.isoformat()}",
            "title": str(comp.get("SUMMARY", "(No title)")),
            "start": s.isoformat(), "end": e.isoformat(), "all_day": all_day,
            "location": str(comp.get("LOCATION", "")), "url": str(comp.get("URL", "")),
            "notes": str(comp.get("DESCRIPTION", ""))[:4000], "calendar": "ICS",
            "organizer": str(comp.get("ORGANIZER", "")).removeprefix("mailto:"), "attendees": attendees,
        })
    return events


def watched(events: list[dict], cfg: dict) -> list[dict]:
    """Only the calendars chosen in Settings (all of them when none are chosen)."""
    chosen = cfg.get("calendar", {}).get("calendars") or []
    return [e for e in events if e.get("calendar") in chosen] if chosen else events


def fetch(cfg: dict, start: datetime | None = None, end: datetime | None = None,
          all_calendars: bool = False, sync: bool = False) -> list[dict]:
    cal = cfg.get("calendar", {})
    source = cal.get("source", "macos")
    now = datetime.now().astimezone()
    start = start or now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = end or start + timedelta(days=1 + int(cal.get("days_ahead", 7)))
    if source == "off":
        return []
    if source == "ics":
        if not cal.get("ics_url"):
            raise CalendarError("No ICS URL set in Settings.")
        events = _from_ics(cal["ics_url"], start, end, cfg.get("my_email", ""))
    elif source == "google":
        from . import config, google_calendar
        client_id = cal.get("google_client_id", "").strip()
        if not client_id:
            raise CalendarError("Add a Google OAuth client ID in Settings before connecting.")
        try:
            events = google_calendar.fetch_events(
                client_id, config.get_secret("google_client_secret"),
                config.get_secret("google_refresh_token"), start, end,
            )
        except google_calendar.GoogleCalendarError as e:
            raise CalendarError(str(e)) from e
    else:
        events = _from_macos(start, end, sync)
    seen, unique = set(), []
    for ev in sorted((_normalize(e, cfg) for e in events), key=lambda e: e["start"]):
        key = (ev["title"].lower(), ev["start"], ev["end"])
        if ev["id"] in seen or key in seen:  # listed twice, or the same invite on two calendars
            continue
        seen.update((ev["id"], key))
        unique.append(ev)
    return unique if all_calendars else watched(unique, cfg)


def current_and_next(events: list[dict], now: datetime | None = None) -> tuple[dict | None, dict | None]:
    now = now or datetime.now().astimezone()
    timed = [e for e in events if not e.get("all_day")]
    current = [e for e in timed if datetime.fromisoformat(e["start"]) <= now < datetime.fromisoformat(e["end"])]
    upcoming = [e for e in timed if e["is_call"] and datetime.fromisoformat(e["start"]) > now]
    # Prefer the call-like event that started most recently.
    current.sort(key=lambda e: (e["is_call"], e["start"]), reverse=True)
    return (current[0] if current else None), (upcoming[0] if upcoming else None)


# -- per-call recording plans -------------------------------------------------------------------------
# Each upcoming call can be set to "record" (start automatically) or "skip" (no prompt); anything
# else follows the global setting: auto-record if that's on, otherwise a reminder when it starts.

def _plans_path():
    from . import config
    return config.CONFIG_DIR / "call-plans.json"


def load_plans() -> dict:
    try:
        return json.loads(_plans_path().read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def set_plan(event_id: str, plan: str) -> dict:
    plans = load_plans()
    if plan in ("record", "skip"):
        plans[event_id] = plan
    else:
        plans.pop(event_id, None)
    # forget plans for calls more than two weeks old
    cutoff = (datetime.now().astimezone() - timedelta(days=14)).isoformat()
    plans = {k: v for k, v in plans.items() if k.rsplit("@", 1)[-1] >= cutoff or "@" not in k}
    path = _plans_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(plans, indent=1))
    return plans


def effective_plan(ev: dict, cfg: dict, plans: dict) -> str:
    """'record' | 'remind' | 'skip' for a call."""
    if ev["id"] in plans:
        return plans[ev["id"]]
    return "record" if cfg.get("calendar", {}).get("auto_record") else "remind"


class CalendarCache:
    """Refreshes in the background so UI requests never block on Calendar/ICS."""

    def __init__(self, get_cfg, ttl: float = 120):
        self.get_cfg = get_cfg
        self.ttl = ttl
        self.events: list[dict] = []    # from the calendars you chose
        self.calendars: list[str] = []  # every calendar seen, for choosing
        self.error = ""
        self.updated = 0.0
        self._lock = threading.Lock()

    def refresh(self, force: bool = False, sync: bool = False) -> list[dict]:
        if not force and time.time() - self.updated < self.ttl:
            return self.events
        with self._lock:
            try:
                cfg = self.get_cfg()
                everything = fetch(cfg, all_calendars=True, sync=sync)
                self.calendars = sorted({e["calendar"] for e in everything if e.get("calendar")}, key=str.lower)
                self.events = watched(everything, cfg)
                self.error = ""
            except Exception as e:
                self.error = str(e)
            self.updated = time.time()
        return self.events

    def get(self, event_id: str) -> dict | None:
        return next((e for e in self.events if e["id"] == event_id), None)

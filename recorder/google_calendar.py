"""Read-only Google Calendar access using OAuth 2.0 with PKCE."""

import base64
import hashlib
import json
import secrets
from datetime import datetime, time, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, quote
from urllib.request import Request, urlopen


AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
CALENDAR_API = "https://www.googleapis.com/calendar/v3"
SCOPE = "openid email https://www.googleapis.com/auth/calendar.readonly"


class GoogleCalendarError(RuntimeError):
    pass


def new_state() -> str:
    return secrets.token_urlsafe(32)


def new_code_verifier() -> str:
    return secrets.token_urlsafe(64)


def code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def authorization_url(client_id: str, redirect_uri: str, state: str, verifier: str,
                      login_hint: str = "") -> str:
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": state,
        "code_challenge": code_challenge(verifier),
        "code_challenge_method": "S256",
    }
    if login_hint:
        params["login_hint"] = login_hint
    return f"{AUTH_URL}?{urlencode(params)}"


def _request_json(url: str, data: bytes | None = None, access_token: str = "") -> dict:
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    request = Request(url, data=data, headers=headers)
    try:
        with urlopen(request, timeout=20) as response:
            payload = response.read()
    except HTTPError as exc:
        try:
            body = json.loads(exc.read())
        except (json.JSONDecodeError, UnicodeDecodeError):
            body = {}
        detail = body.get("error_description") or body.get("error", "")
        raise GoogleCalendarError(f"Google returned HTTP {exc.code}" + (f": {detail}" if detail else "")) from exc
    except (TimeoutError, URLError) as exc:
        raise GoogleCalendarError(f"Couldn't reach Google: {exc}") from exc
    try:
        result = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise GoogleCalendarError("Google returned an invalid response.") from exc
    if not isinstance(result, dict):
        raise GoogleCalendarError("Google returned an invalid response.")
    return result


def exchange_code(client_id: str, client_secret: str, code: str, redirect_uri: str,
                  verifier: str) -> dict:
    data = {
        "client_id": client_id,
        "code": code,
        "code_verifier": verifier,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
    }
    if client_secret:
        data["client_secret"] = client_secret
    result = _request_json(TOKEN_URL, urlencode(data).encode())
    if not result.get("access_token"):
        raise GoogleCalendarError("Google did not return an access token.")
    return result


def refresh_access_token(client_id: str, client_secret: str, refresh_token: str) -> str:
    if not refresh_token:
        raise GoogleCalendarError("Google Calendar isn't connected. Sign in with Google in Settings.")
    data = {
        "client_id": client_id,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }
    if client_secret:
        data["client_secret"] = client_secret
    result = _request_json(TOKEN_URL, urlencode(data).encode())
    access_token = result.get("access_token")
    if not access_token:
        raise GoogleCalendarError("Google did not return an access token. Reconnect in Settings.")
    return access_token


def account_email(access_token: str) -> str:
    result = _request_json(USERINFO_URL, access_token=access_token)
    email = str(result.get("email") or "").strip()
    verified = result.get("email_verified", result.get("verified_email", False))
    if not email or not verified:
        raise GoogleCalendarError("Google didn't provide a verified email address.")
    return email


def _when(value: dict) -> tuple[datetime, bool]:
    if "dateTime" in value:
        text = str(value["dateTime"]).replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(text)
        except ValueError as exc:
            raise GoogleCalendarError("Google returned an invalid event time.") from exc
        return dt.astimezone(), False
    if "date" in value:
        try:
            day = datetime.strptime(value["date"], "%Y-%m-%d").date()
        except ValueError as exc:
            raise GoogleCalendarError("Google returned an invalid all-day event date.") from exc
        return datetime.combine(day, time.min).astimezone(), True
    raise GoogleCalendarError("Google returned an event without a start or end time.")


def event_from_google(item: dict, calendar_name: str) -> dict:
    start, all_day = _when(item.get("start") or {})
    end, _ = _when(item.get("end") or {})
    attendees = []
    for att in item.get("attendees") or []:
        email = str(att.get("email") or "")
        response = str(att.get("responseStatus") or "").lower()
        attendees.append({
            "name": str(att.get("displayName") or ""),
            "email": email,
            "is_me": bool(att.get("self")),
            "partstat": "DECLINED" if response == "declined" else response.upper(),
        })
    conference_url = str(item.get("hangoutLink") or "")
    if not conference_url:
        conference_url = next(
            (str(point.get("uri") or "") for point in (item.get("conferenceData") or {}).get("entryPoints", [])
             if point.get("entryPointType") == "video" and point.get("uri")),
            "",
        )
    event_id = str(item.get("id") or "")
    if not event_id:
        raise GoogleCalendarError("Google returned an event without an ID.")
    return {
        "id": f"google:{event_id}@{start.isoformat()}",
        "title": str(item.get("summary") or "(No title)"),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "all_day": all_day,
        "location": str(item.get("location") or ""),
        "url": conference_url,
        "notes": str(item.get("description") or "")[:4000],
        "calendar": calendar_name,
        "organizer": str((item.get("organizer") or {}).get("email") or ""),
        "attendees": attendees,
    }


def _list_items(url: str, access_token: str) -> list[dict]:
    items = []
    while url:
        page = _request_json(url, access_token=access_token)
        items.extend(page.get("items") or [])
        next_page = page.get("nextPageToken")
        if not next_page:
            break
        separator = "&" if "?" in url else "?"
        url = f"{url}{separator}{urlencode({'pageToken': next_page})}"
    return items


def _rfc3339(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def fetch_events(client_id: str, client_secret: str, refresh_token: str,
                 start: datetime, end: datetime) -> list[dict]:
    access_token = refresh_access_token(client_id, client_secret, refresh_token)
    calendars = _list_items(f"{CALENDAR_API}/users/me/calendarList?maxResults=250", access_token)
    events = []
    for calendar in calendars:
        calendar_id = str(calendar.get("id") or "")
        if not calendar_id or calendar.get("deleted"):
            continue
        name = str(calendar.get("summary") or calendar_id)
        params = urlencode({
            "timeMin": _rfc3339(start),
            "timeMax": _rfc3339(end),
            "singleEvents": "true",
            "orderBy": "startTime",
            "maxResults": "2500",
        })
        url = f"{CALENDAR_API}/calendars/{quote(calendar_id, safe='')}/events?{params}"
        for item in _list_items(url, access_token):
            if item.get("status") == "cancelled":
                continue
            events.append(event_from_google(item, name))
    return events

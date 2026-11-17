"""Local web UI + JSON API (127.0.0.1 only). Live updates go to the browser over Server-Sent Events."""

import asyncio
import contextlib
import ctypes
import json
import mimetypes
import os
import queue
import re
import shlex
import signal
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
from datetime import datetime, timedelta
from html import escape
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import markdown as md

from . import HELPER, ROOT, STATIC, ask, audio, calendar_src, config, google_calendar, integrations, live, meet, meetings, pipeline, prep, search, speakers, todos, transcribe
from .capture import CaptureError
from .detect import MicWatcher, Popup, is_browser
from .session import Session, finish_parts

LEAVE_GRACE = 6       # seconds every call app must stay off the mic (and the call stay quiet) to count as leaving
LEAVE_MAX_WAIT = 60   # ...but sound playing after you left (music, a video) only holds the stop back this long
LEAVE_QUIET = 30      # an app that lets go of the mic while you're muted (Teams): the call must be quiet this long
# A browser whose mic comes and goes mid-call (it let go and came back, or the sound devices just changed). Seen
# 2026-11-10: Meet fell back to a stuck built-in mic, and Chrome reopened it every ~10 s, letting go for 1–8 s
# each time, sometimes with the call's own sound cut to silence too, so a few quiet seconds proved nothing.
LEAVE_GRACE_UNSTEADY = 20      # ...then it must stay off the mic (and the call stay quiet) this long
LEAVE_MAX_WAIT_UNSTEADY = 180  # ...and sound only holds the stop back this long
UNSTEADY_FOR = 600             # a let-go-and-come-back keeps it cautious this long (a fixed mic stops counting)
MEET_LEAVE_GRACE = 3  # seconds the recorded Meet call's tab must stay hung up (or closed) to count as leaving
NEW_CALL_GAP = 3     # a call app off the mic at least this long, then on again for another calendar call: you moved on
JOIN_HOLD = 5         # seconds a call app must keep the mic for you to count as in its call (waiting screens take it 1–3 s)
PAGE_SETTLE = 15      # seconds the page's files must stay unchanged before open pages are told to pick them up
LIVE_WATCH = 15       # seconds a page's "I'm showing the live transcript" lasts (it repeats it every 10 s)


def page_version() -> tuple[str, bool]:
    """Which version of the page's files is on disk (when they last changed), and whether it has settled: an
    update is often written a file at a time, and open pages shouldn't reload halfway through one."""
    latest = max(f.stat().st_mtime_ns for f in STATIC.iterdir() if f.is_file())
    return str(latest), time.time() - latest / 1e9 >= PAGE_SETTLE


def log(message: str) -> None:
    """A timestamped line in the app log (~/Library/Logs/CallRecorder.log when run by the menu bar app)."""
    print(f"{datetime.now().astimezone():%Y-%m-%d %H:%M:%S %Z} {message}", flush=True)


class HTTPError(Exception):
    def __init__(self, status: int, message: str, hint: str = ""):
        super().__init__(message)
        self.status, self.message, self.hint = status, message, hint


def launch_claude_login() -> None:
    if sys.platform != "darwin":
        raise HTTPError(
            501,
            "Run ./callrec login in the VS Code terminal for this environment, then click Check again.",
        )
    login_script = ROOT / "scripts" / "claude-login.command"
    if not login_script.is_file():
        raise HTTPError(500, "The Claude sign-in launcher is missing.")
    command = f"/bin/bash {shlex.quote(str(login_script))}"
    applescript = f'tell application "Terminal" to do script {json.dumps(command)}'
    try:
        subprocess.run(["osascript", "-e", applescript], check=True)
    except (OSError, subprocess.CalledProcessError) as e:
        raise HTTPError(500, f"Couldn't open Terminal for Claude sign-in: {e}")


def notify(title: str, message: str) -> None:
    esc = lambda s: s.replace("\\", "\\\\").replace('"', '\\"')
    subprocess.run(["osascript", "-e",
                    f'display notification "{esc(message)}" with title "{esc(title)}" sound name "Glass"'],
                   capture_output=True)


def lower_priority() -> None:
    """Run this thread at macOS's "utility" priority, so its CPU work gives way to whatever you're doing."""
    try:
        ctypes.CDLL("/usr/lib/libSystem.B.dylib").pthread_set_qos_class_self_np(0x11, 0)  # QOS_CLASS_UTILITY
    except Exception:
        pass


def render_md(text: str) -> str:
    # Escape raw HTML: notes contain transcript text and model output, neither of which should run in the page.
    text = re.sub(r"(?m)^((?![ \t]|[-*+] |\d+\. ).+)\n(?=(?:[-*+]|\d+\.) )", "\\1\n\n", text)  # a list right under a line
    html = md.markdown(text.replace("<", "&lt;"), extensions=["extra", "sane_lists"])
    return html.replace("<li>[ ] ", '<li class="todo">').replace("<li>[x] ", '<li class="todo done">')


class App:
    def __init__(self, cfg: dict, port: int = 8765):
        self.cfg = cfg
        self.port = port
        self.session: Session | None = None
        self.lock = threading.RLock()
        self.clients: list[queue.Queue] = []
        self.live_seen = 0.0  # when a page last said it's showing the live transcript (time.monotonic)
        self.calendar = calendar_src.CalendarCache(lambda: self.cfg)
        self.google_oauth_pending: dict[str, dict] = {}
        self.google_oauth_lock = threading.Lock()
        self.jobs: dict[str, dict] = {}
        self.job_runs: dict[str, tuple[threading.Thread, threading.Event]] = {}  # processing threads, to call off
        self.prompted: set[str] = set()
        self.prompt: dict | None = None          # "meeting is starting — record?"
        self.over_prompted: set[str] = set()
        self.declined: set[str] = set()          # calls you said "Not now" to in a popup
        self.popup: Popup | None = None
        self.mic_handled: set[str] = set()       # call apps whose current use of the mic was already offered/recorded
        self.mic_free_since: float | None = time.monotonic()  # when the last call app let go of the mic (None: in use)
        self.on_call: dict[str, str] = {}        # call apps that used the mic during this recording
        self.mic_drops: list[tuple[float, float]] = []  # (when, seconds): a call app let go mid-call and came back
        self.mic_held = 0.0                      # how long a call app last held the mic before letting go
        self.device_changed = 0.0                # when this recording's sound output device last changed (monotonic)
        self.meet_code: str | None = None        # the Meet call (meeting code) this recording follows
        self.meet_hung_up = False                # ...and whether its tab has hung up or closed
        self.waiting: dict | None = None         # a calendar call to record as soon as you're in it (record_when_joined)
        self.mic_on_since: float | None = None   # when a call app took the mic (None: none has it)
        self.meet_in_call: tuple[str, float] | None = None  # the Meet call a tab last said you're in, and when
        self._recover()
        threading.Thread(target=self._watch, daemon=True).start()
        self.mic = MicWatcher(self._mic_changed)
        self.mic.start()

    def _recover(self):
        """Calls left mid-recording (crash, quit, logout) are finished and processed now; calls left
        mid-processing are only marked, so a call that crashes processing can't loop on every start.
        Calls deleted more than KEEP_DELETED_DAYS ago are removed for good."""
        if purged := meetings.purge_deleted(self.cfg):
            log(f"removed {purged} call{'s' if purged > 1 else ''} deleted over {meetings.KEEP_DELETED_DAYS} days ago")
        for item in meetings.list_all(self.cfg):
            status = item.get("status")
            meeting = meetings.get(self.cfg, item["id"])
            if status == "recording" or (status == "recorded" and meeting.meta().get("process_on_start")):
                finish_parts(meeting.path)  # a stretch recorded after a pause, or onto a finished call
            if status == "recorded" and meeting.meta().get("process_on_start"):  # stopped by a quit/logout
                meeting.update(process_on_start=False)
                self.process_async(meeting)
            elif status in ("recording", "processing", "transcribing", "summarizing"):
                seconds = max(audio.pcm_samples(meeting.path / f"{t}.pcm") for t in ("mic", "system")) / audio.SAMPLE_RATE
                meeting.update(status="recorded", step_message="",
                               duration=item.get("duration") or round(seconds, 1))
                if status == "recording" and seconds > 5:
                    self.process_async(meeting)

    # -- events to the browser ---------------------------------------------------------------------
    def broadcast(self, kind: str, data) -> None:
        for q in list(self.clients):
            try:
                q.put_nowait((kind, data))
            except queue.Full:
                pass

    def _session_event(self, kind: str, data) -> None:
        if kind == "capture_ended":
            message = data.get("message", "capture ended")
            self.broadcast("notice", {"level": "error", "message": f"Recording stopped: {message}"})
            session = self.session
            stderr = " | ".join(getattr(session.capture, "_stderr", [])[-5:]) if session else ""
            threading.Thread(target=self._stop_quietly,
                             args=(f"capture ended on its own: {message}" + (f" ({stderr})" if stderr else ""),),
                             daemon=True).start()
        elif kind == "source_restarted":
            log(f"{data.get('source')} audio restarted: {data.get('reason')}")
            if data.get("source") == "system" and "device changed" in str(data.get("reason")):
                self.device_changed = time.monotonic()  # a headset came or went: call apps reopen the mic
            if data.get("source") == "mic" and "couldn't restart yet" in str(data.get("reason")):
                self.broadcast("notice", {"level": "error", "message":
                    "No microphone is working, so your side of the call isn't being recorded. "
                    "Restarting your Mac usually frees a stuck microphone."})
        elif kind == "source_info":
            instead = data.get("instead_of")
            log(f"{data.get('source')} audio: following “{data.get('device')}”, {data.get('format')}"
                + (f", input channels {data['buffers']}" if "buffers" in data else "")
                + (f" — instead of “{instead}”, which isn't working" if instead else "")
                + ("" if data.get("readable") else " — CAN'T READ IT"))
            if instead:  # the helper moved on from a stuck or silent microphone
                self.broadcast("notice", {"level": "info", "message":
                    f"Your {instead} isn't working, so you're being recorded through the {data.get('device')}. "
                    "Restarting your Mac usually frees a stuck microphone."})
            if not data.get("readable"):
                self.broadcast("notice", {"level": "error", "message":
                    f"Can't read system audio while sound plays through “{data.get('device')}”, so only your "
                    "microphone is being recorded. Switching your Mac's sound output to another device may help."})
        else:
            self.broadcast(kind, data)

    def _stop_quietly(self, reason: str):
        try:
            self.stop_recording(reason)
        except HTTPError:
            pass

    # -- recording ---------------------------------------------------------------------------------
    def start_recording(self, title: str = "", event_id: str = "", meeting=None) -> dict:
        """Start recording a new call, or (`meeting`) record more onto a finished one."""
        with self.lock:
            if self.session:
                raise HTTPError(409, "Already recording.")
            if meeting:
                meta = meeting.meta()
                event, title, auto_title = meta.get("event"), meta.get("title") or meeting.id, False
            else:
                event = self.calendar.get(event_id) if event_id else None
                if not event and not title:
                    current, _ = calendar_src.current_and_next(self.calendar.events)
                    event = current if current and current.get("is_call") else None
                auto_title = not title and not event
                title = title or (event or {}).get("title") or f"Call {datetime.now():%-I:%M %p}"
            session = Session(self.cfg, title, event, self._session_event, auto_title=auto_title,
                              watching=lambda: time.monotonic() - self.live_seen < LIVE_WATCH, meeting=meeting)
            try:
                session.start()
            except CaptureError as e:
                raise HTTPError(500, str(e), e.hint)
            except (OSError, subprocess.CalledProcessError) as e:  # its saved audio couldn't be opened
                raise HTTPError(500, f"Couldn't open this call's recording to add to it ({e}).")
            # Notes written for this call beforehand (from Upcoming) are where its notes start.
            ahead = prep.for_events(self.cfg, self.calendar.events).get(event["id"]) if event and not meeting else None
            if ahead:
                session.meeting.my_notes_path.write_text(ahead["text"])
                prep.copied(self.cfg, ahead, session.meeting.id)
            self.session = session
            transcribe.ON_A_CALL.set()
            self.prompt = None
            was_waiting, self.waiting = self.waiting, None  # started now: nothing left to wait for
            if event:
                self.prompted.add(event["id"])
            self.mic_handled.update(self.mic.apps)  # this call is being recorded; don't offer it again
            self.on_call = dict(self.mic.apps)
            self.mic_drops, self.device_changed = [], 0.0
            self.meet_code, self.meet_hung_up = None, False
        self._close_popup()
        tracks = getattr(session.capture, "tracks", {})
        log((f"recording onto “{title}” again, from {transcribe.ts(session.elapsed())}" if meeting
             else f"recording started: “{title}”") + f" → {session.meeting.path.name}"
            + (f" (mic: {'on' if tracks.get('mic') else 'OFF'}, system audio: {'on' if tracks.get('system') else 'OFF'})"
               if tracks else ""))
        state = session.state()
        if was_waiting:
            self.broadcast("waiting", None)
        self.broadcast("recording_started", state)
        return state

    # -- recording once you're in the call -------------------------------------------------------------
    # A calendar call set to record, or one you said Record to in a popup, starts recording when you're in
    # the call itself (a call app has kept the microphone a few seconds, or the Meet extension says you're
    # in the call), not when it's due or when you click. A Record button on a page or in the menu starts at once.

    def _in_room(self) -> str | None:
        """What you're on a call in right now ("Chrome", "Google Meet"…), or None when you're not in one yet.
        Meet's waiting screen takes the microphone for a second or two, which doesn't count."""
        now = time.monotonic()
        if self.meet_in_call and now - self.meet_in_call[1] < 2 * meet.HEARTBEAT + 1:  # it reports every 5 s
            return "Google Meet"
        apps = self.mic.apps
        if apps and self.mic_on_since is not None and now - self.mic_on_since >= JOIN_HOLD - 0.1:
            return " / ".join(sorted(set(apps.values())))
        return None

    def record_when_joined(self, event: dict, auto: bool = False) -> dict:
        """Record `event` (a calendar call) from when you're in it: now if you already are, else as soon as you
        join. `auto`: it's set to record on its own (nobody clicked anything). Returns the recording's state,
        or {"waiting": …}."""
        with self.lock:
            if self.session:
                raise HTTPError(409, "Already recording.")
            self.prompted.add(event["id"])
            if self.prompt and self.prompt["id"] == event["id"]:
                self.prompt = None  # answered
                self.broadcast("prompt_cleared", {})
            where = self._in_room()
            if not where:
                self.waiting = {"event_id": event["id"], "title": event.get("title") or "the call",
                                "start": event.get("start"), "end": event["end"],
                                "conference_url": event.get("conference_url") or "", "auto": auto}
                waiting = self.waiting
        if where:
            return self._start_joined(event["id"], where, announce=auto)
        log(f"waiting for you to join “{waiting['title']}” to start recording")
        self.broadcast("waiting", waiting)
        return {"waiting": waiting}

    def _check_waiting(self) -> None:
        """Start the call that's waiting if you're in it now; stop waiting once it's over."""
        waiting = self.waiting
        if not waiting:
            return
        if datetime.now().astimezone() >= datetime.fromisoformat(waiting["end"]):
            with self.lock:
                if self.waiting is not waiting:
                    return
                self.waiting = None
            log(f"stopped waiting for “{waiting['title']}”: it ended and you never joined")
            self.broadcast("waiting", None)
            return
        where = self._in_room()
        if where:
            self._start_joined(waiting["event_id"], where, announce=True, only=waiting)

    def _start_joined(self, event_id: str, where: str, announce: bool, only: dict | None = None) -> dict | None:
        """Start recording the call you're in (`only`: if that's still the call waiting). `announce`: a popup
        says so, since it starts without a click."""
        with self.lock:  # one start, however many signals arrive at once
            if self.session or (only is not None and self.waiting is not only):
                return None
            try:
                state = self.start_recording(event_id=event_id)
            except HTTPError as e:
                self.waiting = None
                notify("Call Recorder", f"Couldn't start recording: {e.message}")
                self.broadcast("waiting", None)
                return None
        log(f"you're in the call ({where}), so recording started")
        if announce:
            self._show_popup(event_id, f"Recording “{state.get('title') or 'the call'}”",
                             f"You joined the call on {where}, so it's recording now.", ["OK", "Stop"],
                             lambda choice: choice == "Stop" and self._stop_quietly("stopped from the popup"),
                             timeout=12)
        return state

    def cancel_wait(self) -> None:
        """Don't record the call that's waiting for you to join (it isn't offered again)."""
        with self.lock:
            waiting, self.waiting = self.waiting, None
        if waiting:
            self.declined.add(waiting["event_id"])
            log(f"won't record “{waiting['title']}”: you said not to while it waited for you to join")
            self.broadcast("waiting", None)

    def pause_recording(self) -> dict:
        """Stop capturing for now; nothing is recorded until resume_recording()."""
        session = self.session
        if not session:
            raise HTTPError(409, "Not recording.")
        if session.pause():
            log(f"recording paused at {transcribe.ts(session.elapsed())}: “{session.meeting.meta().get('title')}”")
        state = session.state()
        self.broadcast("recording_paused", state)
        return state

    def resume_recording(self) -> dict:
        session = self.session
        if not session:
            raise HTTPError(409, "Not recording.")
        try:
            if session.resume():
                log(f"recording resumed at {transcribe.ts(session.elapsed())}: “{session.meeting.meta().get('title')}”")
        except CaptureError as e:
            raise HTTPError(500, str(e), e.hint)
        state = session.state()
        self.broadcast("recording_resumed", state)
        return state

    def stop_recording(self, reason: str = "stopped by you", only: Session | None = None) -> dict:
        """Stop the recording (`only`: just that one, if it's still the one going)."""
        with self.lock:
            session = self.session if only in (None, self.session) else None
            if session:
                self.session = None
                transcribe.ON_A_CALL.clear()
        if not session:
            raise HTTPError(409, "Not recording.")
        meeting = session.stop()
        log(f"recording stopped after {session.elapsed():.0f} s: {reason} → {meeting.path.name}")
        self.broadcast("recording_stopped", {"id": meeting.id})
        self.process_async(meeting)
        return {"id": meeting.id}

    def process_async(self, meeting) -> None:
        def progress(step, message):
            self.jobs[meeting.id] = {"step": step, "message": message}
            self.broadcast("job", {"id": meeting.id, "step": step, "message": message})

        cancelled = threading.Event()

        def run():
            lower_priority()  # notes can take a little longer; your next call can't
            try:
                pipeline.process(meeting, self.cfg, progress, cancelled)
                integrations.run_auto(meeting, self.cfg,
                                      log=lambda m: self.broadcast("notice", {"level": "info", "message": m}))
            except Exception:
                pass  # status/error already recorded on the meeting and broadcast (or it was called off)
            finally:
                if self.job_runs.get(meeting.id, (None,))[0] is threading.current_thread():  # not a newer run's
                    self.jobs.pop(meeting.id, None)
                    self.job_runs.pop(meeting.id, None)
                self.broadcast("meetings_changed", {})

        progress("processing", "Starting…")
        thread = threading.Thread(target=run, daemon=True)
        self.job_runs[meeting.id] = (thread, cancelled)
        thread.start()

    def cancel_processing(self, meeting, wait: float = 60) -> bool:
        """Stop writing `meeting`'s notes, so more can be recorded onto it (they're written again when that
        recording stops). Saving its audio always finishes first. Returns whether there was anything to stop."""
        run = self.job_runs.get(meeting.id)
        if not run:
            return False
        thread, cancelled = run
        cancelled.set()
        thread.join(wait)
        if thread.is_alive() and (self.jobs.get(meeting.id) or {}).get("step") == "processing":
            cancelled.clear()  # still saving the audio: recording onto it now could lose some, so carry on
            raise HTTPError(409, "Still saving this call's audio. Try again in a moment.")
        log(f"stopped writing notes for “{meeting.meta().get('title') or meeting.id}” to record more onto it")
        return True

    # -- calendar watcher --------------------------------------------------------------------------
    def _watch(self):
        while True:
            try:
                self._tick()
            except Exception:
                traceback.print_exc()
            time.sleep(15)

    def _tick(self):
        cal_cfg = self.cfg.get("calendar", {})
        if cal_cfg.get("source", "macos") == "off":
            return
        before = self.calendar.updated
        events = self.calendar.refresh()
        if self.calendar.updated != before:
            self.broadcast("calendar", self.calendar_state())
        now = datetime.now().astimezone()
        current, _ = calendar_src.current_and_next(events, now)

        if self.prompt and now >= datetime.fromisoformat(self.prompt["end"]):
            self._close_popup(self.prompt["id"])
            self.prompt = None  # the call is over
            self.broadcast("prompt_cleared", {})

        self._check_waiting()  # in case a join was missed, and to stop waiting once the call is over

        if not self.session and current and current.get("is_call") and current["id"] not in self.prompted:
            started = datetime.fromisoformat(current["start"])
            plan = calendar_src.effective_plan(current, self.cfg, calendar_src.load_plans())
            # `prompted` starts empty after a restart, so also skip calls that already have a recording.
            if now - started < timedelta(minutes=10) and plan != "skip" and not self._recorded(current["id"]):
                self.prompted.add(current["id"])
                if plan == "record":
                    try:
                        self.record_when_joined(current, auto=True)  # starts when you're in the call
                    except HTTPError as e:
                        notify("Call Recorder", f"Couldn't start recording: {e.message}")
                else:
                    self.prompt = current
                    self.broadcast("meeting_starting", current)
                    if cal_cfg.get("notify", True):
                        self._offer(current, f"“{current['title']}” is starting",
                                    " · ".join(filter(None, [_with(current), _conference(current)])))

        session = self.session
        if session and session.event:
            end = datetime.fromisoformat(session.event["end"])
            if now > end + timedelta(minutes=1):
                minutes = cal_cfg.get("auto_stop_silence_minutes", 3)
                if minutes and self._silent_for(session, minutes):
                    self.stop_recording(f"auto-stop: silent for {minutes} min after the scheduled end")
                    self.broadcast("notice", {"level": "info", "message": "Stopped recording — the call seems to be over."})
                    notify("Call Recorder", f"Stopped recording “{session.event['title']}” — the call seems to be over")
                elif session.event["id"] not in self.over_prompted:
                    self.over_prompted.add(session.event["id"])
                    self.broadcast("meeting_over", session.event)

    # -- popups -----------------------------------------------------------------------------------------
    def _show_popup(self, key: str, title: str, message: str, buttons: list[str], on_choice, timeout: float = 600,
                    sound: bool = True):
        with self.lock:
            if self.popup and self.popup.showing:
                if self.popup.key == key:
                    return
                self.popup.close()
            try:
                self.popup = Popup(key, title, message, buttons, on_choice, timeout, sound)
            except OSError:  # no helper: fall back to a plain notification
                notify("Call Recorder", f"{title} — {message}")

    def _close_popup(self, key: str | None = None):
        popup = self.popup
        if popup and popup.showing and (key is None or popup.key == key):
            popup.close()

    def _offer(self, event: dict | None, title: str, message: str, key: str = ""):
        """A "Record / Not now" popup for a call (a calendar event, or an app's call when there's none)."""
        key = key or event["id"]

        def chosen(choice):
            if choice == "Record":
                try:
                    if not event:  # a call app's call: it's on the mic, so you're in it
                        self.start_recording()
                    elif "waiting" in self.record_when_joined(event):
                        self._show_popup(f"wait:{event['id']}", f"Will record “{event['title']}”",
                                         "Recording starts as soon as you're in the call.", ["OK", "Don't record"],
                                         lambda choice: choice == "Don't record" and self.cancel_wait(),
                                         timeout=8, sound=False)
                except HTTPError as e:
                    notify("Call Recorder", f"Couldn't start recording: {e.message}")
            elif choice == "Not now":
                self.declined.add(key)

        self._show_popup(key, title, message, ["Record", "Not now"], chosen)

    def _mic_changed(self, apps: dict):
        """A call app started or stopped using the microphone."""
        session = self.session
        log(f"mic: {', '.join(sorted(set(apps.values()))) or 'no call app'}" + (" (recording)" if session else ""))
        self.mic_handled &= set(apps)  # an app that let go of the mic gets offered again next time
        popup = self.popup
        if popup and popup.showing and popup.key.startswith("mic:") and popup.key[4:] not in apps:
            popup.close()  # the call it was about is over
        if set(apps) - self.mic_handled:
            threading.Timer(3, self._consider_mic).start()  # ignore a mic that's only on for a moment
        if not apps:
            now = time.monotonic()
            self.mic_held = now - self.mic_on_since if self.mic_on_since is not None else 0.0
            self.mic_free_since, self.mic_on_since = now, None
            if session and self.on_call:
                threading.Timer(self._leave_grace(), self._left_call, args=(session, now)).start()
            return
        if self.mic_on_since is None:
            self.mic_on_since = time.monotonic()
            threading.Timer(JOIN_HOLD, self._check_waiting).start()  # still on the mic then: you're in the call
        released = self.mic_free_since
        free_for = time.monotonic() - released if released else 0
        self.mic_free_since = None
        if not session:
            return
        moved_to = self._new_call(session) if self.on_call and free_for >= NEW_CALL_GAP else None
        if moved_to and self._browser_call() and self._mic_unsteady(released) and self._heard_lately(session, free_for):
            log(f"mic: back after {free_for:.0f} s while “{moved_to['title']}” is starting, but the recorded call "
                "stayed audible the whole time, so it's the same call (the mic comes and goes)")
            moved_to = None
        if moved_to and self.cfg.get("stop_when_call_ends", True):
            threading.Thread(target=self._moved_on, args=(session, moved_to), daemon=True).start()
            return
        if (self.on_call and released and set(apps) & set(self.on_call)
                and self.mic_held >= JOIN_HOLD - 0.1):  # not a waiting screen's quick grab
            was_steady = not self._mic_unsteady()
            self.mic_drops.append((time.monotonic(), free_for))
            if was_steady and self._browser_call():
                log(f"mic: {' / '.join(sorted(set(apps.values())))} let go of the mic for {free_for:.0f} s mid-call "
                    f"and came back; until it's steady for {UNSTEADY_FOR // 60} min, leaving takes "
                    f"{LEAVE_GRACE_UNSTEADY} s off the mic")
        self.on_call.update(apps)  # rejoined, switched mics, or joined the call after recording started

    def _new_call(self, session: Session) -> dict | None:
        """The calendar call you moved on to, when a call app takes the mic again after letting go of it: another
        call that started in the last 10 minutes or starts within 5, while the recorded call is over or nearly.
        None means it's the same call (you rejoined it)."""
        now = datetime.now().astimezone()
        recorded = session.event
        if recorded and datetime.fromisoformat(recorded["end"]) > now + timedelta(minutes=5):
            return None
        for e in self.calendar.events:
            start = datetime.fromisoformat(e["start"])
            if (e.get("is_call") and not e.get("declined") and not e.get("all_day")
                    and not (recorded and e["id"] == recorded["id"])
                    and now - timedelta(minutes=10) <= start <= now + timedelta(minutes=5)):
                return e
        return None

    def _moved_on(self, session: Session, event: dict):
        """You left the recorded call and joined the next one on your calendar: stop now, then offer the new one."""
        self._stop_left(session, f"you left the call and joined “{event['title']}”", event)

    def _browser_call(self) -> bool:
        return bool(self.on_call) and all(is_browser(b) for b in self.on_call)

    def _mic_unsteady(self, released: float | None = None) -> str:
        """Why a call app letting go of the mic may not mean you left ("" when nothing says so): during this
        recording it let go and came back within the last UNSTEADY_FOR seconds, or the sound output device changed
        around when it let go (`released`), as a headset connecting or disconnecting makes call apps reopen the mic."""
        now = time.monotonic()
        recent = [gap for when, gap in self.mic_drops if now - when < UNSTEADY_FOR]
        if recent:
            return (f"it let go and came back {len(recent)}× in the last {UNSTEADY_FOR // 60} min, "
                    f"for up to {max(recent):.0f} s")
        if released and self.device_changed and self.device_changed >= released - 10:
            return "the sound device changed as it let go"
        return ""

    def _leave_grace(self) -> float:
        """How long every call app must stay off the mic before you count as gone (browsers; see _left_call)."""
        unsteady = self._browser_call() and self._mic_unsteady(self.mic_free_since)
        return LEAVE_GRACE_UNSTEADY if unsteady else LEAVE_GRACE

    def _left_call(self, session: Session, released: float):
        """Every call app has let go of the mic (at `released`), so you may have left the call. Once the other
        side is quiet too, the recording stops. In a browser (Meet keeps the mic while you're muted) a few quiet
        seconds will do, and sound that goes on past a minute is taken for something else playing; but when the
        browser's mic has been coming and going this call, it must stay off LEAVE_GRACE_UNSTEADY seconds, quiet
        all that time. A desktop app like Teams lets go of the mic while you're muted, so for it the call must go
        quiet for LEAVE_QUIET seconds, however long people keep talking: you're listening on mute, not gone."""
        if not self.cfg.get("stop_when_call_ends", True):
            return
        with self.lock:
            if (self.session is not session or not self.on_call or self.mic.apps
                    or self.mic_free_since is None or self.mic_free_since != released):
                return  # stopped already, or back on the mic (a later letting go has its own timer)
            browser = self._browser_call()
            unsteady = self._mic_unsteady(released) if browser else ""
            grace = LEAVE_GRACE_UNSTEADY if unsteady else LEAVE_GRACE
            free_for = time.monotonic() - released
            if free_for < grace - 0.1:  # it became unsteady after this timer was set (a device change)
                threading.Timer(grace - free_for, self._left_call, args=(session, released)).start()
                return
            if self._heard_lately(session, grace if browser else LEAVE_QUIET) \
                    and (not browser or free_for < (LEAVE_MAX_WAIT_UNSTEADY if unsteady else LEAVE_MAX_WAIT)):
                if not browser and free_for < LEAVE_GRACE * 1.5:  # once per letting go
                    log(f"{' / '.join(sorted(set(self.on_call.values())))} let go of the mic but the call is still "
                        f"audible (muted?): recording on until it's been quiet {LEAVE_QUIET} s")
                threading.Timer(LEAVE_GRACE, self._left_call, args=(session, released)).start()  # look again shortly
                return
            names = " / ".join(sorted(set(self.on_call.values())))
        self._stop_left(session, f"you left the {names} call"
                        + (f" (off the mic {free_for:.0f} s; {unsteady})" if unsteady else ""))

    def _stop_left(self, session: Session, why: str, event: dict | None = None):
        """Stop the recording because you left its call. Then, if you're already on another call (`event`, or a
        call app still on the mic), offer to record it; otherwise a popup says the recording stopped."""
        title = session.meeting.meta().get("title") or "the call"
        try:
            self.stop_recording(f"auto-stop: {why}", only=session)
        except HTTPError:
            return  # stopped already
        self.broadcast("notice", {"level": "info", "message": "Stopped recording — you left the call."})
        self.mic_handled -= set(self.on_call)  # a call app still on the mic is on another call now

        def after():
            if not self._consider_mic(event):
                self._show_popup(f"stopped:{session.meeting.id}", "Stopped recording",
                                 f"You left “{title}”, so its recording stopped.", ["OK"], lambda choice: None,
                                 timeout=8, sound=False)

        threading.Timer(3, after).start()

    # -- Google Meet (the Chrome extension) -----------------------------------------------------------------
    def meet_report(self, item: dict) -> bool:
        """A report from a Meet tab (see meet.py); whether it's from the call being recorded. The recording
        follows the first Meet call you're on while it runs. When that call's tab hangs up (its microphone off
        and the call's tiles gone) or closes, you've left the call, even if Chrome keeps the microphone for
        another Meet tab because you went straight on to your next call, which the mic alone can't tell."""
        code, session = item.get("code"), self.session
        if code and item.get("in_call") and not item.get("closed"):
            self.meet_in_call = (code, time.monotonic())
            if self.waiting and not session:  # you've joined the call that's waiting to be recorded
                threading.Thread(target=self._check_waiting, daemon=True).start()
        elif code and self.meet_in_call and self.meet_in_call[0] == code:
            self.meet_in_call = None  # you left it
        if not code or not session:
            return False
        # A tab only says its microphone is off (False, not None) once it has seen the page turn it on.
        hung_up = bool(item.get("closed")) or (not item.get("in_call") and item.get("mic") is False)
        with self.lock:
            if self.session is not session:
                return False
            if self.meet_code is None and item.get("in_call"):
                self.meet_code = code
                log(f"Meet: recording follows the call at meet.google.com/{code}")
            if code != self.meet_code:
                return False
            was, self.meet_hung_up = self.meet_hung_up, hung_up
        if hung_up and not was:
            threading.Timer(MEET_LEAVE_GRACE, self._left_meet, args=(session, code)).start()
        return True

    def _left_meet(self, session: Session, code: str):
        if not self.cfg.get("stop_when_call_ends", True):
            return
        with self.lock:
            if self.session is not session or self.meet_code != code or not self.meet_hung_up:
                return  # stopped already, or back in the call
        self._stop_left(session, "you left the Meet call", self._new_call(session))

    @staticmethod
    def _heard_lately(session: Session, seconds: float) -> bool:
        """Whether the other side (system audio) made any sound above ~-48 dBFS in the last few seconds."""
        if session.paused:
            return False  # nothing's being heard while paused
        path = session.meeting.path / "system.pcm"
        start = int((session.elapsed() - seconds) * audio.SAMPLE_RATE)  # nothing to read if the track stopped growing
        loud = audio.frame_rms(audio.read_pcm(path, max(0, start))) > 0.004 if path.exists() else []
        return sum(loud) * audio.FRAME / audio.SAMPLE_RATE >= 0.3

    def _consider_mic(self, event: dict | None = None) -> bool:
        """Offer to record the call using the mic: `event` (a call you just joined), else the calendar call on
        now, else an off-calendar call. Whether it offered."""
        if not self.cfg.get("detect_calls", True) or self.session:
            return False
        fresh = {b: n for b, n in self.mic.apps.items() if b not in self.mic_handled} or (self.mic.apps if event else {})
        if not fresh:
            return False
        bundle, name = next(iter(fresh.items()))
        self.mic_handled.update(fresh)
        current = event or calendar_src.current_and_next(self.calendar.events)[0]
        if current and current.get("is_call") and not current.get("declined"):
            plan = calendar_src.effective_plan(current, self.cfg, calendar_src.load_plans())
            if plan == "skip" or current["id"] in self.declined or self._recorded(current["id"]):
                return False
            if self.waiting and self.waiting["event_id"] == current["id"]:
                return True  # it starts by itself once you've been in the call a few seconds
            self.prompted.add(current["id"])
            if plan == "record":  # set to record: it starts when you're in the call, without asking
                try:
                    self.record_when_joined(current, auto=True)
                except HTTPError:
                    return False
                return True
            self._offer(current, f"Record “{current['title']}”?",
                        " · ".join(filter(None, [f"{name} is using your microphone", _with(current)])))
        else:
            self._offer(None, f"Record this {name} call?",
                        f"{name} is using your microphone. It's not on your calendar, so Claude will name it afterwards.",
                        key=f"mic:{bundle}")
        return True

    def _recorded(self, event_id: str) -> bool:
        return any(m.get("event_id") == event_id for m in meetings.list_all(self.cfg))

    @staticmethod
    def _silent_for(session: Session, minutes: float) -> bool:
        if session.elapsed() < minutes * 60:
            return False
        n = int(minutes * 60 * audio.SAMPLE_RATE)
        for track in ("system", "mic"):
            path = session.meeting.path / f"{track}.pcm"
            total = audio.pcm_samples(path)
            if total and audio.has_speech(audio.read_pcm(path, max(0, total - n))):
                return False
        return True

    def calendar_state(self) -> dict:
        plans = calendar_src.load_plans()
        recorded = {m["event_id"]: m["id"] for m in meetings.list_all(self.cfg) if m.get("event_id")}
        recording_event = ((self.session.event or {}).get("id") if self.session else None)
        ahead = prep.for_events(self.cfg, self.calendar.events)
        events = [{**e,
                   "my_notes": (ahead.get(e["id"]) or {}).get("text", ""),
                   "plan": calendar_src.effective_plan(e, self.cfg, plans) if e["is_call"] else None,
                   "plan_override": plans.get(e["id"]),
                   "meeting_id": recorded.get(e["id"]),
                   "recording_now": e["id"] == recording_event}
                  for e in self.calendar.events]
        current, upcoming = calendar_src.current_and_next(self.calendar.events)
        cal = self.cfg.get("calendar", {})
        return {"events": events, "current": current, "next": upcoming, "error": self.calendar.error,
                "calendars": self.calendar.calendars, "watching": cal.get("calendars") or [],
                "source": cal.get("source", "macos"), "days_ahead": cal.get("days_ahead", 7),
                "google_email": cal.get("google_email", ""),
                "google_connected": bool(config.get_secret("google_refresh_token")),
                "auto_record": bool(cal.get("auto_record"))}

    # -- views -------------------------------------------------------------------------------------
    def state(self) -> dict:
        return {
            "recording": self.session.state() if self.session else None,
            "jobs": self.jobs,
            "calendar": self.calendar_state(),
            "prompt": self.prompt,
            "waiting": self.waiting,
            "meetings": meetings.list_all(self.cfg),
            "settings": self.settings(),
        }

    def status(self) -> dict:
        """Small payload polled every second by the menu bar app."""
        session, prompt = self.session, self.prompt
        _, upcoming = calendar_src.current_and_next(self.calendar.events)
        return {
            "recording": {"title": session.meeting.meta().get("title"), "elapsed": session.elapsed(),
                          "paused": session.paused} if session else None,
            "jobs": len(self.jobs),
            "prompt": {"id": prompt["id"], "title": prompt["title"]} if prompt and not session else None,
            "next_call": {"title": upcoming["title"], "start": upcoming["start"]} if upcoming else None,
            "recordings_path": str(config.recordings_dir(self.cfg)),
        }

    def settings(self) -> dict:
        from .summarize import backend, cli_status
        return {**self.cfg, "secrets": config.secret_status(), "claude_backend_resolved": backend(self.cfg),
                "claude_login": cli_status(), "claude_login_terminal": sys.platform == "darwin",
                "recordings_path": str(config.recordings_dir(self.cfg))}

    def google_redirect_uri(self, host: str) -> str:
        host = host.lower()
        if host in {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}:
            return f"http://localhost:{self.port}/api/google/callback"
        codespace = os.environ.get("CODESPACE_NAME", "").lower()
        forwarding_domain = os.environ.get("GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN", "").lower()
        expected = f"{codespace}-{self.port}.{forwarding_domain}" if codespace and forwarding_domain else ""
        if expected and host == expected:
            return f"https://{host}/api/google/callback"
        raise HTTPError(400, "Google sign-in is only available from this app's local or Codespaces address.")

    def google_login(self, redirect_uri: str) -> dict:
        client_id = self.cfg.get("calendar", {}).get("google_client_id", "").strip()
        if not client_id:
            raise HTTPError(400, "Add your Google OAuth client ID in Settings first.")
        if not config.get_secret("google_client_secret"):
            raise HTTPError(400, "Add your Google OAuth client secret in Settings first.")
        with self.google_oauth_lock:
            cutoff = time.monotonic() - 600
            self.google_oauth_pending = {
                key: value for key, value in self.google_oauth_pending.items() if value["created"] > cutoff
            }
            state, verifier = google_calendar.new_state(), google_calendar.new_code_verifier()
            self.google_oauth_pending[state] = {
                "verifier": verifier, "redirect_uri": redirect_uri, "created": time.monotonic(),
            }
        return {"url": google_calendar.authorization_url(
            client_id, redirect_uri, state, verifier, "ben20russell@gmail.com")}

    def google_callback(self, query: dict, host: str) -> tuple[str, bool]:
        state = query.get("state", [""])[0]
        with self.google_oauth_lock:
            pending = self.google_oauth_pending.pop(state, None)
        if not state or not pending or time.monotonic() - pending["created"] > 600:
            return "This Google sign-in expired or could not be verified. Close this window and try again.", False
        error = query.get("error", [""])[0]
        if error:
            return f"Google sign-in was not completed: {escape(error)}. You can close this window.", False
        code = query.get("code", [""])[0]
        if not code:
            return "Google did not return an authorization code. Close this window and try again.", False
        try:
            redirect_uri = pending["redirect_uri"]
            if redirect_uri != self.google_redirect_uri(host):
                raise google_calendar.GoogleCalendarError("The sign-in address changed. Start again from Settings.")
            tokens = google_calendar.exchange_code(
                self.cfg.get("calendar", {}).get("google_client_id", "").strip(),
                config.get_secret("google_client_secret"), code, redirect_uri, pending["verifier"],
            )
            email = google_calendar.account_email(tokens["access_token"])
            if email.casefold() != "ben20russell@gmail.com":
                raise google_calendar.GoogleCalendarError(
                    f"Signed in as {email}, not ben20russell@gmail.com. Choose the requested Google account and try again.")
            refresh_token = tokens.get("refresh_token") or config.get_secret("google_refresh_token")
            if not refresh_token:
                raise google_calendar.GoogleCalendarError(
                    "Google didn't return a refresh token. Remove this app from your Google Account permissions and try again.")
            config.set_secret("google_refresh_token", refresh_token)
            self.cfg = config.update({"calendar": {"source": "google", "google_email": email}})
            self.calendar.refresh(force=True)
            self.broadcast("calendar", self.calendar_state())
            return f"Google Calendar connected as {escape(email)}. You can close this window.", True
        except (google_calendar.GoogleCalendarError, OSError, RuntimeError, subprocess.SubprocessError) as e:
            return f"Couldn't connect Google Calendar: {escape(str(e))}", False

    @staticmethod
    def can_record_onto(meeting) -> bool:
        """Whether a finished call has its own recording (not imported) that more can be added to."""
        if meeting.meta().get("transcript_source"):
            return False
        return any((meeting.path / f"{t}.{ext}").exists() for t in ("mic", "system") for ext in ("m4a", "pcm"))

    @staticmethod
    def save_transcript(meeting) -> Path:
        """Write `meeting`'s transcript to ~/Downloads as Markdown, named like "<title> - Nov 6 2026 - Transcript.md"
        (" (2)" and on when that's taken). Saved here because the app window can't save downloads itself."""
        segments = meeting.transcript() or [{**s, "speaker": s.get("speaker") or ""} for s in live.load_live(meeting)]
        if not segments:
            raise HTTPError(404, "This call has no transcript yet.")
        meta = meeting.meta()
        started = datetime.fromisoformat(meta["started_at"]) if meta.get("started_at") else datetime.now()
        title = re.sub(r"\s+", " ", re.sub(r"[/:\\]", "", meta.get("title") or meeting.id)).strip().lstrip(".")[:150] or "Call"
        stem = f"{title} - {started:%b %-d %Y} - Transcript"
        folder = Path.home() / "Downloads"
        path, n = folder / f"{stem}.md", 2
        while path.exists():
            path, n = folder / f"{stem} ({n}).md", n + 1
        path.write_text(pipeline.render_transcript_file(meta, meeting.summary() or {}, segments))
        log(f"saved the transcript of “{meta.get('title') or meeting.id}” → {path}")
        return path

    def meeting_detail(self, meeting) -> dict:
        meta = meeting.meta()
        notes = meeting.read(meeting.notes_path, "")
        # The page header already shows title/date/people, so start the rendered notes at the summary.
        body = notes.split("\n## Transcript\n")[0]
        if "\n## Summary\n" in body:
            body = body[body.index("\n## Summary\n") + 1:]
        return {
            "meta": meta,
            "summary": meeting.summary(),
            "notes_html": render_md(body) if body else "",
            "notes_md": notes,
            "transcript": transcribe.paragraphs(meeting.transcript()) or live.load_live(meeting),
            "speakers": [{k: v for k, v in sp.items() if k != "embedding"}
                         for sp in speakers.load(meeting).get("speakers", [])],
            "questions": [{**q, "html": render_md(ask.link_calls(q["a"]))} for q in ask.questions(meeting)],
            "my_notes": meeting.my_notes(),
            "has_audio": (meeting.path / "call.m4a").exists(),
            "can_record": self.can_record_onto(meeting),
            "job": self.jobs.get(meeting.id),
            "path": str(meeting.path),
        }


class Handler(BaseHTTPRequestHandler):
    app: App
    port: int
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # quiet
        pass

    # -- plumbing ----------------------------------------------------------------------------------
    def _check_host(self) -> bool:
        host = self.headers.get("Host", "")
        allowed = {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}
        codespace = os.environ.get("CODESPACE_NAME", "").lower()
        forwarding_domain = os.environ.get("GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN", "").lower()
        if codespace and forwarding_domain:
            allowed.add(f"{codespace}-{self.port}.{forwarding_domain}")
        if host.lower() not in allowed:
            self.send_error(HTTPStatus.FORBIDDEN, "Bad host")
            return False
        return True

    def _json(self, data, status: int = 200):
        body = json.dumps(data, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length) or b"{}") if length else {}

    def _meeting(self, meeting_id: str):
        meeting = meetings.get(self.app.cfg, meeting_id)
        if not meeting:
            raise HTTPError(404, "No such meeting")
        return meeting

    def do_GET(self):
        if self._check_host():
            self._dispatch("GET")

    def do_POST(self):
        if not self._check_host():
            return
        if self.headers.get("X-CallRec") != "1":  # blocks cross-site form posts
            self.send_error(HTTPStatus.FORBIDDEN, "Missing X-CallRec header")
            return
        self._dispatch("POST")

    def _dispatch(self, method: str):
        url = urlparse(self.path)
        path = url.path
        try:
            if method == "GET" and path == "/api/google/callback":
                message, connected = self.app.google_callback(parse_qs(url.query), self.headers.get("Host", ""))
                post_message = json.dumps({
                    "type": "callrec-google-oauth", "ok": connected, "message": message,
                }).replace("<", "\\u003c")
                payload = (
                    "<!doctype html><meta charset=utf-8><title>Google Calendar</title>"
                    "<main style=\"font:16px system-ui;max-width:36rem;margin:12vh auto;padding:24px\">"
                    f"<h1>{'Connected' if connected else 'Sign-in not completed'}</h1><p>{message}</p>"
                    "<p>You can close this window and return to Call Recorder.</p></main>"
                    "<script>if(window.opener){window.opener.postMessage("
                    f"{post_message},location.origin);if({str(connected).lower()})window.close();}}</script>"
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'unsafe-inline'")
                self.end_headers()
                self.wfile.write(payload)
                return
            if method == "GET" and (path == "/" or path.startswith("/static/")):
                return self._static("index.html" if path == "/" else path[len("/static/"):])
            if method == "GET" and path == "/api/stream":
                return self._stream()
            if method == "POST" and path == "/api/ask":
                return self._ask()
            m = re.fullmatch(r"/api/meetings/([^/]+)/audio", path)
            if method == "GET" and m:
                return self._audio(self._meeting(_unquote(m.group(1))))
            result = self._api(method, path, parse_qs(url.query))
            self._json(result)
        except HTTPError as e:
            self._json({"error": e.message, "hint": e.hint}, e.status)
        except (integrations.IntegrationError, calendar_src.CalendarError) as e:
            self._json({"error": str(e)}, 400)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            traceback.print_exc()
            self._json({"error": str(e)}, 500)

    def _api(self, method: str, path: str, query: dict):
        app = self.app
        if method == "GET":
            if path == "/api/state":
                return app.state()
            if path == "/api/status":
                return app.status()
            if path == "/api/record/live":
                if not app.session:
                    return {"segments": [], "notes": ""}
                return {"segments": live.load_live(app.session.meeting), "notes": app.session.meeting.my_notes()}
            if path == "/api/meetings":
                return meetings.list_all(app.cfg)
            if path == "/api/mcp":  # Claude's tools, for the Claude desktop extension (see extension/)
                from . import mcp_server
                tools = asyncio.run(mcp_server.server.list_tools())
                return {"instructions": mcp_server.server.instructions or "",
                        "tools": [t.model_dump(exclude_none=True, by_alias=True) for t in tools]}
            if path == "/api/searches":  # recent searches, newest first, with Claude's answers as HTML
                return [{**i, "html": render_md(ask.link_calls(i["answer"])) if i.get("answer") else ""}
                        for i in search.recent()]
            if path == "/api/search":
                arg = lambda name: query.get(name, [""])[0].strip()
                try:
                    return search.search(app.cfg, arg("q"), since=arg("since"), until=arg("until"), person=arg("person"))
                except search.SearchError as e:
                    raise HTTPError(400, str(e))
            m = re.fullmatch(r"/api/meetings/([^/]+)", path)
            if m:
                return app.meeting_detail(self._meeting(_unquote(m.group(1))))
            if path == "/api/settings":
                return app.settings()
            if path == "/api/google/redirect-uri":
                return {"redirect_uri": app.google_redirect_uri(self.headers.get("Host", ""))}
            if path == "/api/todos":  # Live To-Dos: ?days=14 (empty: every call)
                days = query.get("days", ["14"])[0]
                return todos.collect(app.cfg, int(days) if days.isdigit() else None)
            if path == "/api/meet":  # whether the Chrome extension for Google Meet has been heard from
                return meet.connection()
            if path == "/api/permissions":
                out = subprocess.run([str(HELPER), "permissions"], capture_output=True, text=True, timeout=20).stdout
                return json.loads(out)
            if path == "/api/claude/status":
                from .summarize import cli_status
                return cli_status()
            if path == "/api/mics":
                out = subprocess.run([str(HELPER), "list-mics"], capture_output=True, text=True, timeout=20).stdout
                return json.loads(out)["mics"]
            raise HTTPError(404, "Not found")

        body = self._body()
        if path == "/api/google/login":
            redirect_uri = app.google_redirect_uri(self.headers.get("Host", ""))
            return app.google_login(redirect_uri)
        if path == "/api/google/disconnect":
            config.set_secret("google_refresh_token", "")
            app.cfg = config.update({"calendar": {
                "google_email": "",
                "source": (
                    "macos" if app.cfg.get("calendar", {}).get("source") == "google"
                    else app.cfg.get("calendar", {}).get("source", "macos")
                ),
            }})
            app.calendar.refresh(force=True)
            return {"ok": True}
        if path == "/api/record/start":
            if body.get("when_joined"):  # a "call is starting" Record button: from when you're in the call
                event = app.calendar.get(str(body.get("event_id", "")))
                if not event:
                    raise HTTPError(404, "That call isn't on your calendar any more.")
                return app.record_when_joined(event)
            return app.start_recording(body.get("title", ""), body.get("event_id", ""))
        if path == "/api/record/wait/cancel":  # don't record the call waiting for you to join
            app.cancel_wait()
            return {"ok": True}
        if path == "/api/record/stop":
            return app.stop_recording()
        if path == "/api/searches":  # {"q"}: a search you made, for the recent ones
            search.remember(str(body.get("q", "")))
            return {"ok": True}
        if path == "/api/searches/forget":  # {"q"}: drop one; {"all": true}: drop them all
            search.forget(None if body.get("all") else str(body.get("q", "")))
            return {"ok": True}
        if path == "/api/record/pause":
            return app.pause_recording()
        if path == "/api/record/resume":
            return app.resume_recording()
        if path == "/api/record/notes":
            if not app.session:
                raise HTTPError(409, "Not recording.")
            app.session.meeting.my_notes_path.write_text(body.get("text", ""))
            return {"ok": True}
        if path == "/api/record/title":
            if not app.session:
                raise HTTPError(409, "Not recording.")
            app.session.meeting.update(title=body.get("title", "").strip() or "Call", auto_title=False)
            return {"ok": True}
        if path == "/api/meet":  # the Chrome extension: names on a Meet call and whose tile shows them talking
            session = app.session
            # Only the recorded call's tab goes in its timeline, and nothing while it's paused (no audio to match).
            ours = session if app.meet_report(body) and not session.paused else None
            meet.report(ours.meeting if ours else None, ours.clock.timestamp() if ours else None, body)
            return {"recording": bool(session)}
        if path == "/api/todos/done":  # tick a to-do off (or back on)
            todos.set_done(str(body.get("key", "")), bool(body.get("done")))
            app.broadcast("todos_changed", {"key": body.get("key"), "done": bool(body.get("done"))})
            return {"ok": True}
        if path == "/api/meet/reveal":
            meet.reveal()
            return {"ok": True}
        if path == "/api/mcp/call":  # run one of Claude's tools for the desktop extension
            from . import mcp_server
            result = asyncio.run(mcp_server.server.call_tool(body.get("name", ""), body.get("arguments") or {}))
            return result.model_dump(exclude_none=True, by_alias=True)
        if path == "/api/permissions/request":
            # Shows any unanswered macOS prompts (they belong to Call Recorder when it runs as the app).
            out = subprocess.run([str(HELPER), "request-permissions"], capture_output=True, text=True, timeout=180).stdout
            return json.loads(out.strip().splitlines()[-1])
        if path == "/api/claude/login":
            # Install Claude Code if needed, then sign in interactively with a Claude account.
            launch_claude_login()
            return {"ok": True}
        if path == "/api/prompt/dismiss":
            app.prompt = None
            return {"ok": True}
        if path == "/api/live/watching":  # {"on": bool}: whether the page is showing the live transcript
            app.live_seen = time.monotonic() if body.get("on") else 0.0
            return {"ok": True}
        if path == "/api/calendar/refresh":  # {"sync": true}: the Refresh button, which also has Calendar sync first
            app.calendar.refresh(force=True, sync=bool(body.get("sync")))
            state = app.calendar_state()
            app.broadcast("calendar", state)
            return state
        if path == "/api/upcoming/notes":  # {"id": event id, "text": markdown}: notes written ahead of a call
            event = app.calendar.get(str(body.get("id", "")))
            if not event:
                raise HTTPError(404, "That call isn't on your calendar any more.")
            text = str(body.get("text", ""))
            prep.save(app.cfg, event, text, prep.for_events(app.cfg, app.calendar.events).get(event["id"]))
            session = app.session
            if session and (session.event or {}).get("id") == event["id"]:  # it started while you were writing
                session.meeting.my_notes_path.write_text(text)
            return {"ok": True}
        if path == "/api/calendar/plan":
            calendar_src.set_plan(body["id"], body.get("plan", ""))
            state = app.calendar_state()
            app.broadcast("calendar", state)
            return state
        if path == "/api/settings":
            for name, value in (body.pop("secrets", None) or {}).items():
                if value is not None:
                    config.set_secret(name, value.strip())
            changes = {k: v for k, v in body.items() if k in config.DEFAULTS}
            app.cfg = config.update(changes)
            if "calendar" in changes:
                app.calendar.refresh(force=True)
            return app.settings()
        m = re.fullmatch(r"/api/meetings/([^/]+)/restore", path)
        if m:  # undo a delete (the call is in Recently Deleted, so not found the usual way)
            try:
                meeting = meetings.restore(app.cfg, _unquote(m.group(1)))
            except KeyError:
                raise HTTPError(404, "That call isn't in Recently Deleted any more.")
            except FileExistsError:
                raise HTTPError(409, "A call with the same name is already there.")
            log(f"restored “{meeting.meta().get('title')}” from {meetings.DELETED}")
            app.broadcast("meetings_changed", {})
            return {"ok": True, "id": meeting.id}
        m = re.fullmatch(r"/api/meetings/([^/]+)/(\w[\w-]*)", path)
        if m:
            meeting, action = self._meeting(_unquote(m.group(1))), m.group(2)
            if action == "delete":
                if app.session and app.session.meeting.id == meeting.id:
                    raise HTTPError(409, "Stop the recording before deleting it.")
                if meeting.id in app.jobs:
                    raise HTTPError(409, "Wait until the call has finished processing.")
                title = meeting.meta().get("title") or meeting.id
                meetings.delete(app.cfg, meeting.id)
                log(f"deleted “{title}” → {meetings.DELETED}")
                app.broadcast("meetings_changed", {})
                return {"ok": True}
            if action == "record":  # record more onto a finished call: what's said now is added to its end
                # {"stop_current": true}: stop the recording that's going first. Notes still being written for
                # this call are called off; they're written again, with the new part, when this recording stops.
                if app.session and app.session.meeting.id == meeting.id:
                    raise HTTPError(409, "This call is being recorded already.")
                if not app.can_record_onto(meeting):
                    raise HTTPError(400, "This call has no recording to add to.")
                if app.session:
                    if not body.get("stop_current"):
                        raise HTTPError(409, "Stop the recording that's going first.")
                    try:
                        app.stop_recording("stopped to record more onto an earlier call")
                    except HTTPError:
                        pass  # it had just stopped on its own
                interrupted = app.cancel_processing(meeting)
                try:
                    return app.start_recording(meeting=meeting)
                except Exception:
                    if interrupted:
                        app.process_async(meeting)  # couldn't record onto it after all: finish its notes
                    raise
            if action == "process":
                if meeting.id in app.jobs:
                    raise HTTPError(409, "Already processing.")
                app.process_async(meeting)
                return {"ok": True}
            if action == "apple-notes":
                return {"note_id": integrations.save_to_apple_notes(meeting, app.cfg)}
            if action == "transcript-file":  # Download transcript: a Markdown file in Downloads, shown in Finder
                path = app.save_transcript(meeting)
                subprocess.run(["open", "-R", str(path)])
                return {"path": str(path), "name": path.name}
            if action == "reveal":
                subprocess.run(["open", "-R", str(meeting.notes_path if meeting.notes_path.exists() else meeting.path)])
                return {"ok": True}
            if action == "questions":  # {"clear": true}: forget the questions asked about this call
                if body.get("clear"):
                    ask.clear_questions(meeting)
                return {"ok": True}
            if action == "my-notes":
                meeting.my_notes_path.write_text(body.get("text", ""))
                return {"ok": True}
            if action == "title":
                meeting.update(title=body.get("title", "").strip() or meeting.meta().get("title"), auto_title=False)
                return {"ok": True}
            if action == "speakers":  # name (or un-name) one voice on the call
                if meeting.id in app.jobs:
                    raise HTTPError(409, "Wait until the call has finished processing.")
                try:
                    speakers.rename(meeting, body.get("id", ""), body.get("name", ""), app.cfg)
                except KeyError:
                    raise HTTPError(404, "No such speaker on this call.")
                app.broadcast("meetings_changed", {})
                return app.meeting_detail(meeting)
        raise HTTPError(404, "Not found")

    def _static(self, name: str):
        file = (STATIC / name).resolve()
        if STATIC.resolve() not in file.parents or not file.is_file():
            raise HTTPError(404, "Not found")
        data = file.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(file.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def _audio(self, meeting):
        file = meeting.path / "call.m4a"
        if not file.exists():
            raise HTTPError(404, "No audio yet")
        size = file.stat().st_size
        start, end = 0, size - 1
        rng = re.fullmatch(r"bytes=(\d*)-(\d*)", self.headers.get("Range", ""))
        if rng:
            start = int(rng.group(1) or 0)
            end = min(int(rng.group(2) or size - 1), size - 1)
        with open(file, "rb") as f:
            f.seek(start)
            data = f.read(end - start + 1)
        self.send_response(206 if rng else 200)
        self.send_header("Content-Type", "audio/mp4")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(len(data)))
        if rng:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        self.wfile.write(data)

    def _stream(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        q: queue.Queue = queue.Queue(maxsize=500)
        self.app.clients.append(q)
        # The page's version: told when it connects, and again when its files change, so it can pick up an update.
        page, checked = page_version()[0], time.monotonic()
        try:
            self.wfile.write(f": hello\n\nevent: page\ndata: {json.dumps({'version': page})}\n\n".encode())
            self.wfile.flush()
            while True:
                try:
                    kind, data = q.get(timeout=15)
                    payload = f"event: {kind}\ndata: {json.dumps(data, default=str)}\n\n"
                except queue.Empty:
                    payload = ": ping\n\n"
                if time.monotonic() - checked >= 2:
                    checked = time.monotonic()
                    version, settled = page_version()
                    if settled and version != page:
                        page = version
                        payload += f"event: page\ndata: {json.dumps({'version': page})}\n\n"
                self.wfile.write(payload.encode())
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            self.app.clients.remove(q)
            self.close_connection = True


    def _ask(self):
        """Claude's answer to a question about your calls, streamed as JSON lines: what it's doing, the
        answer as it's written, then the finished answer (with HTML). With "call_id", about that call only
        ("history": the earlier questions on it), and the answer is kept with the call."""
        body = self._body()
        question = (body.get("question") or "").strip()
        if not question:
            raise HTTPError(400, "Type a question first.")
        meeting = self._meeting(body["call_id"]) if body.get("call_id") else None
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

        def send(event: dict):
            self.wfile.write((json.dumps(event) + "\n").encode())
            self.wfile.flush()

        try:
            events = ask.ask_call(question, self.app.cfg, meeting, body.get("history") or []) if meeting \
                else ask.ask(question, self.app.cfg)
            with contextlib.closing(events):  # closing stops Claude if you leave
                for event in events:
                    if event["type"] == "done":
                        event["html"] = render_md(ask.link_calls(event["answer"]))
                        if meeting:
                            ask.save_question(meeting, question, event["answer"])
                        else:
                            search.remember(question, event["answer"])  # with the recent searches
                    send(event)
        except ask.AskError as e:
            send({"type": "error", "message": str(e)})


def _with(event: dict) -> str:
    names = [a.get("name") or a.get("email", "") for a in event.get("others", [])]
    names = [n for n in names if n]
    if not names:
        return ""
    return "with " + ", ".join(names[:2]) + (f" +{len(names) - 2}" if len(names) > 2 else "")


def _conference(event: dict) -> str:
    url = (event.get("conference_url") or "").lower()
    for part, name in (("zoom", "Zoom"), ("teams", "Teams"), ("meet.google", "Google Meet"), ("webex", "Webex"),
                       ("facetime", "FaceTime"), ("slack", "Slack")):
        if part in url:
            return name
    return ""


def _unquote(s: str) -> str:
    from urllib.parse import unquote
    return unquote(s)


def serve(cfg: dict, port: int = 8765, open_browser: bool = True) -> None:
    # Claim the port before anything else, so a second copy exits without touching any recordings.
    handler = type("BoundHandler", (Handler,), {"port": port})
    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    except OSError:
        raise SystemExit(f"Call Recorder is already running on port {port} (http://127.0.0.1:{port}/).")
    server.daemon_threads = True
    app = handler.app = App(cfg, port)
    url = f"http://127.0.0.1:{port}/"
    print(f"Call Recorder running at {url}  (Ctrl-C to quit)")
    threading.Thread(target=app.calendar.refresh, kwargs={"force": True}, daemon=True).start()
    try:
        meet.install()  # the Chrome extension's folder, kept current
    except OSError as e:
        log(f"couldn't update the Chrome extension folder: {e}")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()

    def on_term(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, on_term)
    signal.signal(signal.SIGINT, signal.default_int_handler)  # even if started with SIGINT ignored
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if app.session:
            print("Stopping the active recording…")
            session, app.session = app.session, None
            meeting = session.stop()
            meeting.update(process_on_start=True)
            log(f"recording stopped after {session.elapsed():.0f} s: Call Recorder quit → {meeting.path.name}")
            print(f"Saved; it will be transcribed the next time Call Recorder starts. "
                  f"(Or now: ./callrec process \"{meeting.id}\")")
        server.server_close()

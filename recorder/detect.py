"""Noticing calls: which call apps are using the microphone right now (Zoom, Teams, Meet in a browser…),
and the desktop popup that offers to record. Both are drawn/read by the capture helper, which needs no extra
permission for either."""

import json
import subprocess
import threading
import time

from . import HELPER

# App bundle id (or its start) → how to call it. Browsers count because Meet, Zoom and Teams run in them.
CALL_APPS = {
    "us.zoom.xos": "Zoom",
    "com.microsoft.teams": "Teams",           # also com.microsoft.teams2 (new Teams)
    "com.google.Chrome": "Chrome",
    "com.apple.Safari": "Safari",
    "com.microsoft.edgemac": "Edge",
    "company.thebrowser.Browser": "Arc",
    "com.brave.Browser": "Brave",
    "org.mozilla.firefox": "Firefox",
    "com.tinyspeck.slackmacgap": "Slack",
    "com.apple.FaceTime": "FaceTime",
    "com.cisco.webexmeetingsapp": "Webex",
    "Cisco-Systems.Spark": "Webex",
    "com.hnc.Discord": "Discord",
    "net.whatsapp.WhatsApp": "WhatsApp",
    "desktop.WhatsApp": "WhatsApp",
}


# Browsers keep the microphone while you're muted on a call (Meet does). Desktop call apps can let go of it while
# you're muted (Teams does), so for them the mic going quiet doesn't mean you left.
BROWSERS = ("com.google.Chrome", "com.apple.Safari", "com.microsoft.edgemac", "company.thebrowser.Browser",
            "com.brave.Browser", "org.mozilla.firefox")


def is_browser(bundle: str) -> bool:
    return bundle.startswith(BROWSERS)


def call_app(user: dict) -> tuple[str, str] | None:
    """(bundle id, name) when a process using the mic belongs to a call app."""
    for bundle in (user.get("app_bundle") or "", user.get("bundle") or ""):
        for prefix, name in CALL_APPS.items():
            if bundle.startswith(prefix):
                return bundle.split(".helper")[0], name
    return None


class MicWatcher(threading.Thread):
    """Keeps `apps`, the call apps using the microphone ({bundle: name}), and calls on_change when it changes."""

    def __init__(self, on_change):
        super().__init__(daemon=True)
        self.on_change = on_change
        self.apps: dict[str, str] = {}

    def run(self):
        while True:
            try:
                proc = subprocess.Popen([str(HELPER), "watch-mic"], stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, text=True)
                for line in proc.stdout:
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    apps = dict(filter(None, (call_app(u) for u in event.get("apps", []))))
                    if apps != self.apps:
                        self.apps = apps
                        self.on_change(apps)
            except OSError:
                pass  # the helper isn't built; try again later
            time.sleep(10)


class Popup:
    """A desktop popup (`callrec-capture popup`): top right, over other apps, without taking focus.
    on_choice gets the button clicked, "timeout" or "closed"."""

    def __init__(self, key: str, title: str, message: str, buttons: list[str], on_choice,
                 timeout: float = 600, sound: bool = True):
        self.key = key
        cmd = [str(HELPER), "popup", "--title", title, "--message", message, "--timeout", str(int(timeout))]
        for button in buttons:
            cmd += ["--button", button]
        if sound:
            cmd.append("--sound")
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, text=True)
        threading.Thread(target=self._wait, args=(on_choice,), daemon=True).start()

    def _wait(self, on_choice):
        choice = "closed"
        for line in self.proc.stdout:
            try:
                choice = json.loads(line).get("choice", choice)
            except json.JSONDecodeError:
                pass
        self.proc.wait()
        on_choice(choice)

    @property
    def showing(self) -> bool:
        return self.proc.poll() is None

    def close(self):
        try:
            self.proc.stdin.write("close\n")
            self.proc.stdin.flush()
        except (BrokenPipeError, ValueError, OSError):
            pass

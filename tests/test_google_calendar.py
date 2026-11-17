import base64
import hashlib
import os
import threading
import time
import unittest
from datetime import datetime
from urllib.parse import parse_qs, urlparse
from unittest.mock import Mock, patch

from recorder import google_calendar
from recorder.server import App, HTTPError


class GoogleCalendarTests(unittest.TestCase):
    def test_authorization_uses_pkce_read_only_scope_and_login_hint(self):
        verifier = google_calendar.new_code_verifier()
        state = google_calendar.new_state()
        url = google_calendar.authorization_url(
            "client-id", "http://127.0.0.1:8765/api/google/callback", state, verifier,
            "ben20russell@gmail.com",
        )
        query = parse_qs(urlparse(url).query)

        self.assertEqual(query["state"], [state])
        self.assertEqual(query["login_hint"], ["ben20russell@gmail.com"])
        self.assertIn("calendar.readonly", query["scope"][0])
        self.assertEqual(query["code_challenge_method"], ["S256"])
        expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        self.assertEqual(query["code_challenge"], [expected])

    def test_event_conversion_preserves_attendees_and_video_link(self):
        event = google_calendar.event_from_google({
            "id": "evt-1",
            "summary": "Planning",
            "start": {"dateTime": "2026-11-18T10:00:00-08:00"},
            "end": {"dateTime": "2026-11-18T10:30:00-08:00"},
            "attendees": [
                {"email": "ben20russell@gmail.com", "self": True},
                {"email": "guest@example.com", "displayName": "Guest"},
            ],
            "conferenceData": {"entryPoints": [
                {"entryPointType": "video", "uri": "https://meet.google.com/abc-defg-hij"},
            ]},
        }, "Work")

        self.assertTrue(datetime.fromisoformat(event["start"]).tzinfo)
        self.assertEqual(event["calendar"], "Work")
        self.assertEqual(event["attendees"][0]["is_me"], True)
        self.assertEqual(event["attendees"][1]["name"], "Guest")
        self.assertEqual(event["url"], "https://meet.google.com/abc-defg-hij")

    def test_all_day_event_is_marked(self):
        event = google_calendar.event_from_google({
            "id": "evt-2",
            "start": {"date": "2026-11-18"},
            "end": {"date": "2026-11-19"},
        }, "Personal")

        self.assertTrue(event["all_day"])
        self.assertEqual(event["calendar"], "Personal")

    def test_redirect_uri_is_limited_to_local_or_current_codespace_host(self):
        app = App.__new__(App)
        app.port = 8765
        with patch.dict(os.environ, {
            "CODESPACE_NAME": "test-space",
            "GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN": "app.github.dev",
        }):
            self.assertEqual(
                app.google_redirect_uri("127.0.0.1:8765"),
                "http://localhost:8765/api/google/callback",
            )
            self.assertEqual(
                app.google_redirect_uri("test-space-8765.app.github.dev"),
                "https://test-space-8765.app.github.dev/api/google/callback",
            )
            with self.assertRaises(HTTPError):
                app.google_redirect_uri("attacker.example")

    def test_callback_rejects_unknown_oauth_state(self):
        app = App.__new__(App)
        app.google_oauth_lock = threading.Lock()
        app.google_oauth_pending = {}

        message, connected = app.google_callback({"state": ["unknown"], "code": ["bad"]}, "localhost:8765")

        self.assertFalse(connected)
        self.assertIn("expired", message)

    def test_callback_connects_only_the_requested_google_account(self):
        app = App.__new__(App)
        app.port = 8765
        app.cfg = {"calendar": {"google_client_id": "client-id"}}
        app.google_oauth_lock = threading.Lock()
        app.google_oauth_pending = {
            "state": {
                "verifier": "verifier",
                "redirect_uri": "http://localhost:8765/api/google/callback",
                "created": time.monotonic(),
            },
        }
        app.calendar = Mock()
        app.calendar_state = Mock(return_value={"google_connected": True})
        app.broadcast = Mock()
        updated = {"calendar": {"source": "google", "google_email": "ben20russell@gmail.com"}}

        with (
            patch("recorder.server.config.get_secret", return_value="client-secret"),
            patch("recorder.server.config.set_secret") as set_secret,
            patch("recorder.server.config.update", return_value=updated),
            patch.object(google_calendar, "exchange_code", return_value={
                "access_token": "access-token", "refresh_token": "refresh-token",
            }),
            patch.object(google_calendar, "account_email", return_value="ben20russell@gmail.com"),
        ):
            message, connected = app.google_callback(
                {"state": ["state"], "code": ["authorization-code"]}, "localhost:8765")

        self.assertTrue(connected)
        self.assertIn("ben20russell@gmail.com", message)
        set_secret.assert_called_once_with("google_refresh_token", "refresh-token")
        app.calendar.refresh.assert_called_once_with(force=True)

    def test_callback_does_not_store_tokens_for_another_google_account(self):
        app = App.__new__(App)
        app.port = 8765
        app.cfg = {"calendar": {"google_client_id": "client-id"}}
        app.google_oauth_lock = threading.Lock()
        app.google_oauth_pending = {
            "state": {
                "verifier": "verifier",
                "redirect_uri": "http://localhost:8765/api/google/callback",
                "created": time.monotonic(),
            },
        }

        with (
            patch("recorder.server.config.get_secret", return_value="client-secret"),
            patch("recorder.server.config.set_secret") as set_secret,
            patch.object(google_calendar, "exchange_code", return_value={"access_token": "access-token"}),
            patch.object(google_calendar, "account_email", return_value="other@example.com"),
        ):
            message, connected = app.google_callback(
                {"state": ["state"], "code": ["authorization-code"]}, "localhost:8765")

        self.assertFalse(connected)
        self.assertIn("not ben20russell@gmail.com", message)
        set_secret.assert_not_called()


if __name__ == "__main__":
    unittest.main()

import contextlib
import io
import unittest
from pathlib import Path
from unittest.mock import patch

from recorder import __main__ as callrec
from recorder.server import HTTPError, launch_claude_login


class ClaudeAuthTests(unittest.TestCase):
    def test_login_starts_claude_account_auth(self):
        env = {"PATH": "/usr/bin"}
        with (
            patch("recorder.summarize.find_claude_cli", return_value="/usr/local/bin/claude"),
            patch("recorder.summarize.cli_env", return_value=env),
            patch("recorder.summarize.cli_status", return_value={"logged_in": True, "account": "me@example.com"}),
            patch.object(callrec.subprocess, "run") as run,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            result = callrec.cmd_login(None, {})

        self.assertIsNone(result)
        run.assert_called_once_with(
            ["/usr/local/bin/claude", "auth", "login"],
            env=env,
        )

    def test_web_login_gives_vscode_terminal_instructions_off_macos(self):
        with patch("recorder.server.sys.platform", "linux"):
            with self.assertRaises(HTTPError) as raised:
                launch_claude_login()

        self.assertEqual(raised.exception.status, 501)
        self.assertIn("./callrec login", raised.exception.message)

    def test_macos_login_opens_terminal_with_the_launcher(self):
        script = Path("/workspace/with spaces/scripts/claude-login.command")
        with (
            patch("recorder.server.sys.platform", "darwin"),
            patch("recorder.server.ROOT", script.parents[1]),
            patch.object(Path, "is_file", return_value=True),
            patch("recorder.server.subprocess.run") as run,
        ):
            launch_claude_login()

        args = run.call_args.args[0]
        self.assertEqual(args[:2], ["osascript", "-e"])
        self.assertIn("/bin/bash '/workspace/with spaces/scripts/claude-login.command'", args[2])


if __name__ == "__main__":
    unittest.main()

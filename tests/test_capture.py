import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from recorder.capture import CaptureError, HelperCapture


class HelperCaptureTests(unittest.TestCase):
    def test_live_capture_explains_non_macos_limitation(self):
        capture = HelperCapture(Path("/tmp/callrec-test"), {})
        with patch.object(sys, "platform", "linux"):
            with self.assertRaisesRegex(CaptureError, "requires Call Recorder to run on macOS") as raised:
                capture.start()

        self.assertIn("cannot access your Mac's microphone", raised.exception.hint)


if __name__ == "__main__":
    unittest.main()

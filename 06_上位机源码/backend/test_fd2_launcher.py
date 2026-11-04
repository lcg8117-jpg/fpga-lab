import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class FD2LauncherTests(unittest.TestCase):
    def test_five_help(self):
        for variant in range(1, 6):
            result = subprocess.run(
                [
                    sys.executable,
                    "-X",
                    "utf8",
                    str(ROOT / "launch_host.py"),
                    f"V{variant}",
                    "--help",
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("--offline-preview", result.stdout)

    def test_detached_preview_once(self):
        result = subprocess.run(
            [
                sys.executable,
                "-X",
                "utf8",
                str(ROOT / "launch_host.py"),
                "V5",
                "--offline-preview",
                "--headless",
                "--once",
                "--port",
                "0",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"connected": false', result.stdout)
        self.assertIn('"variant": 5', result.stdout)

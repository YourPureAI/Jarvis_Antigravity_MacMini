import json
import subprocess
import sys
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "main.py"


class HomeDeviceTest(unittest.TestCase):
    def test_ping_localhost(self):
        r = subprocess.run([sys.executable, str(SCRIPT), "ping", "127.0.0.1"], capture_output=True, text=True, check=True)
        data = json.loads(r.stdout)
        self.assertTrue(data.get("ok"))
        self.assertTrue(data.get("online"))

    def test_hass_unconfigured_error_handling(self):
        r = subprocess.run([sys.executable, str(SCRIPT), "state"], capture_output=True, text=True, check=True)
        data = json.loads(r.stdout)
        self.assertFalse(data.get("ok"))
        self.assertIn("HASS_TOKEN", data.get("error", ""))


if __name__ == "__main__":
    unittest.main()

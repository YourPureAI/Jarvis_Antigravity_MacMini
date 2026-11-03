import json
import subprocess
import sys
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "main.py"


class SmokeTest(unittest.TestCase):
    def test_runs(self):
        r = subprocess.run([sys.executable, str(SCRIPT), "--echo", "hi"], capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(r.stdout)["echo"], "hi")


if __name__ == "__main__":
    unittest.main()

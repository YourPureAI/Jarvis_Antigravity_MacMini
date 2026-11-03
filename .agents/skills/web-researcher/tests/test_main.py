import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "main.py"


class WebResearcherTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.env = dict(os.environ, AGENT_SKILL_DATA=self.tmp)

    def test_save_and_search_notes(self):
        r1 = subprocess.run(
            [sys.executable, str(SCRIPT), "save", "Apple M4 Analysis", "Unified memory bandwidth is 120 GB/s on base M4", "--tags", "apple,hardware"],
            capture_output=True, text=True, env=self.env, check=True
        )
        data1 = json.loads(r1.stdout)
        self.assertTrue(data1.get("ok"))

        r2 = subprocess.run(
            [sys.executable, str(SCRIPT), "search", "memory bandwidth"],
            capture_output=True, text=True, env=self.env, check=True
        )
        data2 = json.loads(r2.stdout)
        self.assertTrue(data2.get("ok"))
        self.assertTrue(len(data2.get("results", [])) > 0)
        self.assertIn("Apple M4 Analysis", data2["results"][0]["title"])


if __name__ == "__main__":
    unittest.main()

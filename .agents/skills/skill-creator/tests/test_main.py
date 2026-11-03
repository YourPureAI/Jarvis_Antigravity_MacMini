import json
import subprocess
import sys
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "main.py"


class SkillCreatorTest(unittest.TestCase):
    def test_list_skills(self):
        r = subprocess.run([sys.executable, str(SCRIPT), "list"], capture_output=True, text=True, check=True)
        data = json.loads(r.stdout)
        self.assertTrue(data.get("ok"))
        self.assertTrue(any(s["name"] == "skill-creator" for s in data["skills"]))


if __name__ == "__main__":
    unittest.main()

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "main.py"


class OnlineRadioTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.env = os.environ.copy()
        self.env["AGENT_SKILL_DATA"] = self.temp_dir.name

    def tearDown(self):
        self.temp_dir.cleanup()

    def run_cli(self, *args: str) -> dict:
        cmd = [sys.executable, str(SCRIPT)] + list(args)
        res = subprocess.run(cmd, env=self.env, capture_output=True, text=True, check=True)
        return json.loads(res.stdout)

    def test_default_stations(self):
        data = self.run_cli("list")
        self.assertTrue(data.get("ok"))
        stations = data.get("stations", [])
        names = [s["name"] for s in stations]

        # Specific user requirement checks
        self.assertIn("Radio Krokodýl", names)
        self.assertIn("Český Rozhlas Radiožurnál", names)

        # Check NZ stations
        nz_stations = [s for s in stations if s["country"] == "NZ"]
        self.assertGreaterEqual(len(nz_stations), 3)

        # Check CZ stations
        cz_stations = [s for s in stations if s["country"] == "CZ"]
        self.assertGreaterEqual(len(cz_stations), 2)

    def test_filter_by_country(self):
        cz_data = self.run_cli("list", "--country", "CZ")
        self.assertTrue(cz_data.get("ok"))
        for s in cz_data.get("stations", []):
            self.assertEqual(s["country"], "CZ")

        nz_data = self.run_cli("list", "--country", "NZ")
        self.assertTrue(nz_data.get("ok"))
        self.assertGreaterEqual(len(nz_data.get("stations", [])), 3)
        for s in nz_data.get("stations", []):
            self.assertEqual(s["country"], "NZ")

    def test_add_and_remove_station(self):
        add_res = self.run_cli(
            "add",
            "--name", "My Custom Station",
            "--url", "http://stream.custom.radio/live",
            "--country", "CZ",
            "--genre", "Synthwave",
            "--desc", "User added retro station"
        )
        self.assertTrue(add_res.get("ok"))
        station_id = add_res["station"]["id"]

        # Verify it shows in list
        list_data = self.run_cli("list")
        names = [s["name"] for s in list_data["stations"]]
        self.assertIn("My Custom Station", names)

        # Remove station
        del_res = self.run_cli("remove", "--id", str(station_id))
        self.assertTrue(del_res.get("ok"))

        # Verify removal
        list_after = self.run_cli("list")
        names_after = [s["name"] for s in list_after["stations"]]
        self.assertNotIn("My Custom Station", names_after)

    def test_play_command(self):
        res = self.run_cli("play", "Radio Krokodýl")
        self.assertTrue(res.get("ok"))
        self.assertEqual(res["station"]["name"], "Radio Krokodýl")
        self.assertTrue("krokodyl" in res["station"]["url"].lower())


if __name__ == "__main__":
    unittest.main()

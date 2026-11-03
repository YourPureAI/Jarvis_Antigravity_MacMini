"""Unit tests for audio-control skill."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import main  # noqa: E402


class AudioControlTest(unittest.TestCase):
    def test_build_parser(self):
        p = main.build_parser()
        args = p.parse_args(["volume", "50"])
        self.assertEqual(args.command, "volume")
        self.assertEqual(args.level, 50)

        args_pause = p.parse_args(["pause", "--target", "radio"])
        self.assertEqual(args_pause.command, "pause")
        self.assertEqual(args_pause.target, "radio")

        args_up = p.parse_args(["up", "15"])
        self.assertEqual(args_up.command, "up")
        self.assertEqual(args_up.step, 15)

    @patch("main.run_apple_script")
    def test_get_mac_volume_parsing(self, mock_as):
        mock_as.return_value = {
            "ok": True,
            "output": "output volume:75, input volume:90, alert volume:100, output muted:false",
            "error": None,
        }
        res = main.get_mac_volume("macmini")
        self.assertTrue(res["ok"])
        self.assertEqual(res["output_volume"], 75)
        self.assertFalse(res["output_muted"])
        self.assertEqual(res["input_volume"], 90)

    @patch("main.run_apple_script")
    def test_set_mac_volume(self, mock_as):
        def side_effect(script, host="macmini", timeout=8):
            if "set volume output volume" in script:
                return {"ok": True, "output": "", "error": None}
            return {
                "ok": True,
                "output": "output volume:40, input volume:90, alert volume:100, output muted:false",
                "error": None,
            }

        mock_as.side_effect = side_effect
        res = main.set_mac_volume(40, host="macmini")
        self.assertTrue(res["ok"])
        self.assertEqual(res["output_volume"], 40)
        self.assertEqual(res["requested_volume"], 40)

    @patch("main.get_mac_volume")
    @patch("main.set_mac_volume")
    def test_adjust_mac_volume(self, mock_set, mock_get):
        mock_get.return_value = {"ok": True, "output_volume": 60}
        mock_set.return_value = {"ok": True, "output_volume": 70}

        res = main.adjust_mac_volume(10, host="macmini")
        mock_set.assert_called_with(70, host="macmini")
        self.assertTrue(res["ok"])

    @patch("main.run_apple_script")
    def test_set_and_toggle_mute(self, mock_as):
        mock_as.return_value = {
            "ok": True,
            "output": "output volume:70, input volume:90, alert volume:100, output muted:true",
            "error": None,
        }
        res = main.set_mac_mute(True, host="macmini")
        self.assertTrue(res["ok"])
        self.assertTrue(res["output_muted"])

    @patch("main.invoke_skill_command")
    @patch("main.pause_mac_media")
    def test_pause_all_audio(self, mock_mac, mock_skill):
        mock_skill.return_value = {"ok": True, "output": "paused"}
        mock_mac.return_value = {"ok": True, "output": "paused"}

        res = main.pause_all_audio("all", host="macmini")
        self.assertTrue(res["ok"])
        self.assertEqual(res["action"], "pause")
        self.assertIn("online-radio", res["details"])
        self.assertIn("run-youtube-music", res["details"])
        self.assertIn("macmini_media", res["details"])

    @patch("main.invoke_skill_command")
    @patch("main.stop_mac_media")
    def test_stop_all_audio(self, mock_mac, mock_skill):
        mock_skill.return_value = {"ok": True, "output": "stopped"}
        mock_mac.return_value = {"ok": True, "output": "stopped"}

        res = main.stop_all_audio("radio", host="macmini")
        self.assertTrue(res["ok"])
        self.assertEqual(res["action"], "stop")
        self.assertIn("online-radio", res["details"])
        self.assertNotIn("run-youtube-music", res["details"])

    @patch("main.get_mac_volume")
    @patch("main.invoke_skill_command")
    def test_full_status(self, mock_skill, mock_vol):
        mock_vol.return_value = {"ok": True, "output_volume": 80, "output_muted": False}
        mock_skill.return_value = {"ok": True, "data": {"playback": {"state": "playing"}}}

        st = main.get_full_audio_status("macmini")
        self.assertTrue(st["ok"])
        self.assertEqual(st["macmini"]["output_volume"], 80)
        self.assertIn("online-radio", st["skills"])


if __name__ == "__main__":
    unittest.main()

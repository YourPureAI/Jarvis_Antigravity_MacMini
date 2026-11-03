"""Unit tests for Voice Agent skill."""
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
MAIN_PY = ROOT / ".agents" / "skills" / "voice-agent" / "scripts" / "main.py"


class TestVoiceAgent(unittest.TestCase):
    def test_status_json_output(self):
        res = subprocess.run([sys.executable, str(MAIN_PY), "status"], capture_output=True, text=True, check=True)
        data = json.loads(res.stdout)
        self.assertTrue(data["ok"])
        self.assertIn("status", data)
        self.assertIn("active", data["status"])
        self.assertIn("mode", data["status"])

    def test_clean_text_for_speech(self):
        sys.path.insert(0, str(ROOT / "runtime"))
        import voice_worker
        raw = "**Dobrý den!** Zde je `kód` a [odkaz](http://example.com) s úsměvem 😊."
        cleaned = voice_worker.clean_text_for_speech(raw)
        self.assertNotIn("**", cleaned)
        self.assertNotIn("`", cleaned)
        self.assertNotIn("😊", cleaned)
        self.assertIn("Dobrý den", cleaned)

    def test_hallucination_filter(self):
        sys.path.insert(0, str(ROOT / "runtime"))
        import voice_worker
        self.assertTrue(voice_worker.is_hallucination("Děkuji za sledování a odběr"))
        self.assertTrue(voice_worker.is_hallucination("titulky vyrobil johnyx"))
        self.assertTrue(voice_worker.is_hallucination(""))
        self.assertFalse(voice_worker.is_hallucination("Jaké je dnes počasí v Brně?"))

    def test_stop_tts_safety(self):
        sys.path.insert(0, str(ROOT / "runtime"))
        import voice_worker
        # Calling stop_tts when no speech is active should safely return without exceptions
        voice_worker.stop_tts()
        self.assertFalse(voice_worker.is_tts_playing)
        self.assertEqual(voice_worker.tts_cooldown_until, 0.0)

    def test_language_support(self):
        sys.path.insert(0, str(ROOT / "runtime"))
        import voice
        st = voice.get_status()
        self.assertIn("language", st)


if __name__ == "__main__":
    unittest.main()

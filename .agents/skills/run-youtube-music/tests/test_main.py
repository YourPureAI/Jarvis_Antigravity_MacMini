"""Unit tests for the run-youtube-music skill."""
import json
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# Ensure scripts dir is on sys.path
SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import main  # noqa: E402


class YoutubeMusicTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db_path = Path(self.tmp.name)
        main.init_db(self.db_path)

    def tearDown(self):
        try:
            self.db_path.unlink(missing_ok=True)
        except Exception:
            pass

    def test_extract_video_id(self):
        # Raw 11-char ID
        self.assertEqual(main.extract_video_id("dQw4w9WgXcQ"), "dQw4w9WgXcQ")
        # Standard watch URL
        self.assertEqual(
            main.extract_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42s"),
            "dQw4w9WgXcQ",
        )
        # youtu.be shortlink
        self.assertEqual(main.extract_video_id("https://youtu.be/dQw4w9WgXcQ"), "dQw4w9WgXcQ")
        # YouTube Music URL
        self.assertEqual(
            main.extract_video_id("https://music.youtube.com/watch?v=dQw4w9WgXcQ"),
            "dQw4w9WgXcQ",
        )
        # Embed URL
        self.assertEqual(
            main.extract_video_id("https://www.youtube.com/embed/dQw4w9WgXcQ"),
            "dQw4w9WgXcQ",
        )
        # Shorts URL
        self.assertEqual(
            main.extract_video_id("https://www.youtube.com/shorts/dQw4w9WgXcQ"),
            "dQw4w9WgXcQ",
        )
        # Non-video query
        self.assertIsNone(main.extract_video_id("lofi hip hop radio"))

    def test_playlist_lifecycle(self):
        # Create
        res = main.playlist_create("Coding Flow", "Focus beats", db_path=self.db_path)
        self.assertTrue(res["ok"])
        pid = res["id"]
        self.assertIsInstance(pid, int)

        # Duplicate create
        dup = main.playlist_create("Coding Flow", db_path=self.db_path)
        self.assertFalse(dup["ok"])

        # Add track using direct video ID
        add_res = main.playlist_add("Coding Flow", "dQw4w9WgXcQ", custom_title="Rickroll Focus", db_path=self.db_path)
        self.assertTrue(add_res["ok"])
        item_id = add_res["item"]["id"]
        self.assertEqual(add_res["item"]["title"], "Rickroll Focus")
        self.assertEqual(add_res["item"]["video_id"], "dQw4w9WgXcQ")

        # List playlists
        l_res = main.playlist_list(db_path=self.db_path)
        self.assertTrue(l_res["ok"])
        self.assertEqual(len(l_res["playlists"]), 1)
        self.assertEqual(l_res["playlists"][0]["track_count"], 1)

        # Show playlist
        s_res = main.playlist_show("Coding Flow", db_path=self.db_path)
        self.assertTrue(s_res["ok"])
        self.assertEqual(len(s_res["playlist"]["tracks"]), 1)
        self.assertEqual(s_res["playlist"]["tracks"][0]["video_id"], "dQw4w9WgXcQ")

        # Remove track
        rm_res = main.playlist_remove("Coding Flow", item_id, db_path=self.db_path)
        self.assertTrue(rm_res["ok"])
        s_res2 = main.playlist_show("Coding Flow", db_path=self.db_path)
        self.assertEqual(len(s_res2["playlist"]["tracks"]), 0)

        # Delete playlist
        del_res = main.playlist_delete("Coding Flow", db_path=self.db_path)
        self.assertTrue(del_res["ok"])
        l_res2 = main.playlist_list(db_path=self.db_path)
        self.assertEqual(len(l_res2["playlists"]), 0)

    @patch("main.search_youtube")
    def test_resolve_playable_item(self, mock_search):
        mock_search.return_value = [
            {"id": "abcdefghijk", "title": "Mock Lofi Beat", "url": "https://www.youtube.com/watch?v=abcdefghijk"}
        ]
        res = main.resolve_playable_item("chill lofi", target="music")
        self.assertIsNotNone(res)
        self.assertEqual(res["id"], "abcdefghijk")
        self.assertIn("music.youtube.com", res["play_url"])
        self.assertIn("autoplay=1", res["play_url"])

    @patch("main.open_browser_cleanly", return_value=True)
    def test_cli_play(self, mock_open):
        stdout_trap = io.StringIO()
        with patch("sys.stdout", stdout_trap):
            main.main(["play", "dQw4w9WgXcQ", "--no-open"])
        out = json.loads(stdout_trap.getvalue())
        self.assertTrue(out.get("ok"))
        self.assertEqual(out.get("video_id"), "dQw4w9WgXcQ")
        self.assertIn("autoplay=1", out.get("play_url"))


if __name__ == "__main__":
    unittest.main()

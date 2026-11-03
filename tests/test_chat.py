"""Unit tests for Agent Chat Engine, tools registry, and API endpoints."""
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "runtime"))
sys.path.insert(0, str(ROOT / "runtime" / "web"))

import llm_chat  # noqa: E402
import server  # noqa: E402


class ChatEngineTest(unittest.TestCase):
    def test_build_tools(self):
        tools = llm_chat.build_tools()
        names = [t["function"]["name"] for t in tools]
        self.assertIn("get_weather", names)
        self.assertIn("check_macmini", names)
        self.assertIn("execute_skill", names)
        self.assertIn("search_memory", names)
        self.assertIn("add_memory", names)
        self.assertIn("send_notification", names)

    def test_weather_tool(self):
        res = llm_chat.fetch_weather("Praha")
        parsed = json.loads(res)
        self.assertTrue(parsed.get("ok"))
        self.assertIn("Prague", parsed.get("location", ""))
        self.assertIn("temperature_celsius", parsed.get("current", {}))

    def test_execute_tool_call(self):
        # Test memory addition tool
        res = llm_chat.execute_tool_call("add_memory", {
            "subject": "TestChatEntity",
            "content": "Testing chat tool capability",
            "kind": "fact"
        })
        self.assertIn("TestChatEntity", res)

        # Test memory search tool
        search_res = llm_chat.execute_tool_call("search_memory", {"query": "TestChatEntity"})
        self.assertIn("TestChatEntity", search_res)

        # Test notification tool
        notif_res = llm_chat.execute_tool_call("send_notification", {
            "title": "ChatTest",
            "message": "Testing notification from chat"
        })
        self.assertIn("inbox_id", notif_res)

        # Test run-youtube-music execution (ensuring --no-open is added and metadata returned)
        yt_res = llm_chat.execute_tool_call("execute_skill", {
            "skill": "run-youtube-music",
            "args": ["play", "dQw4w9WgXcQ"]
        })
        yt_data = json.loads(yt_res)
        self.assertTrue(yt_data.get("ok"))
        self.assertEqual(yt_data.get("video_id"), "dQw4w9WgXcQ")
        self.assertFalse(yt_data.get("browser_opened"))

    def test_chat_history_db(self):
        session = "test_sess_123"
        llm_chat.clear_chat_history(session)

        con = llm_chat.db()
        con.execute(
            "INSERT INTO chat_messages(session_id, role, content, created_at) VALUES(?,?,?,?)",
            (session, "user", "Hello agent", "2026-11-02T19:00:00Z")
        )
        con.execute(
            "INSERT INTO chat_messages(session_id, role, content, prompt_tokens, completion_tokens, total_tokens, cached_tokens, created_at) VALUES(?,?,?,?,?,?,?,?)",
            (session, "assistant", "Hello User", 100, 25, 125, 10, "2026-11-02T19:00:01Z")
        )
        con.commit()

        history = llm_chat.get_chat_history(session)
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["content"], "Hello agent")
        self.assertEqual(history[1]["content"], "Hello User")
        self.assertEqual(history[1]["prompt_tokens"], 100)
        self.assertEqual(history[1]["completion_tokens"], 25)
        self.assertEqual(history[1]["total_tokens"], 125)
        self.assertEqual(history[1]["cached_tokens"], 10)

        stats = llm_chat.get_chat_stats(session)
        self.assertEqual(stats["total_tokens"], 125)
        self.assertEqual(stats["prompt_tokens"], 100)
        self.assertEqual(stats["completion_tokens"], 25)
        self.assertEqual(stats["cached_tokens"], 10)

        llm_chat.clear_chat_history(session)
        self.assertEqual(len(llm_chat.get_chat_history(session)), 0)
        empty_stats = llm_chat.get_chat_stats(session)
        self.assertEqual(empty_stats["total_tokens"], 0)


class ChatApiServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        server.STATIC_DIR.mkdir(parents=True, exist_ok=True)
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.AgentApiHandler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def get_json(self, path: str) -> dict:
        url = f"http://127.0.0.1:{self.port}{path}"
        with urllib.request.urlopen(url, timeout=5) as resp:
            self.assertEqual(resp.status, 200)
            return json.loads(resp.read().decode("utf-8"))

    def post_json(self, path: str, data: dict) -> dict:
        url = f"http://127.0.0.1:{self.port}{path}"
        body = json.dumps(data).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def test_chat_config_endpoint(self):
        data = self.get_json("/api/chat/config")
        self.assertTrue(data.get("ok"))
        self.assertIn("config", data)
        self.assertIn("active", data)
        self.assertIn("provider", data["active"])

    def test_chat_messages_endpoint(self):
        data = self.get_json("/api/chat/messages")
        self.assertTrue(data.get("ok"))
        self.assertIsInstance(data.get("messages"), list)
        self.assertIn("stats", data)
        self.assertIn("total_tokens", data["stats"])

    def test_chat_clear_endpoint(self):
        data = self.post_json("/api/chat/clear", {"session_id": "test_sess"})
        self.assertTrue(data.get("ok"))
        self.assertIn("stats", data)


if __name__ == "__main__":
    unittest.main()


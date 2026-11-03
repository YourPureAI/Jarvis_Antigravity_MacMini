"""Unit tests for Agent Web UI server & API endpoints."""
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

import server  # noqa: E402


class WebApiTest(unittest.TestCase):
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

    def test_overview_endpoint(self):
        data = self.get_json("/api/overview")
        self.assertTrue(data.get("ok"))
        self.assertIn("agent", data)
        self.assertIn("heartbeat", data["agent"])

    def test_skills_list_endpoint(self):
        data = self.get_json("/api/skills")
        self.assertTrue(data.get("ok"))
        skills = data.get("skills", [])
        self.assertTrue(any(s["name"] == "skill-creator" for s in skills))
        self.assertTrue(any(s["name"] == "macmini-monitor" for s in skills))

    def test_skill_widget_endpoint(self):
        data = self.get_json("/api/skills/macmini-monitor/widget")
        self.assertTrue(data.get("ok"))
        self.assertTrue(data.get("custom"))
        self.assertIn("Apple M4 Vitals", data.get("html", ""))

    def test_memory_endpoint(self):
        data = self.get_json("/api/memory")
        self.assertTrue(data.get("ok"))
        self.assertIn("facts", data)

    def test_jobs_endpoint(self):
        data = self.get_json("/api/jobs")
        self.assertTrue(data.get("ok"))
        self.assertIn("jobs", data)

    def test_voice_status_and_stop_endpoints(self):
        status_data = self.get_json("/api/voice/status")
        self.assertTrue(status_data.get("ok"))
        self.assertIn("status", status_data)
        self.assertIn("active", status_data["status"])

        stop_data = self.post_json("/api/voice/stop", {})
        self.assertTrue(stop_data.get("ok"))


if __name__ == "__main__":
    unittest.main()

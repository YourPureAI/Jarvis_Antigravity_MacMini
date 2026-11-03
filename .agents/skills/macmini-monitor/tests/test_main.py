import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add scripts directory to path
SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import main  # noqa: E402


class TestPingCheck(unittest.TestCase):
    def test_ping_unreachable_on_windows(self):
        output = """Pinging 192.168.1.100 with 32 bytes of data:
Reply from 192.168.1.242: Destination host unreachable.

Ping statistics for 192.168.1.100:
    Packets: Sent = 1, Received = 1, Lost = 0 (0% loss),"""
        mock_proc = MagicMock(returncode=0, stdout=output)
        with patch("subprocess.run", return_value=mock_proc):
            res = main.ping_check("192.168.1.100")
            self.assertFalse(res["ok"])

    def test_ping_success_on_windows(self):
        output = """Pinging 192.168.1.100 with 32 bytes of data:
Reply from 192.168.1.100: bytes=32 time=2ms TTL=64

Ping statistics for 192.168.1.100:
    Packets: Sent = 1, Received = 1, Lost = 0 (0% loss),
Approximate round trip times in milli-seconds:
    Minimum = 2ms, Maximum = 2ms, Average = 2ms"""
        mock_proc = MagicMock(returncode=0, stdout=output)
        with patch("subprocess.run", return_value=mock_proc):
            res = main.ping_check("192.168.1.100")
            self.assertTrue(res["ok"])
            self.assertEqual(res["latency_ms"], 2.0)


class TestMacHealthParser(unittest.TestCase):
    def test_parse_mac_health_output(self):
        sample_output = """
15:30  up 14 days,  2:12, 1 user, load averages: 1.25 0.95 0.88
===SECTION===
ProductName: macOS
ProductVersion: 15.3
BuildVersion: 24D60
===SECTION===
Filesystem     1K-blocks      Used Available Capacity iused      ifree %iused  Mounted on
/dev/disk3s1s1 494384640 125438400 368946240    26%  450000 3689462400    0%   /
===SECTION===
Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages free:                               34512.
Pages active:                            210543.
Pages inactive:                           98012.
Pages speculative:                         4321.
Pages wired down:                         89123.
Pages occupied by compressor:             12400.
"""
        metrics = main.parse_mac_health_output(sample_output)
        self.assertIn("14 days", metrics["uptime"])
        self.assertEqual(metrics["load_1m"], 1.25)
        self.assertEqual(metrics["load_5m"], 0.95)
        self.assertEqual(metrics["load_15m"], 0.88)
        self.assertEqual(metrics["os_version"], "macOS 15.3 (24D60)")
        self.assertEqual(metrics["disk_used_pct"], 26.0)
        self.assertIsNotNone(metrics["mem_used_pct"])
        self.assertGreater(metrics["mem_used_pct"], 0)


class TestDatabaseAndHistory(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db_path = Path(self.tmp.name)

    def tearDown(self):
        if self.db_path.exists():
            self.db_path.unlink()

    def test_record_and_history(self):
        con = main.get_db(self.db_path)
        data1 = {
            "timestamp": "2026-11-02T12:00:00Z",
            "host": "macmini",
            "ip": "192.168.1.100",
            "is_up": 1,
            "ping_ok": 1,
            "ping_ms": 2.5,
            "port_ok": 1,
            "port_ms": 3.1,
            "ssh_ok": 1,
            "load_1m": 0.5,
            "load_5m": 0.4,
            "load_15m": 0.3,
            "mem_used_pct": 45.0,
            "disk_used_pct": 30.0,
            "uptime_str": "up 2 days",
            "details_json": "{}",
        }
        main.record_check(con, data1)
        con.close()

        hist = main.query_history(limit=5, host="macmini", db_path=self.db_path)
        self.assertTrue(hist["ok"])
        self.assertEqual(hist["count"], 1)
        self.assertEqual(hist["history"][0]["is_up"], 1)

    def test_transition_detection(self):
        # First check: online
        with patch("main.ping_check", return_value={"ok": True, "latency_ms": 1.2}):
            with patch("main.tcp_port_check", return_value={"ok": True, "latency_ms": 2.0}):
                with patch("main.ssh_health_check", return_value={"ok": True, "metrics": {}}):
                    res1 = main.check_status(host="macmini", db_path=self.db_path)
                    self.assertTrue(res1["is_up"])
                    self.assertIsNone(res1["transition"])

        # Second check: offline -> should detect transition: "offline"
        with patch("main.ping_check", return_value={"ok": False}):
            with patch("main.tcp_port_check", return_value={"ok": False}):
                res2 = main.check_status(host="macmini", db_path=self.db_path)
                self.assertFalse(res2["is_up"])
                self.assertEqual(res2["transition"], "offline")

        # Third check: back online -> should detect transition: "recovered"
        with patch("main.ping_check", return_value={"ok": True, "latency_ms": 1.1}):
            with patch("main.tcp_port_check", return_value={"ok": True, "latency_ms": 1.8}):
                with patch("main.ssh_health_check", return_value={"ok": True, "metrics": {}}):
                    res3 = main.check_status(host="macmini", db_path=self.db_path)
                    self.assertTrue(res3["is_up"])
                    self.assertEqual(res3["transition"], "recovered")


class TestCLIExecution(unittest.TestCase):
    def test_cli_help(self):
        script = SCRIPTS_DIR / "main.py"
        r = subprocess.run([sys.executable, str(script), "--help"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        self.assertIn("Mac Mini availability", r.stdout)


if __name__ == "__main__":
    unittest.main()

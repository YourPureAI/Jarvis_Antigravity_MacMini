"""Mac Mini availability and system health monitor.

Checks ICMP ping, TCP port 22 reachability, and remote SSH system metrics
(uptime, load averages, memory, disk usage). Persists check history to SQLite
and detects status transitions (offline / recovered).
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_HOST = "macmini"
DEFAULT_IP = "192.168.1.100"
DEFAULT_PORT = 22
DEFAULT_TIMEOUT_SEC = 3


def get_data_dir() -> Path:
    data = os.environ.get("AGENT_SKILL_DATA")
    if data:
        p = Path(data)
    else:
        p = Path(__file__).resolve().parent.parent / "data"
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_db(db_path: Path | None = None) -> sqlite3.Connection:
    if db_path is None:
        db_path = get_data_dir() / "state.db"
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS checks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            host TEXT NOT NULL,
            ip TEXT NOT NULL,
            is_up INTEGER NOT NULL,
            ping_ok INTEGER NOT NULL,
            ping_ms REAL,
            port_ok INTEGER NOT NULL,
            port_ms REAL,
            ssh_ok INTEGER NOT NULL,
            load_1m REAL,
            load_5m REAL,
            load_15m REAL,
            mem_used_pct REAL,
            disk_used_pct REAL,
            uptime_str TEXT,
            details_json TEXT
        )
        """
    )
    con.commit()
    return con


def ping_check(ip: str, timeout_ms: int = 1000) -> dict:
    flag = "-n" if sys.platform == "win32" else "-c"
    timeout_flag = "-w" if sys.platform == "win32" else "-W"
    timeout_val = str(timeout_ms) if sys.platform == "win32" else str(max(1, timeout_ms // 1000))

    t0 = time.perf_counter()
    try:
        r = subprocess.run(
            ["ping", flag, "1", timeout_flag, timeout_val, ip],
            capture_output=True,
            text=True,
            timeout=max(2.0, timeout_ms / 1000.0 + 1.5),
        )
        elapsed_ms = round((time.perf_counter() - t0) * 1000, 2)
        stdout = r.stdout
        # Windows ping returncode can be 0 even on 'Destination host unreachable'
        unreachable = any(
            phrase in stdout.lower()
            for phrase in ("unreachable", "timed out", "could not find host", "100% loss")
        )
        has_reply = "ttl=" in stdout.lower() or (f"reply from {ip.lower()}" in stdout.lower() and not unreachable)
        online = r.returncode == 0 and not unreachable and has_reply

        latency_ms = None
        if online:
            # Extract round-trip time from ping output: time=12ms or time<1ms
            m = re.search(r"time[<=](\d+(?:\.\d+)?)ms", stdout, re.IGNORECASE)
            if m:
                latency_ms = float(m.group(1))
            else:
                latency_ms = elapsed_ms
        return {"ok": online, "latency_ms": latency_ms, "raw": stdout.strip()}
    except Exception as e:
        return {"ok": False, "latency_ms": None, "error": str(e)}


def tcp_port_check(ip: str, port: int = 22, timeout_sec: float = 2.0) -> dict:
    t0 = time.perf_counter()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout_sec)
    try:
        sock.connect((ip, port))
        sock.close()
        elapsed_ms = round((time.perf_counter() - t0) * 1000, 2)
        return {"ok": True, "latency_ms": elapsed_ms, "error": None}
    except Exception as e:
        return {"ok": False, "latency_ms": None, "error": str(e)}


def parse_mac_health_output(stdout: str) -> dict:
    """Parses combined output from Mac Mini diagnostics."""
    metrics: dict = {
        "uptime": None,
        "load_1m": None,
        "load_5m": None,
        "load_15m": None,
        "os_version": None,
        "disk_used_pct": None,
        "mem_used_pct": None,
    }
    parts = stdout.split("===SECTION===")
    for section in parts:
        section = section.strip()
        if not section:
            continue
        # Uptime section
        if "load average" in section or "load averages" in section:
            # Example: 15:30  up 12 days,  3:14, 2 users, load averages: 1.12 1.45 1.50
            metrics["uptime"] = section.split("\n")[0].strip()
            m = re.search(r"load averages?:\s*([0-9.]+)[,\s]+([0-9.]+)[,\s]+([0-9.]+)", section)
            if m:
                metrics["load_1m"] = float(m.group(1))
                metrics["load_5m"] = float(m.group(2))
                metrics["load_15m"] = float(m.group(3))
        # sw_vers section
        elif "ProductVersion:" in section or "ProductName:" in section:
            ver_m = re.search(r"ProductVersion:\s*([^\n]+)", section)
            build_m = re.search(r"BuildVersion:\s*([^\n]+)", section)
            ver = ver_m.group(1).strip() if ver_m else ""
            build = build_m.group(1).strip() if build_m else ""
            metrics["os_version"] = f"macOS {ver} ({build})".strip()
        # df section
        elif "Filesystem" in section and "/" in section:
            lines = section.strip().split("\n")
            if len(lines) >= 2:
                # Find Capacity or %
                m = re.search(r"(\d+)%", lines[-1])
                if m:
                    metrics["disk_used_pct"] = float(m.group(1))
        # vm_stat section
        elif "Mach Virtual Memory Statistics" in section or "Pages free:" in section:
            page_size = 16384  # default arm64 page size
            m_ps = re.search(r"page size of (\d+) bytes", section)
            if m_ps:
                page_size = int(m_ps.group(1))

            def get_pages(name: str) -> int:
                m = re.search(rf"{name}:\s+(\d+)\.", section)
                return int(m.group(1)) if m else 0

            p_free = get_pages("Pages free")
            p_active = get_pages("Pages active")
            p_inactive = get_pages("Pages inactive")
            p_spec = get_pages("Pages speculative")
            p_wired = get_pages("Pages wired down")
            p_comp = get_pages("Pages occupied by compressor")

            total_pages = p_free + p_active + p_inactive + p_spec + p_wired + p_comp
            if total_pages > 0:
                used_pages = p_active + p_wired + p_comp
                metrics["mem_used_pct"] = round((used_pages / total_pages) * 100.0, 1)

    return metrics


def ssh_health_check(host: str, timeout_sec: int = 5) -> dict:
    cmd_str = (
        'uptime; echo "===SECTION==="; '
        'sw_vers; echo "===SECTION==="; '
        'df -k /; echo "===SECTION==="; '
        "vm_stat"
    )
    try:
        r = subprocess.run(
            [
                "ssh",
                "-o",
                f"ConnectTimeout={timeout_sec}",
                "-o",
                "BatchMode=yes",
                "-o",
                "StrictHostKeyChecking=accept-new",
                host,
                cmd_str,
            ],
            capture_output=True,
            text=True,
            timeout=timeout_sec + 3,
        )
        if r.returncode == 0:
            metrics = parse_mac_health_output(r.stdout)
            return {"ok": True, "metrics": metrics, "raw": r.stdout[:1000]}
        return {"ok": False, "returncode": r.returncode, "error": r.stderr.strip()}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def get_latest_check(con: sqlite3.Connection, host: str) -> dict | None:
    cur = con.execute("SELECT * FROM checks WHERE host=? ORDER BY id DESC LIMIT 1", (host,))
    row = cur.fetchone()
    if not row:
        return None
    return dict(row)


def record_check(con: sqlite3.Connection, data: dict) -> None:
    con.execute(
        """
        INSERT INTO checks (
            timestamp, host, ip, is_up, ping_ok, ping_ms, port_ok, port_ms,
            ssh_ok, load_1m, load_5m, load_15m, mem_used_pct, disk_used_pct,
            uptime_str, details_json
        ) VALUES (
            :timestamp, :host, :ip, :is_up, :ping_ok, :ping_ms, :port_ok, :port_ms,
            :ssh_ok, :load_1m, :load_5m, :load_15m, :mem_used_pct, :disk_used_pct,
            :uptime_str, :details_json
        )
        """,
        data,
    )
    con.commit()


def check_status(
    host: str = DEFAULT_HOST,
    ip: str = DEFAULT_IP,
    port: int = DEFAULT_PORT,
    timeout: int = DEFAULT_TIMEOUT_SEC,
    record: bool = True,
    db_path: Path | None = None,
) -> dict:
    t_start = datetime.datetime.now(datetime.timezone.utc).isoformat()
    ping_res = ping_check(ip, timeout_ms=timeout * 1000)
    port_res = tcp_port_check(ip, port=port, timeout_sec=float(timeout))

    ssh_res = {"ok": False, "metrics": {}}
    if port_res["ok"]:
        ssh_res = ssh_health_check(host, timeout_sec=timeout)

    is_up = bool(ping_res["ok"] or port_res["ok"] or ssh_res["ok"])
    metrics = ssh_res.get("metrics") or {}

    con = get_db(db_path)
    latest = get_latest_check(con, host)

    transition = None
    if latest is not None:
        last_is_up = bool(latest["is_up"])
        if last_is_up and not is_up:
            transition = "offline"
        elif not last_is_up and is_up:
            transition = "recovered"

    record_data = {
        "timestamp": t_start,
        "host": host,
        "ip": ip,
        "is_up": 1 if is_up else 0,
        "ping_ok": 1 if ping_res["ok"] else 0,
        "ping_ms": ping_res.get("latency_ms"),
        "port_ok": 1 if port_res["ok"] else 0,
        "port_ms": port_res.get("latency_ms"),
        "ssh_ok": 1 if ssh_res["ok"] else 0,
        "load_1m": metrics.get("load_1m"),
        "load_5m": metrics.get("load_5m"),
        "load_15m": metrics.get("load_15m"),
        "mem_used_pct": metrics.get("mem_used_pct"),
        "disk_used_pct": metrics.get("disk_used_pct"),
        "uptime_str": metrics.get("uptime"),
        "details_json": json.dumps({"ping": ping_res, "port": port_res, "ssh": ssh_res}),
    }

    if record:
        record_check(con, record_data)

    con.close()

    return {
        "ok": True,
        "host": host,
        "ip": ip,
        "is_up": is_up,
        "status": "online" if is_up else "offline",
        "transition": transition,
        "ping": ping_res,
        "port": port_res,
        "ssh": {"ok": ssh_res["ok"], "metrics": metrics},
        "timestamp": t_start,
    }


def query_history(limit: int = 10, host: str = DEFAULT_HOST, db_path: Path | None = None) -> dict:
    con = get_db(db_path)
    cur = con.execute(
        "SELECT id, timestamp, host, is_up, ping_ok, ping_ms, port_ok, ssh_ok, load_1m, mem_used_pct, disk_used_pct "
        "FROM checks WHERE host=? ORDER BY id DESC LIMIT ?",
        (host, limit),
    )
    rows = [dict(r) for r in cur.fetchall()]
    con.close()
    return {"ok": True, "count": len(rows), "history": rows}


def query_uptime(hours: int = 24, host: str = DEFAULT_HOST, db_path: Path | None = None) -> dict:
    con = get_db(db_path)
    since = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=hours)).isoformat()
    cur = con.execute(
        "SELECT id, timestamp, is_up FROM checks WHERE host=? AND timestamp >= ? ORDER BY id ASC",
        (host, since),
    )
    rows = [dict(r) for r in cur.fetchall()]
    con.close()

    if not rows:
        return {"ok": True, "hours": hours, "total_checks": 0, "uptime_pct": None, "status": "no_data"}

    total = len(rows)
    up_count = sum(1 for r in rows if r["is_up"] == 1)
    pct = round((up_count / total) * 100.0, 2)
    return {
        "ok": True,
        "hours": hours,
        "total_checks": total,
        "up_checks": up_count,
        "uptime_pct": pct,
        "first_check": rows[0]["timestamp"],
        "latest_check": rows[-1]["timestamp"],
    }


def wake_on_lan(mac: str, broadcast: str = "255.255.255.255", port: int = 9) -> dict:
    clean_mac = mac.replace(":", "").replace("-", "").replace(".", "")
    if len(clean_mac) != 12:
        return {"ok": False, "error": f"Invalid MAC address: {mac}"}
    data = bytes.fromhex("FF" * 6 + clean_mac * 16)
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.sendto(data, (broadcast, port))
        sock.close()
        return {"ok": True, "mac": mac, "broadcast": broadcast, "sent": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def main() -> None:
    p = argparse.ArgumentParser(description="Mac Mini availability & health monitor")
    sub = p.add_subparsers(dest="action", required=True)

    # status
    p_status = sub.add_parser("status", help="Check current Mac Mini status and metrics")
    p_status.add_argument("--host", default=DEFAULT_HOST, help=f"SSH host alias (default: {DEFAULT_HOST})")
    p_status.add_argument("--ip", default=DEFAULT_IP, help=f"Host IP (default: {DEFAULT_IP})")
    p_status.add_argument("--port", type=int, default=DEFAULT_PORT, help="Port to test (default: 22)")
    p_status.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SEC, help="Timeout in seconds")
    p_status.add_argument("--no-record", action="store_true", help="Do not save this check to database")
    p_status.add_argument("--fail-if-down", action="store_true", help="Exit code 1 if host is unreachable")

    # history
    p_hist = sub.add_parser("history", help="Query recent check history")
    p_hist.add_argument("--limit", type=int, default=10, help="Number of records (default: 10)")
    p_hist.add_argument("--host", default=DEFAULT_HOST)

    # uptime
    p_upt = sub.add_parser("uptime", help="Calculate uptime percentage over a time window")
    p_upt.add_argument("--hours", type=int, default=24, help="Window in hours (default: 24)")
    p_upt.add_argument("--host", default=DEFAULT_HOST)

    # wake
    p_wake = sub.add_parser("wake", help="Send Wake-on-LAN packet")
    p_wake.add_argument("--mac", required=True, help="Target MAC address")
    p_wake.add_argument("--broadcast", default="255.255.255.255")

    args = p.parse_args()

    if args.action == "status":
        res = check_status(
            host=args.host,
            ip=args.ip,
            port=args.port,
            timeout=args.timeout,
            record=not args.no_record,
        )
        print(json.dumps(res, indent=2))
        if getattr(args, "fail_if_down", False):
            if not res["is_up"]:
                sys.exit(1)
        return

    if args.action == "history":
        res = query_history(limit=args.limit, host=args.host)
        print(json.dumps(res, indent=2))
        return

    if args.action == "uptime":
        res = query_uptime(hours=args.hours, host=args.host)
        print(json.dumps(res, indent=2))
        return

    if args.action == "wake":
        res = wake_on_lan(mac=args.mac, broadcast=args.broadcast)
        print(json.dumps(res, indent=2))
        return


if __name__ == "__main__":
    main()

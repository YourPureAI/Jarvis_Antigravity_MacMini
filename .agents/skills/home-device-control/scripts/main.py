"""Home Assistant & Local Network Device Control."""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys


def get_hass_headers() -> tuple[str, dict] | None:
    url = os.environ.get("HASS_URL") or "http://homeassistant.local:8123"
    token = os.environ.get("HASS_TOKEN")
    if not token:
        return None
    return url.rstrip("/"), {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def hass_get_state(entity_id: str | None = None) -> dict:
    cfg = get_hass_headers()
    if not cfg:
        return {"ok": False, "error": "HASS_TOKEN not configured in secrets (run: python runtime/agent.py secret set HASS_TOKEN)"}
    base_url, headers = cfg
    import httpx

    endpoint = f"{base_url}/api/states/{entity_id}" if entity_id else f"{base_url}/api/states"
    try:
        r = httpx.get(endpoint, headers=headers, timeout=10.0)
        return {"ok": r.status_code == 200, "status_code": r.status_code, "data": r.json()}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def hass_call_service(domain: str, service: str, payload: dict) -> dict:
    cfg = get_hass_headers()
    if not cfg:
        return {"ok": False, "error": "HASS_TOKEN not configured"}
    base_url, headers = cfg
    import httpx

    endpoint = f"{base_url}/api/services/{domain}/{service}"
    try:
        r = httpx.post(endpoint, headers=headers, json=payload, timeout=10.0)
        return {"ok": r.status_code in (200, 201), "status_code": r.status_code, "data": r.json() if r.text else {}}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def ping_host(host: str) -> dict:
    flag = "-n" if sys.platform == "win32" else "-c"
    try:
        r = subprocess.run(["ping", flag, "1", "-w", "1000", host], capture_output=True, text=True, timeout=5)
        online = r.returncode == 0
        return {"ok": True, "host": host, "online": online}
    except Exception as e:
        return {"ok": False, "host": host, "error": str(e)}


def ssh_command(host: str, cmd: str) -> dict:
    try:
        r = subprocess.run(["ssh", host, cmd], capture_output=True, text=True, timeout=30)
        return {
            "ok": r.returncode == 0,
            "host": host,
            "returncode": r.returncode,
            "stdout": r.stdout.strip(),
            "stderr": r.stderr.strip(),
        }
    except Exception as e:
        return {"ok": False, "host": host, "error": str(e)}


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
    p = argparse.ArgumentParser(description="Home device & network controller")
    sub = p.add_subparsers(dest="action", required=True)

    st = sub.add_parser("state", help="Check Home Assistant entity state")
    st.add_argument("entity_id", nargs="?")

    call = sub.add_parser("call", help="Call Home Assistant service")
    call.add_argument("domain")
    call.add_argument("service")
    call.add_argument("--data", default="{}")

    pi = sub.add_parser("ping", help="Ping local host or IP")
    pi.add_argument("host")

    sc = sub.add_parser("ssh", help="Run remote SSH command on local machine")
    sc.add_argument("host")
    sc.add_argument("command")

    wl = sub.add_parser("wol", help="Send Wake-on-LAN packet")
    wl.add_argument("mac")
    wl.add_argument("--broadcast", default="255.255.255.255")

    a = p.parse_args()

    if a.action == "state":
        print(json.dumps(hass_get_state(a.entity_id), indent=2))
    elif a.action == "call":
        try:
            payload = json.loads(a.data)
        except Exception:
            payload = {}
        print(json.dumps(hass_call_service(a.domain, a.service, payload), indent=2))
    elif a.action == "ping":
        print(json.dumps(ping_host(a.host), indent=2))
    elif a.action == "ssh":
        print(json.dumps(ssh_command(a.host, a.command), indent=2))
    elif a.action == "wol":
        print(json.dumps(wake_on_lan(a.mac, a.broadcast), indent=2))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Universal Audio and Volume Controller Skill.

Controls Mac Mini system volume and mute (natively on Darwin or via SSH from Windows),
and provides universal playback controls (pause, stop, resume, status) across all
agent audio skills (online-radio, run-youtube-music, and Mac Mini media).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_HOST = "macmini"

# Discovered audio skills supported out-of-the-box
SUPPORTED_AUDIO_SKILLS = ["online-radio", "run-youtube-music"]


def is_mac_local() -> bool:
    """Return True if running directly on macOS Darwin."""
    return sys.platform == "darwin"


def run_apple_script(script: str, host: str = DEFAULT_HOST, timeout: int = 8) -> Dict[str, Any]:
    """Execute an AppleScript snippet locally on Darwin or remotely via SSH."""
    if is_mac_local():
        cmd = ["osascript", "-e", script]
    else:
        cmd = ["ssh", host, f"osascript -e {shlex.quote(script)}"]

    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if r.returncode == 0:
            return {"ok": True, "output": r.stdout.strip(), "error": None}
        return {"ok": False, "output": r.stdout.strip(), "error": r.stderr.strip() or f"Exited with code {r.returncode}"}
    except Exception as e:
        return {"ok": False, "output": "", "error": str(e)}


def get_mac_volume(host: str = DEFAULT_HOST) -> Dict[str, Any]:
    """Query Mac Mini volume settings: output volume (0-100), muted, input volume, alert volume."""
    res = run_apple_script("get volume settings", host=host)
    if not res["ok"]:
        return {
            "ok": False,
            "error": res["error"],
            "host": host,
            "is_local": is_mac_local(),
            "output_volume": None,
            "output_muted": None,
        }

    raw = res["output"]
    # Typical macOS output: "output volume:71, input volume:96, alert volume:100, output muted:false"
    out_vol = None
    in_vol = None
    alert_vol = None
    muted = None

    m_out = re.search(r"output volume:(\d+)", raw)
    if m_out:
        out_vol = int(m_out.group(1))

    m_in = re.search(r"input volume:(\d+)", raw)
    if m_in:
        in_vol = int(m_in.group(1))

    m_alert = re.search(r"alert volume:(\d+)", raw)
    if m_alert:
        alert_vol = int(m_alert.group(1))

    m_mute = re.search(r"output muted:(true|false)", raw, re.IGNORECASE)
    if m_mute:
        muted = m_mute.group(1).lower() == "true"

    return {
        "ok": True,
        "host": host,
        "is_local": is_mac_local(),
        "output_volume": out_vol,
        "output_muted": muted,
        "input_volume": in_vol,
        "alert_volume": alert_vol,
        "raw": raw,
    }


def set_mac_volume(level: int, host: str = DEFAULT_HOST) -> Dict[str, Any]:
    """Set Mac Mini output volume to an absolute percentage (0-100)."""
    level = max(0, min(100, int(level)))
    script = f"set volume output volume {level}"
    res = run_apple_script(script, host=host)
    if not res["ok"]:
        return {"ok": False, "error": res["error"], "level": level}

    status = get_mac_volume(host=host)
    status["requested_volume"] = level
    return status


def adjust_mac_volume(delta: int, host: str = DEFAULT_HOST) -> Dict[str, Any]:
    """Adjust Mac Mini volume up (+) or down (-) by delta."""
    curr = get_mac_volume(host=host)
    current_vol = curr.get("output_volume") or 50
    target_vol = max(0, min(100, current_vol + delta))
    return set_mac_volume(target_vol, host=host)


def set_mac_mute(muted: bool, host: str = DEFAULT_HOST) -> Dict[str, Any]:
    """Set Mac Mini output mute state."""
    val = "true" if muted else "false"
    script = f"set volume output muted {val}"
    res = run_apple_script(script, host=host)
    if not res["ok"]:
        return {"ok": False, "error": res["error"], "muted": muted}
    return get_mac_volume(host=host)


def toggle_mac_mute(host: str = DEFAULT_HOST) -> Dict[str, Any]:
    """Toggle Mac Mini output mute state."""
    curr = get_mac_volume(host=host)
    current_muted = curr.get("output_muted", False)
    return set_mac_mute(not current_muted, host=host)


def invoke_skill_command(skill_name: str, script_name: str, args: List[str]) -> Dict[str, Any]:
    """Invoke a command on another skill using workspace runner or in-process execution."""
    root = Path(__file__).resolve().parents[3]
    agent_py = root / "runtime" / "agent.py"

    cmd = [sys.executable, str(agent_py), "run", skill_name, script_name] + args
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=12)
        out = r.stdout.strip()
        data = None
        try:
            data = json.loads(out)
        except Exception:
            pass
        return {
            "ok": r.returncode == 0,
            "skill": skill_name,
            "command": args,
            "data": data,
            "output": out,
            "error": r.stderr.strip() if r.returncode != 0 else None,
        }
    except Exception as e:
        return {"ok": False, "skill": skill_name, "command": args, "error": str(e)}


def pause_mac_media(host: str = DEFAULT_HOST) -> Dict[str, Any]:
    """Pause Music app on Mac Mini if running."""
    script = (
        'tell application "System Events" to set isRunning to (name of processes contains "Music")\n'
        'if isRunning then\n'
        '  tell application "Music" to pause\n'
        '  return "paused"\n'
        'end if\n'
        'return "not_running"'
    )
    return run_apple_script(script, host=host, timeout=3)


def stop_mac_media(host: str = DEFAULT_HOST) -> Dict[str, Any]:
    """Stop Music app on Mac Mini if running."""
    script = (
        'tell application "System Events" to set isRunning to (name of processes contains "Music")\n'
        'if isRunning then\n'
        '  tell application "Music" to stop\n'
        '  return "stopped"\n'
        'end if\n'
        'return "not_running"'
    )
    return run_apple_script(script, host=host, timeout=3)


def pause_all_audio(target: str = "all", host: str = DEFAULT_HOST) -> Dict[str, Any]:
    """Pause running music or video across radio, youtube, and mac mini media."""
    results: Dict[str, Any] = {}
    target_clean = target.lower().strip()

    # 1. Radio skill
    if target_clean in ("all", "radio", "online-radio"):
        results["online-radio"] = invoke_skill_command("online-radio", "main.py", ["pause"])

    # 2. YouTube skill
    if target_clean in ("all", "youtube", "music", "run-youtube-music"):
        results["run-youtube-music"] = invoke_skill_command("run-youtube-music", "main.py", ["pause"])

    # 3. Mac Mini Safari/Music
    if target_clean in ("all", "macmini", "mac", "safari"):
        results["macmini_media"] = pause_mac_media(host=host)

    return {
        "ok": True,
        "action": "pause",
        "target": target,
        "details": results,
    }


def stop_all_audio(target: str = "all", host: str = DEFAULT_HOST) -> Dict[str, Any]:
    """Stop running music or video across radio, youtube, and mac mini media."""
    results: Dict[str, Any] = {}
    target_clean = target.lower().strip()

    # 1. Radio skill
    if target_clean in ("all", "radio", "online-radio"):
        results["online-radio"] = invoke_skill_command("online-radio", "main.py", ["stop"])

    # 2. YouTube skill
    if target_clean in ("all", "youtube", "music", "run-youtube-music"):
        results["run-youtube-music"] = invoke_skill_command("run-youtube-music", "main.py", ["stop"])

    # 3. Mac Mini Safari/Music
    if target_clean in ("all", "macmini", "mac", "safari"):
        results["macmini_media"] = stop_mac_media(host=host)

    return {
        "ok": True,
        "action": "stop",
        "target": target,
        "details": results,
    }


def resume_all_audio(target: str = "all", host: str = DEFAULT_HOST) -> Dict[str, Any]:
    """Resume paused music across youtube and media skills."""
    results: Dict[str, Any] = {}
    target_clean = target.lower().strip()

    if target_clean in ("all", "youtube", "music", "run-youtube-music"):
        results["run-youtube-music"] = invoke_skill_command("run-youtube-music", "main.py", ["resume"])

    if target_clean in ("all", "macmini", "mac", "music_app"):
        results["macmini_music"] = run_apple_script('try\n tell application "Music" to play\nend try', host=host)

    return {
        "ok": True,
        "action": "resume",
        "target": target,
        "details": results,
    }


def get_full_audio_status(host: str = DEFAULT_HOST) -> Dict[str, Any]:
    """Get complete audio status: Mac Mini volume/mute + all audio skills playback states."""
    vol_status = get_mac_volume(host=host)

    radio_status = invoke_skill_command("online-radio", "main.py", ["status"])
    youtube_status = invoke_skill_command("run-youtube-music", "main.py", ["status"])

    return {
        "ok": True,
        "macmini": vol_status,
        "skills": {
            "online-radio": radio_status.get("data", {}).get("playback") if radio_status.get("ok") else None,
            "run-youtube-music": youtube_status.get("data", {}).get("playback") if youtube_status.get("ok") else None,
        },
        "supported_skills": SUPPORTED_AUDIO_SKILLS,
    }


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="audio-control",
        description="Universal audio and volume controller for Mac Mini and agent media skills.",
    )
    p.add_argument("--host", default=DEFAULT_HOST, help=f"Mac Mini SSH host or alias (default: {DEFAULT_HOST})")

    sub = p.add_subparsers(dest="command")

    # status
    sub.add_parser("status", help="Get comprehensive audio status (Mac Mini volume + skills playback)")

    # volume
    p_vol = sub.add_parser("volume", help="Get or set Mac Mini output volume")
    p_vol.add_argument("level", nargs="?", type=int, default=None, help="Volume percentage (0-100). If omitted, returns current volume.")

    # up
    p_up = sub.add_parser("up", help="Increase Mac Mini volume")
    p_up.add_argument("step", nargs="?", type=int, default=10, help="Volume increase percentage (default 10)")

    # down
    p_down = sub.add_parser("down", help="Decrease Mac Mini volume")
    p_down.add_argument("step", nargs="?", type=int, default=10, help="Volume decrease percentage (default 10)")

    # mute
    sub.add_parser("mute", help="Mute Mac Mini audio output")

    # unmute
    sub.add_parser("unmute", help="Unmute Mac Mini audio output")

    # toggle-mute
    sub.add_parser("toggle-mute", help="Toggle Mac Mini audio output mute state")

    # pause
    p_pause = sub.add_parser("pause", help="Pause running music/video across all or specific audio skills")
    p_pause.add_argument("--target", "-t", default="all", choices=["all", "radio", "youtube", "macmini"], help="Target audio source (default 'all')")

    # stop
    p_stop = sub.add_parser("stop", help="Stop running music/video across all or specific audio skills")
    p_stop.add_argument("--target", "-t", default="all", choices=["all", "radio", "youtube", "macmini"], help="Target audio source (default 'all')")

    # resume
    p_resume = sub.add_parser("resume", help="Resume paused audio")
    p_resume.add_argument("--target", "-t", default="all", choices=["all", "youtube", "macmini"], help="Target audio source (default 'all')")

    # list-skills
    sub.add_parser("list-skills", help="List registered audio skills")

    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        # Default behavior: show full status
        res = get_full_audio_status(host=args.host)
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0

    try:
        if args.command == "status":
            res = get_full_audio_status(host=args.host)
            print(json.dumps(res, ensure_ascii=False, indent=2))
            return 0 if res.get("ok") else 1

        elif args.command == "volume":
            if args.level is not None:
                res = set_mac_volume(args.level, host=args.host)
            else:
                res = get_mac_volume(host=args.host)
            print(json.dumps(res, ensure_ascii=False, indent=2))
            return 0 if res.get("ok") else 1

        elif args.command == "up":
            res = adjust_mac_volume(args.step, host=args.host)
            print(json.dumps(res, ensure_ascii=False, indent=2))
            return 0 if res.get("ok") else 1

        elif args.command == "down":
            res = adjust_mac_volume(-args.step, host=args.host)
            print(json.dumps(res, ensure_ascii=False, indent=2))
            return 0 if res.get("ok") else 1

        elif args.command == "mute":
            res = set_mac_mute(True, host=args.host)
            print(json.dumps(res, ensure_ascii=False, indent=2))
            return 0 if res.get("ok") else 1

        elif args.command == "unmute":
            res = set_mac_mute(False, host=args.host)
            print(json.dumps(res, ensure_ascii=False, indent=2))
            return 0 if res.get("ok") else 1

        elif args.command == "toggle-mute":
            res = toggle_mac_mute(host=args.host)
            print(json.dumps(res, ensure_ascii=False, indent=2))
            return 0 if res.get("ok") else 1

        elif args.command == "pause":
            res = pause_all_audio(target=args.target, host=args.host)
            print(json.dumps(res, ensure_ascii=False, indent=2))
            return 0

        elif args.command == "stop":
            res = stop_all_audio(target=args.target, host=args.host)
            print(json.dumps(res, ensure_ascii=False, indent=2))
            return 0

        elif args.command == "resume":
            res = resume_all_audio(target=args.target, host=args.host)
            print(json.dumps(res, ensure_ascii=False, indent=2))
            return 0

        elif args.command == "list-skills":
            print(json.dumps({"ok": True, "skills": SUPPORTED_AUDIO_SKILLS}, ensure_ascii=False, indent=2))
            return 0

    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())

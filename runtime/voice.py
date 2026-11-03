"""Voice Conversation Controller for Antigravity Personal Agent.
Manages the Apple Silicon M4 Voice Engine (sounddevice, mlx-whisper, Zuzana TTS)
both in local mode (when deployed on macOS Darwin) and remote mode (Windows -> Mac Mini via SSH).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "runtime"))
import core  # noqa: E402
import llm_chat  # noqa: E402

_lock = threading.RLock()
_process: subprocess.Popen | None = None
_monitor_thread: threading.Thread | None = None
_stop_event = threading.Event()

_status = {
    "active": False,
    "state": "idle",  # idle, listening, pause, button_pressed, transcribing, thinking, speaking
    "last_partial": "",
    "last_user_text": "",
    "last_agent_text": "",
    "silence_remaining": 0.0,
    "platform": sys.platform,
    "mode": "local" if sys.platform == "darwin" else "remote_macmini",
    "language": "cs",
    "voice": "Zuzana (Premium)",
    "started_at": None,
    "last_open_skill": None,
    "last_skill_args": [],
    "last_executed_tools": [],
    "turn_id": 0,
    "recent_events": [],
}


def _record_event(evt_type: str, data: dict | None = None) -> None:
    global _status
    entry = {
        "event": evt_type,
        "time": core.utc_now_iso(),
        "data": data or {},
    }
    _status["recent_events"].append(entry)
    if len(_status["recent_events"]) > 30:
        _status["recent_events"] = _status["recent_events"][-30:]


def is_active() -> bool:
    with _lock:
        if _process is None:
            return False
        return _process.poll() is None


def get_status() -> dict:
    with _lock:
        st = dict(_status)
        st["active"] = is_active()
        st["recent_events"] = list(_status["recent_events"])
        return st


def _send_command(cmd: dict) -> bool:
    with _lock:
        if _process and _process.poll() is None and _process.stdin:
            try:
                line = json.dumps(cmd, ensure_ascii=False) + "\n"
                _process.stdin.write(line.encode("utf-8"))
                _process.stdin.flush()
                return True
            except Exception:
                return False
    return False


def speak_text(text: str, voice: str | None = None) -> bool:
    """Manually command the voice engine to speak a phrase."""
    return _send_command({"action": "speak", "text": text, "voice": voice or _status.get("voice")})


def _worker_monitor(proc: subprocess.Popen, session_id: str):
    """Background monitor thread to read worker stdout/events and route LLM turns."""
    global _status

    try:
        for raw_line in iter(proc.stdout.readline, b""):
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line:
                continue

            if "__VOICE_EVENT__" in line:
                try:
                    payload_raw = line.split("__VOICE_EVENT__", 1)[1].strip()
                    evt = json.loads(payload_raw)
                    etype = evt.get("event")
                    edata = evt.get("data", {})

                    with _lock:
                        if etype == "ready":
                            _status["state"] = "listening"
                            _record_event("ready", edata)
                        elif etype == "listening_idle":
                            _status["state"] = "listening"
                            _status["last_partial"] = ""
                            _status["silence_remaining"] = 0.0
                            _record_event("listening_idle")
                        elif etype == "speech_start":
                            _status["state"] = "listening"
                            _status["silence_remaining"] = 4.0
                            _record_event("speech_start", edata)
                        elif etype == "partial":
                            _status["state"] = "listening"
                            _status["last_partial"] = edata.get("text", "")
                            _record_event("partial", edata)
                        elif etype == "silence_countdown":
                            _status["state"] = "pause"
                            _status["silence_remaining"] = edata.get("remaining", 0.0)
                        elif etype in ("speech_interrupted", "speaking_interrupted"):
                            _status["state"] = "listening"
                            _record_event(etype, edata)
                        elif etype == "button_press":
                            _status["state"] = "button_pressed"
                            _record_event("button_press")
                        elif etype == "transcribing_start":
                            _status["state"] = "transcribing"
                            _record_event("transcribing_start", edata)
                        elif etype == "speaking_start":
                            _status["state"] = "speaking"
                            _record_event("speaking_start", edata)
                        elif etype == "speaking_end":
                            _status["state"] = "cooldown"
                            _record_event("speaking_end", edata)
                        elif etype == "stopped":
                            _status["state"] = "idle"
                            _status["active"] = False
                            _record_event("stopped", edata)

                    # When speech transcription is complete, route to agent LLM
                    if etype == "user_speech":
                        user_text = edata.get("text", "").strip()
                        if user_text:
                            with _lock:
                                _status["last_user_text"] = user_text
                                _status["last_partial"] = ""
                                _status["state"] = "thinking"
                                _record_event("user_speech", edata)

                            # Send to agent chat brain
                            try:
                                agent_res = llm_chat.send_chat_message(user_text, session_id=session_id)
                                response_text = agent_res.get("reply") or agent_res.get("response") or ""
                                with _lock:
                                    _status["last_agent_text"] = response_text
                                    _status["last_open_skill"] = agent_res.get("open_skill")
                                    _status["last_skill_args"] = agent_res.get("skill_args") or []
                                    _status["last_executed_tools"] = agent_res.get("executed_tools") or []
                                    _status["turn_id"] = _status.get("turn_id", 0) + 1
                                    _record_event("agent_reply", {
                                        "text": response_text,
                                        "open_skill": agent_res.get("open_skill"),
                                        "skill_args": agent_res.get("skill_args"),
                                        "executed_tools": agent_res.get("executed_tools"),
                                        "turn_id": _status["turn_id"],
                                    })

                                # Send speak command to voice worker
                                if response_text:
                                    _send_command({"action": "speak", "text": response_text, "voice": _status.get("voice")})
                            except Exception as e:
                                err_msg = (
                                    f"Sorry, an error occurred: {e}"
                                    if _status.get("language") == "en"
                                    else f"Omlouvám se, došlo k chybě: {e}"
                                )
                                _send_command({"action": "speak", "text": err_msg, "voice": _status.get("voice")})
                except Exception:
                    pass
    except Exception:
        pass
    finally:
        with _lock:
            _status["active"] = False
            _status["state"] = "idle"
            _record_event("worker_exited")


def start(session_id: str = "default", voice: str | None = None, device: str | None = None, language: str = "cs") -> dict:
    """Start the voice conversation loop."""
    global _process, _monitor_thread, _status

    if not voice:
        voice = "Samantha" if language == "en" else "Zuzana (Premium)"

    with _lock:
        if _process and _process.poll() is None:
            return {"ok": True, "message": "Voice conversation is already running", "status": get_status()}

        _status["active"] = True
        _status["state"] = "starting"
        _status["language"] = language
        _status["voice"] = voice
        _status["started_at"] = core.utc_now_iso()
        _status["recent_events"] = []

        is_darwin = sys.platform == "darwin"
        _status["mode"] = "local" if is_darwin else "remote_macmini"

    try:
        if sys.platform == "darwin":
            # Running natively on macOS / Apple Silicon
            worker_path = str(ROOT / "runtime" / "voice_worker.py")
            py_bin = sys.executable
            # Check if specialized voice venv exists
            custom_py = Path.home() / ".venv-voice" / "bin" / "python"
            if custom_py.exists():
                py_bin = str(custom_py)

            cmd = [py_bin, "-u", worker_path, "--managed", "--voice", voice, "--language", language]
            if device:
                cmd.extend(["--device", str(device)])

            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
            )
        else:
            # Running on Windows: bridge to Mac Mini over SSH
            # Sync latest voice_worker.py first
            local_worker = ROOT / "runtime" / "voice_worker.py"
            subprocess.run(["scp", str(local_worker), "macmini:~/voice_worker.py"], check=False, timeout=10)

            # Launch on Mac Mini using ~/.venv-voice/bin/python with unbuffered I/O
            remote_cmd = f"~/.venv-voice/bin/python -u ~/voice_worker.py --managed --voice '{voice}' --language '{language}'"
            if device:
                remote_cmd += f" --device '{device}'"

            cmd = ["ssh", "-o", "ServerAliveInterval=15", "macmini", remote_cmd]
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
            )

        with _lock:
            _process = proc
            _status["active"] = True
            _status["state"] = "initializing"

        mon = threading.Thread(target=_worker_monitor, args=(proc, session_id), daemon=True)
        mon.start()
        _monitor_thread = mon

        return {"ok": True, "message": "Voice conversation started", "mode": _status["mode"], "voice": voice, "language": language}
    except Exception as e:
        with _lock:
            _status["active"] = False
            _status["state"] = "error"
            _record_event("error", {"error": str(e)})
        return {"ok": False, "error": f"Failed to start voice loop: {e}"}


def stop() -> dict:
    """Stop the active voice conversation loop."""
    global _process, _status

    with _lock:
        if not _process or _process.poll() is not None:
            _status["active"] = False
            _status["state"] = "idle"
            return {"ok": True, "message": "Voice loop is not running"}

        proc = _process

    # Send graceful stop command
    _send_command({"action": "stop"})
    time.sleep(0.5)

    if proc.poll() is None:
        try:
            proc.terminate()
            proc.wait(timeout=2.0)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    with _lock:
        _process = None
        _status["active"] = False
        _status["state"] = "idle"
        _record_event("stopped_by_user")

    return {"ok": True, "message": "Voice conversation stopped"}

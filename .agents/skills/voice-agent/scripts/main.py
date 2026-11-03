"""CLI script for Voice Agent Skill."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "runtime"))
import voice  # noqa: E402


def cmd_status(_args):
    st = voice.get_status()
    print(json.dumps({"ok": True, "status": st}, ensure_ascii=False, indent=2))


def cmd_start(args):
    res = voice.start(session_id=args.session, voice=args.voice, device=args.device, language=args.language)
    print(json.dumps(res, ensure_ascii=False, indent=2))


def cmd_stop(_args):
    res = voice.stop()
    print(json.dumps(res, ensure_ascii=False, indent=2))


def cmd_speak(args):
    text = " ".join(args.text).strip()
    if not text:
        print(json.dumps({"ok": False, "error": "No text provided"}, ensure_ascii=False))
        return
    ok = voice.speak_text(text)
    print(json.dumps({"ok": ok, "spoken": text}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description="Voice Agent Controller CLI")
    sub = parser.add_subparsers(dest="command")

    p_status = sub.add_parser("status", help="Get voice loop status")
    p_status.set_defaults(func=cmd_status)

    p_start = sub.add_parser("start", help="Start voice loop")
    p_start.add_argument("--session", default="default", help="Chat session ID")
    p_start.add_argument("--language", choices=["cs", "en"], default="cs", help="Spoken language: cs (Czech) or en (English)")
    p_start.add_argument("--voice", default=None, help="TTS voice name (defaults to Zuzana for cs, Samantha for en)")
    p_start.add_argument("--device", default=None, help="Input audio device")
    p_start.set_defaults(func=cmd_start)

    p_stop = sub.add_parser("stop", help="Stop voice loop")
    p_stop.set_defaults(func=cmd_stop)

    p_speak = sub.add_parser("speak", help="Speak text via TTS")
    p_speak.add_argument("text", nargs="+", help="Text to speak")
    p_speak.set_defaults(func=cmd_speak)

    args = parser.parse_args()
    if hasattr(args, "func"):
        args.func(args)
    else:
        cmd_status(args)


if __name__ == "__main__":
    main()

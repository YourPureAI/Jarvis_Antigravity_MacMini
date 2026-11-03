"""Shim for cached Antigravity hooks referencing runtime/agent.py from .agents/"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "runtime"))
import agent  # noqa: E402

if __name__ == "__main__":
    agent.main()


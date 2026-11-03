"""Bridge to runtime/agent.py for lifecycle hooks executed from .agents/"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "runtime"))
import agent  # noqa: E402

if __name__ == "__main__":
    agent.main()


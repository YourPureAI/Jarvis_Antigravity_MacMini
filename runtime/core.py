"""Shared kernel helpers: paths, database access, output. Stdlib only."""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AGENTS_DIR = ROOT / ".agents"
SKILLS_DIR = AGENTS_DIR / "skills"
RULES_DIR = AGENTS_DIR / "rules"
DATA_DIR = Path(os.environ.get("AGENT_DATA_DIR") or ROOT / "data")  # override for tests
ENVS_DIR = ROOT / "envs"
LOGS_DIR = ROOT / "logs"
BACKUPS_DIR = ROOT / "backups"
AGENT_DB = DATA_DIR / "agent.db"

SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def get_data_dir() -> Path:
    return Path(os.environ.get("AGENT_DATA_DIR") or ROOT / "data")


def ensure_dirs() -> None:
    d = get_data_dir()
    for p in (d, ENVS_DIR, LOGS_DIR, BACKUPS_DIR, d / "skills"):
        p.mkdir(parents=True, exist_ok=True)


def connect(path: Path | None = None) -> sqlite3.Connection:
    ensure_dirs()
    target = path or (get_data_dir() / "agent.db")
    target.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(target, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    return con


def now_local() -> datetime:
    """Timezone-aware local time (minute precision is what the scheduler needs)."""
    return datetime.now().astimezone()


def iso(dt: datetime | None) -> str | None:
    return dt.astimezone().isoformat(timespec="seconds") if dt else None


def parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.astimezone()  # interpret naive values as local time
    return dt


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def out(obj) -> None:
    """Print compact JSON (token-efficient, machine-readable)."""
    sys.stdout.write(json.dumps(obj, ensure_ascii=False, default=str) + "\n")


def fail(msg: str, code: int = 1) -> None:
    out({"ok": False, "error": msg})
    if os.environ.get("AGENT_NO_EXIT"):
        raise RuntimeError(msg)
    sys.exit(code)


def skill_dir(name: str) -> Path:
    if not SKILL_NAME_RE.match(name):
        fail(f"invalid skill name '{name}' (lowercase-hyphenated)")
    return SKILLS_DIR / name


def skill_data_dir(name: str) -> Path:
    d = DATA_DIR / "skills" / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def python_exe() -> str:
    return sys.executable


def env_flag(name: str) -> bool:
    return os.environ.get(name, "").lower() in ("1", "true", "yes")

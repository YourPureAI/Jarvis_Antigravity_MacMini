"""Single CLI entry point for the agent runtime.  Usage: python runtime/agent.py <command> ... (-h for help)
All commands print compact JSON. Stdlib only - runs with any Python >= 3.11 without a venv."""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import backup  # noqa: E402
import inbox  # noqa: E402
import memory  # noqa: E402
import scheduler  # noqa: E402
import secrets_store  # noqa: E402
import skills  # noqa: E402
from core import ROOT, ensure_dirs, out  # noqa: E402

SIDECAR_ID = "agent-heartbeat"
SIDECAR_DIR = ROOT / ".agents" / "sidecars" / SIDECAR_ID


def _tz_name() -> str:
    """Best-effort IANA timezone of the host."""
    try:
        r = subprocess.run(["tzutil", "/g"], capture_output=True, text=True, timeout=10)
        win = r.stdout.strip()
        mapping = {"Central Europe Standard Time": "Europe/Prague", "Central European Standard Time": "Europe/Warsaw",
                   "W. Europe Standard Time": "Europe/Berlin", "GMT Standard Time": "Europe/London",
                   "UTC": "UTC", "Eastern Standard Time": "America/New_York",
                   "Pacific Standard Time": "America/Los_Angeles"}
        return mapping.get(win, "UTC")
    except Exception:
        return "UTC"


def heartbeat_install(a) -> None:
    """Write the Antigravity scheduled sidecar that calls `agent.py tick` every minute (user enables it)."""
    py = Path(sys.executable)
    pyw = py.with_name("pythonw.exe")
    exe = str(pyw if (a.windowless and pyw.exists()) else py)
    cfg = {
        "builtin": "schedule",
        "args": [f"CRON_TZ={a.tz or _tz_name()} * * * * *", exe, str(ROOT / "runtime" / "agent.py"), "tick"],
        "restart_policy": "always",
        "display_name": "Agent Heartbeat",
        "description": "Every minute runs the personal agent scheduler (runtime/agent.py tick). Deterministic, "
                       "no LLM tokens unless a job of kind 'agent' is due.",
        "agent_permissions": {"workspace_uris": [ROOT.as_uri()]},
    }
    SIDECAR_DIR.mkdir(parents=True, exist_ok=True)
    (SIDECAR_DIR / "sidecar.json").write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    out({"ok": True, "sidecar": str(SIDECAR_DIR / "sidecar.json"),
         "next": "User must enable 'Agent Heartbeat' in the Automations dashboard (sidecar://dashboard)."})


def setup(a) -> None:
    ensure_dirs()
    if not (ROOT / ".git").exists():
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=ROOT, check=True)
    memory.db()
    memory.render_profile()
    inbox.db()
    con = scheduler.db()
    if not con.execute("SELECT 1 FROM jobs WHERE id='daily-backup'").fetchone():
        args = argparse.Namespace(id="daily-backup", kind="core", at=None, cron="30 3 * * *", every=None,
                                  description="Daily git backup (+push if remote configured)", title=None,
                                  message=None, skill=None, script=None, args_json="[]", notify="on_error",
                                  timeout=900, action="backup", prompt=None, model="flash", replace=False)
        scheduler._add(args)
    out({"ok": True, "root": str(ROOT)})


def _agentapi_ok() -> str:
    try:
        return " ".join(scheduler._agentapi())
    except Exception as e:
        return f"MISSING ({e})"


def doctor(_a) -> None:
    checks = {
        "python": sys.version.split()[0],
        "uv": shutil.which("uv") or "MISSING",
        "git": shutil.which("git") or "MISSING",
        "agentapi": _agentapi_ok(),
        "git_repo": (ROOT / ".git").exists(),
        "git_remote": backup.git("remote", "get-url", "origin").stdout.strip() or "none",
        "heartbeat": scheduler.heartbeat_status(),
        "heartbeat_sidecar_written": (SIDECAR_DIR / "sidecar.json").exists(),
        "secrets": len(secrets_store._load()),
    }
    errs, warns, always = skills.lint_rules()
    checks["rules_always_on_chars"] = always
    checks["rule_errors"] = errs
    out({"ok": not errs and checks["uv"] != "MISSING" and checks["git"] != "MISSING", "checks": checks})


def daemon(_a) -> None:
    """Run tick in a simple minute loop locally (stops with Ctrl+C)."""
    import time
    from core import now_local
    out({"ok": True, "daemon": "running", "started": str(now_local())})
    while True:
        try:
            scheduler.tick()
        except Exception as e:
            sys.stderr.write(f"daemon tick error: {e}\n")
        time.sleep(60)


def web(a) -> None:
    from web.server import start_server
    start_server(host=a.host, port=a.port, open_browser=a.open)


def hook(a) -> None:
    import hooks

    hooks.main(a.event)


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="agent.py", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    for mod in (memory, scheduler, inbox, skills, secrets_store, backup):
        mod.register(sub)
    sub.add_parser("setup", help="idempotent first-time initialisation").set_defaults(fn=setup)
    sub.add_parser("doctor", help="health check").set_defaults(fn=doctor)
    sub.add_parser("daemon", help="run heartbeat tick locally in a loop").set_defaults(fn=daemon)
    w = sub.add_parser("web", help="launch local agent Web UI dashboard & skill widgets")
    w.add_argument("--host", default="0.0.0.0", help="HTTP bind host (default 0.0.0.0)")
    w.add_argument("--port", type=int, default=8765, help="HTTP port (default 8765)")
    w.add_argument("--open", action="store_true", help="open web browser automatically")
    w.set_defaults(fn=web)
    hb = sub.add_parser("heartbeat-install", help="write the project-local Antigravity scheduler sidecar")
    hb.add_argument("--tz")
    hb.add_argument("--windowless", action="store_true", help="use pythonw.exe")
    hb.set_defaults(fn=heartbeat_install)
    h = sub.add_parser("hook", help="(internal) Antigravity lifecycle hooks")
    h.add_argument("event", choices=("pre-tool", "session-start"))
    h.set_defaults(fn=hook)
    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()

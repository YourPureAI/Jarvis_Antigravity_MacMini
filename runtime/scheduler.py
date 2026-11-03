"""Job scheduler. One heartbeat (`agent.py tick`, every minute) executes due jobs from SQLite.
Job kinds:
  notify - desktop toast + inbox entry (0 tokens)
  script - run a skill script in its venv (0 tokens)
  core   - whitelisted runtime action, e.g. backup (0 tokens)
  agent  - start a fresh Antigravity conversation via `agentapi new-conversation` (costs tokens)
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from core import DATA_DIR, LOGS_DIR, ROOT, connect, fail, iso, now_local, out, parse_iso
from cron import Cron

KINDS = ("notify", "script", "core", "agent")
CORE_ACTIONS = ("backup",)
AGENT_MODELS = ("flash_lite", "flash", "pro")
LOCK = DATA_DIR / "tick.lock"
HEARTBEAT = DATA_DIR / "heartbeat.txt"
LOCK_STALE_MIN = 30
CATCHUP_GRACE_MIN = 24 * 60  # missed recurring runs older than this are skipped, not executed

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs(
  id TEXT PRIMARY KEY,
  description TEXT NOT NULL DEFAULT '',
  kind TEXT NOT NULL,
  sched_type TEXT NOT NULL,          -- once | cron | every
  spec TEXT NOT NULL,                -- ISO datetime | cron expr | minutes
  payload TEXT NOT NULL DEFAULT '{}',
  enabled INTEGER NOT NULL DEFAULT 1,
  next_run TEXT,
  last_run TEXT,
  last_status TEXT,
  last_output TEXT,
  run_count INTEGER NOT NULL DEFAULT 0,
  fail_count INTEGER NOT NULL DEFAULT 0,
  created TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS job_runs(
  id INTEGER PRIMARY KEY,
  job_id TEXT NOT NULL,
  started TEXT NOT NULL,
  status TEXT NOT NULL,
  output TEXT
);
"""

_REL_RE = re.compile(r"^\+(\d+)([mhdw])$")


def db():
    con = connect()
    con.executescript(SCHEMA)
    return con


def parse_when(s: str) -> datetime:
    m = _REL_RE.match(s.strip())
    if m:
        n, u = int(m.group(1)), m.group(2)
        delta = {"m": timedelta(minutes=n), "h": timedelta(hours=n), "d": timedelta(days=n), "w": timedelta(weeks=n)}[u]
        return (now_local() + delta).replace(second=0, microsecond=0)
    dt = parse_iso(s)
    if not dt:
        fail(f"cannot parse time '{s}'")
    return dt


def compute_next(sched_type: str, spec: str, after: datetime) -> datetime | None:
    if sched_type == "cron":
        return Cron(spec).next_after(after)
    if sched_type == "every":
        return (after + timedelta(minutes=int(spec))).replace(second=0, microsecond=0)
    return None  # once: no further runs


# ---- execution -------------------------------------------------------------

def _agentapi() -> list[str]:
    # Prefer the real executable: .bat wrappers go through cmd.exe, which mangles quotes/&/| in prompts.
    agy = Path.home() / ".gemini" / "bin" / ("agy.exe" if os.name == "nt" else "agy")
    if agy.exists():
        return [str(agy), "agentapi"]
    exe = shutil.which("agentapi")
    if exe and not exe.lower().endswith((".bat", ".cmd")):
        return [exe]
    raise RuntimeError("agentapi executable not found")


def agent_prompt(job_id: str, prompt: str) -> str:
    """Single-line, self-contained prompt for an unattended run (newlines can truncate agentapi args)."""
    r = str(ROOT)
    text = (
        f"[Scheduled job '{job_id}' - unattended run of the personal agent] Workspace: {r} . "
        f"First read {r}\\.agents\\rules\\00-core.md and {r}\\.agents\\rules\\10-safety.md and obey them. "
        f"Unattended mode: never ask questions, never perform confirm-tier or forbidden actions (record them as "
        f"pending instead), use existing skills in {r}\\.agents\\skills, keep tool calls minimal. "
        f"Finish by recording the outcome: python {r}\\runtime\\agent.py notify \"<short title>\" \"<result summary>\" "
        f"--source job:{job_id} . TASK: {prompt}"
    )
    return " ".join(text.split())


def execute(job: dict) -> tuple[str, str]:
    """Run one job. Returns (status, output)."""
    payload = json.loads(job["payload"] or "{}")
    kind = job["kind"]
    try:
        if kind == "notify":
            from inbox import notify

            res = notify(payload.get("title", "Reminder"), payload.get("message", ""), f"job:{job['id']}")
            return "ok", json.dumps(res)
        if kind == "script":
            from skills import run_skill

            rc, output = run_skill(payload["skill"], payload["script"], payload.get("args", []), capture=True,
                                   timeout=int(payload.get("timeout", 900)))
            status = "ok" if rc == 0 else f"error rc={rc}"
            mode = payload.get("notify", "on_error")
            if mode == "always" or (mode == "on_error" and rc != 0):
                from inbox import notify

                notify(f"Job {job['id']}: {status}", output[-500:], f"job:{job['id']}", "info" if rc == 0 else "error")
            return status, output[-4000:]
        if kind == "core":
            action = payload.get("action")
            if action == "backup":
                from backup import backup_now

                res = backup_now(f"scheduled backup ({job['id']})")
                return ("ok" if res.get("ok") else "error"), json.dumps(res)[-4000:]
            return "error", f"unknown core action {action}"
        if kind == "agent":
            model = payload.get("model", "flash")
            cmd = _agentapi() + ["new-conversation", f"--model={model}", f"--title=Job: {job['id']}", "--",
                                 agent_prompt(job["id"], payload["prompt"])]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=120, encoding="utf-8", errors="replace",
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            return ("ok" if r.returncode == 0 else f"error rc={r.returncode}"), (r.stdout + r.stderr)[-4000:]
        return "error", f"unknown kind {kind}"
    except subprocess.TimeoutExpired:
        return "error timeout", ""
    except SystemExit as e:  # fail() inside helpers
        return "error", f"aborted ({e})"
    except Exception as e:  # never let one job break the heartbeat
        return "error", f"{type(e).__name__}: {e}"


def run_job(con, job: dict, now: datetime, manual: bool = False) -> dict:
    status, output = execute(job)
    nxt = None if manual and job["sched_type"] == "once" else compute_next(job["sched_type"], job["spec"], now)
    enabled = job["enabled"]
    if job["sched_type"] == "once" and not manual:
        enabled = 0
    failed = not status.startswith("ok")
    con.execute(
        "UPDATE jobs SET last_run=?, last_status=?, last_output=?, run_count=run_count+1, "
        "fail_count=CASE WHEN ? THEN fail_count+1 ELSE 0 END, next_run=COALESCE(?, next_run), enabled=? WHERE id=?",
        (iso(now), status, output, int(failed), iso(nxt) if nxt else None, enabled, job["id"]),
    )
    con.execute("INSERT INTO job_runs(job_id, started, status, output) VALUES(?,?,?,?)",
                (job["id"], iso(now), status, output[-1000:]))
    con.execute("DELETE FROM job_runs WHERE id NOT IN (SELECT id FROM job_runs ORDER BY id DESC LIMIT 500)")
    con.commit()
    if failed and not manual:
        from inbox import inbox_add

        inbox_add(f"job:{job['id']}", f"Job '{job['id']}' failed: {status}", output[-800:], "error")
    return {"id": job["id"], "status": status}


def _acquire_lock() -> bool:
    try:
        if LOCK.exists():
            age = datetime.now().timestamp() - LOCK.stat().st_mtime
            if age < LOCK_STALE_MIN * 60:
                return False
            LOCK.unlink()
        fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return True
    except FileExistsError:
        return False


def tick(_a=None) -> None:
    """Heartbeat entry point. Safe to call every minute from any scheduler."""
    now = now_local()
    HEARTBEAT.parent.mkdir(parents=True, exist_ok=True)
    HEARTBEAT.write_text(iso(now), encoding="utf-8")
    if not _acquire_lock():
        out({"ok": True, "skipped": "locked"})
        return
    results = []
    try:
        con = db()
        due = [dict(r) for r in con.execute(
            "SELECT * FROM jobs WHERE enabled=1 AND next_run IS NOT NULL ORDER BY next_run")]
        for job in due:
            nr = parse_iso(job["next_run"])
            if nr > now:
                continue
            late_min = (now - nr).total_seconds() / 60
            if job["sched_type"] != "once" and late_min > CATCHUP_GRACE_MIN:
                nxt = compute_next(job["sched_type"], job["spec"], now)
                con.execute("UPDATE jobs SET next_run=?, last_status=? WHERE id=?",
                            (iso(nxt), f"skipped missed run ({int(late_min)} min late)", job["id"]))
                con.commit()
                continue
            results.append(run_job(con, job, now))
    finally:
        LOCK.unlink(missing_ok=True)
    if results:
        LOGS_DIR.mkdir(exist_ok=True)
        with open(LOGS_DIR / "scheduler.log", "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": iso(now), "ran": results}) + "\n")
    out({"ok": True, "ran": results})


# ---- CLI -------------------------------------------------------------------

def _add(a):
    if a.kind not in KINDS:
        fail(f"kind must be one of {KINDS}")
    sched = [x for x in (("once", a.at), ("cron", a.cron), ("every", a.every)) if x[1]]
    if len(sched) != 1:
        fail("specify exactly one of --at / --cron / --every")
    stype, spec = sched[0]
    now = now_local()
    if stype == "once":
        when = parse_when(spec)
        spec, nxt = iso(when), when
        if when < now - timedelta(minutes=1):
            fail(f"--at {spec} is in the past")
    elif stype == "cron":
        try:
            nxt = Cron(spec).next_after(now)
        except ValueError as e:
            fail(f"bad cron: {e}")
    else:
        if int(spec) < 1:
            fail("--every must be >= 1 minute")
        nxt = compute_next("every", spec, now)

    payload: dict = {}
    if a.kind == "notify":
        payload = {"title": a.title or "Reminder", "message": a.message or ""}
    elif a.kind == "script":
        if not (a.skill and a.script):
            fail("script jobs need --skill and --script")
        from skills import _resolve_script, skill_dir

        _resolve_script(skill_dir(a.skill), a.script)
        try:
            sargs = json.loads(a.args_json)
            assert isinstance(sargs, list) and all(isinstance(x, str) for x in sargs)
        except Exception:
            fail("--args-json must be a JSON list of strings")
        payload = {"skill": a.skill, "script": a.script, "args": sargs, "notify": a.notify,
                   "timeout": a.timeout}
    elif a.kind == "core":
        if a.action not in CORE_ACTIONS:
            fail(f"core jobs need --action one of {CORE_ACTIONS}")
        payload = {"action": a.action}
    elif a.kind == "agent":
        if not a.prompt:
            fail("agent jobs need --prompt")
        if a.model not in AGENT_MODELS:
            fail(f"--model must be one of {AGENT_MODELS}")
        payload = {"prompt": " ".join(a.prompt.split()), "model": a.model}

    con = db()
    if con.execute("SELECT 1 FROM jobs WHERE id=?", (a.id,)).fetchone() and not a.replace:
        fail(f"job '{a.id}' exists (use --replace)")
    con.execute(
        "INSERT OR REPLACE INTO jobs(id,description,kind,sched_type,spec,payload,enabled,next_run,created) "
        "VALUES(?,?,?,?,?,?,1,?,?)",
        (a.id, a.description or "", a.kind, stype, spec, json.dumps(payload, ensure_ascii=False), iso(nxt), iso(now)),
    )
    con.commit()
    out({"ok": True, "id": a.id, "kind": a.kind, "schedule": f"{stype} {spec}", "next_run": iso(nxt),
         "heartbeat": heartbeat_status()})


def heartbeat_status() -> str:
    if not HEARTBEAT.exists():
        return "never-ran (enable the 'Agent Heartbeat' automation)"
    last = parse_iso(HEARTBEAT.read_text(encoding="utf-8").strip())
    age = (now_local() - last).total_seconds() / 60
    return "alive" if age < 3 else f"stale ({int(age)} min since last tick)"


def _list(a):
    con = db()
    sql = "SELECT id,description,kind,sched_type,spec,enabled,next_run,last_run,last_status,run_count FROM jobs"
    if not a.all:
        sql += " WHERE enabled=1"
    rows = [dict(r) for r in con.execute(sql + " ORDER BY next_run")]
    out({"ok": True, "heartbeat": heartbeat_status(), "jobs": rows})


def _show(a):
    con = db()
    r = con.execute("SELECT * FROM jobs WHERE id=?", (a.id,)).fetchone()
    if not r:
        fail(f"job '{a.id}' not found")
    runs = [dict(x) for x in con.execute(
        "SELECT started,status,substr(output,1,300) output FROM job_runs WHERE job_id=? ORDER BY id DESC LIMIT 5",
        (a.id,))]
    out({"ok": True, "job": dict(r), "recent_runs": runs})


def _set_enabled(a, value: int):
    con = db()
    r = con.execute("SELECT * FROM jobs WHERE id=?", (a.id,)).fetchone()
    if not r:
        fail(f"job '{a.id}' not found")
    nxt = r["next_run"]
    if value and r["sched_type"] != "once":
        nxt = iso(compute_next(r["sched_type"], r["spec"], now_local()))
    con.execute("UPDATE jobs SET enabled=?, next_run=? WHERE id=?", (value, nxt, a.id))
    con.commit()
    out({"ok": True, "id": a.id, "enabled": bool(value), "next_run": nxt})


def _rm(a):
    con = db()
    n = con.execute("DELETE FROM jobs WHERE id=?", (a.id,)).rowcount
    con.commit()
    out({"ok": True, "deleted": n})


def _run_now(a):
    con = db()
    r = con.execute("SELECT * FROM jobs WHERE id=?", (a.id,)).fetchone()
    if not r:
        fail(f"job '{a.id}' not found")
    job = dict(r)
    status, output = execute(job)
    con.execute("INSERT INTO job_runs(job_id, started, status, output) VALUES(?,?,?,?)",
                (a.id, iso(now_local()), status + " (manual)", output[-1000:]))
    con.commit()
    out({"ok": status.startswith("ok"), "status": status, "output": output[-1500:]})


def register(sub) -> None:
    sub.add_parser("tick", help="heartbeat: run due jobs (called every minute)").set_defaults(fn=tick)
    p = sub.add_parser("job", help="scheduled jobs")
    s = p.add_subparsers(dest="action", required=True)
    a = s.add_parser("add")
    a.add_argument("id", help="kebab-case job id")
    a.add_argument("--kind", required=True, choices=KINDS)
    a.add_argument("--at", help="once: ISO local time (2026-11-03T08:00) or relative +15m/+2h/+1d/+1w")
    a.add_argument("--cron", help="recurring: 5-field cron in LOCAL time, e.g. '0 7 * * 1-5'")
    a.add_argument("--every", help="recurring: interval in minutes")
    a.add_argument("--description", "-d")
    a.add_argument("--title")
    a.add_argument("--message")
    a.add_argument("--skill")
    a.add_argument("--script")
    a.add_argument("--args-json", default="[]", help='script args as JSON list, e.g. \'["--city","Prague"]\'')
    a.add_argument("--notify", default="on_error", choices=("always", "on_error", "never"))
    a.add_argument("--timeout", type=int, default=900)
    a.add_argument("--action")
    a.add_argument("--prompt")
    a.add_argument("--model", default="flash")
    a.add_argument("--replace", action="store_true")
    a.set_defaults(fn=_add)
    ls = s.add_parser("list")
    ls.add_argument("--all", action="store_true")
    ls.set_defaults(fn=_list)
    for name, fn in (("show", _show), ("rm", _rm), ("run", _run_now),
                     ("pause", lambda x: _set_enabled(x, 0)), ("resume", lambda x: _set_enabled(x, 1))):
        c = s.add_parser(name)
        c.add_argument("id")
        c.set_defaults(fn=fn)

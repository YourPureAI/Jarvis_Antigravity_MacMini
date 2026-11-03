"""Antigravity lifecycle hooks (see .agents/hooks.json). Must never crash: on any error emit {}."""
from __future__ import annotations

import json
import re
import sys

# Hard blocks: catastrophic / security-control-defeating / secret-exfiltrating commands.
DENY = [
    (r"\brm\s+-[a-z]*r[a-z]*f?\s+(/|~|\$HOME|[A-Za-z]:[\\/]?)(\s|$|\*)", "recursive delete of a root/home directory"),
    (r"Remove-Item\b.*-Recurse.*\s([A-Za-z]:[\\/]?|~|\$env:USERPROFILE|\$HOME)(\s|$|\*|\")", "recursive delete of a root/home directory"),
    (r"\b(rd|rmdir)\s+/s\b.*\s[A-Za-z]:\\?(\s|$)", "recursive delete of a drive root"),
    (r"\bformat(\.com)?\s+[A-Za-z]:", "disk format"),
    (r"\b(diskpart|bcdedit|cipher\s+/w)\b", "low-level disk/boot tooling"),
    (r"vssadmin\s+delete", "deleting shadow copies"),
    (r"git\s+push\b.*(\s--force\b|\s-f\b|\s--force-with-lease\b|\s\+\S+)", "force push (rewrites remote history)"),
    (r"secrets\.dpapi\.json", "direct access to the secret store (use agent.py run injection)"),
    (r"Set-MpPreference\b.*-Disable|DisableRealtimeMonitoring|netsh\s+advfirewall\s+set\s+\S+\s+state\s+off",
     "disabling security controls"),
    (r"(iwr|irm|Invoke-WebRequest|Invoke-RestMethod|curl|wget)\b[^|]*\|\s*(iex|Invoke-Expression|sh|bash|python)\b",
     "piping remote content straight into an interpreter"),
    (r"reg(\.exe)?\s+delete\s+HKLM", "deleting machine registry keys"),
]
# Always require an explicit user click, regardless of cached approvals.
ASK = [
    (r"git\s+(reset\s+--hard|clean\s+-[a-z]*f|filter-branch|filter-repo)", "destructive git operation"),
    (r"git\s+push\b", "publishing to a remote"),
    (r"\b(shutdown|Stop-Computer|Restart-Computer)\b", "power action"),
    (r"\b(schtasks\s+/(create|delete|change)|Register-ScheduledTask|Unregister-ScheduledTask)\b", "OS scheduled task change"),
    (r"\b(winget|choco|scoop)\s+(install|uninstall|upgrade)\b", "system-wide software change"),
    (r"(^|[;&|]\s*)(pip|pip3|python\s+-m\s+pip)\s+install\b(?!.*--target)", "global pip install (use per-skill venv: agent.py env add)"),
    (r"\b(Remove-Item|del|erase|rm)\b.*(-Recurse|/s|-r)", "recursive delete"),
    (r"\bSet-ExecutionPolicy\b|\breg(\.exe)?\s+(add|delete)\b", "system configuration change"),
]


def _emit(obj) -> None:
    sys.stdout.write(json.dumps(obj))


def pre_tool(payload: dict) -> dict:
    call = payload.get("toolCall") or {}
    args = call.get("args") or {}
    cmd = args.get("CommandLine") or args.get("commandLine") or args.get("command") or ""
    if not cmd:
        return {"decision": "allow"}
    for pat, why in DENY:
        if re.search(pat, cmd, re.IGNORECASE):
            return {"decision": "deny", "reason": f"Blocked by agent safety policy: {why}."}
    for pat, why in ASK:
        if re.search(pat, cmd, re.IGNORECASE):
            return {"decision": "force_ask", "reason": f"Agent safety policy: {why} needs explicit approval."}
    return {"decision": "allow"}


def session_start(_payload: dict) -> dict:
    notes = []
    try:
        from inbox import unread_count

        n = unread_count()
        if n:
            notes.append(f"{n} unread inbox item(s) (results of scheduled jobs/reminders): "
                         "`python runtime/agent.py inbox list --unread` when relevant.")
    except Exception:
        pass
    try:
        from scheduler import db as jobs_db, heartbeat_status

        con = jobs_db()
        active = con.execute("SELECT count(*) FROM jobs WHERE enabled=1").fetchone()[0]
        failing = [r[0] for r in con.execute("SELECT id FROM jobs WHERE enabled=1 AND fail_count>0")]
        hb = heartbeat_status()
        if active and hb != "alive":
            notes.append(f"Scheduler heartbeat is {hb} while {active} job(s) are active - tell the user.")
        if failing:
            notes.append(f"Failing jobs: {', '.join(failing)} (`agent.py job show <id>`).")
    except Exception:
        pass
    if not notes:
        return {}
    return {"injectSteps": [{"systemMessage": {"systemMessage": "[agent status] " + " ".join(notes)}}]}


def main(event: str) -> None:
    try:
        raw = sys.stdin.read() if not sys.stdin.isatty() else ""
        payload = json.loads(raw) if raw.strip() else {}
        handler = {"pre-tool": pre_tool, "session-start": session_start}.get(event)
        res = handler(payload) if handler else {}
        _emit(res)
    except Exception:
        _emit({"decision": "allow"} if event == "pre-tool" else {})


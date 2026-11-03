"""Inbox (persistent results/notifications) + Windows toast notifications."""
from __future__ import annotations

import subprocess
import sys

from core import connect, now_local, iso, out

SCHEMA = """
CREATE TABLE IF NOT EXISTS inbox(
  id INTEGER PRIMARY KEY,
  ts TEXT NOT NULL,
  source TEXT NOT NULL,
  title TEXT NOT NULL,
  body TEXT NOT NULL DEFAULT '',
  level TEXT NOT NULL DEFAULT 'info',
  read INTEGER NOT NULL DEFAULT 0
);
"""

_PS_TOAST = r"""
$ErrorActionPreference='Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$t = [Security.SecurityElement]::Escape($env:AGENT_TOAST_TITLE)
$m = [Security.SecurityElement]::Escape($env:AGENT_TOAST_MSG)
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml("<toast><visual><binding template='ToastGeneric'><text>$t</text><text>$m</text></binding></visual><audio src='ms-winsoundevent:Notification.Reminder'/></toast>")
$app = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app).Show([Windows.UI.Notifications.ToastNotification]::new($xml))
"""


def db():
    con = connect()
    con.executescript(SCHEMA)
    return con


def inbox_add(source: str, title: str, body: str = "", level: str = "info") -> int:
    con = db()
    cur = con.execute(
        "INSERT INTO inbox(ts,source,title,body,level) VALUES(?,?,?,?,?)",
        (iso(now_local()), source, title, body, level),
    )
    con.commit()
    return cur.lastrowid


def toast(title: str, message: str) -> bool:
    """Best-effort desktop toast (Windows PowerShell toast or macOS Notification Center)."""
    import os

    if os.environ.get("AGENT_NO_TOAST"):
        return False

    if sys.platform == "win32":
        env = dict(os.environ, AGENT_TOAST_TITLE=title[:200], AGENT_TOAST_MSG=message[:600])
        try:
            r = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", _PS_TOAST],
                env=env, capture_output=True, timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return r.returncode == 0
        except Exception:
            return False
    elif sys.platform == "darwin":
        clean_title = title[:200].replace('"', '\\"')
        clean_msg = message[:600].replace('"', '\\"')
        apple_script = f'display notification "{clean_msg}" with title "{clean_title}"'
        try:
            r = subprocess.run(["osascript", "-e", apple_script], capture_output=True, timeout=10)
            return r.returncode == 0
        except Exception:
            return False

    return False


def notify(title: str, message: str, source: str = "agent", level: str = "info") -> dict:
    iid = inbox_add(source, title, message, level)
    return {"inbox_id": iid, "toast": toast(title, message)}


# ---- CLI -------------------------------------------------------------------

def _cli_notify(a):
    out({"ok": True, **notify(a.title, a.message, a.source, a.level)})


def _cli_add(a):
    out({"ok": True, "id": inbox_add(a.source, a.title, a.body, a.level)})


def _cli_list(a):
    con = db()
    sql = "SELECT * FROM inbox" + (" WHERE read=0" if a.unread else "") + " ORDER BY id DESC LIMIT ?"
    rows = [dict(r) for r in con.execute(sql, (a.limit,))]
    if a.mark_read and rows:
        con.execute(f"UPDATE inbox SET read=1 WHERE id IN ({','.join('?' * len(rows))})", [r["id"] for r in rows])
        con.commit()
    out({"ok": True, "items": rows})


def _cli_read(a):
    con = db()
    if a.all:
        con.execute("UPDATE inbox SET read=1 WHERE read=0")
    else:
        con.execute("UPDATE inbox SET read=1 WHERE id=?", (a.id,))
    con.commit()
    out({"ok": True})


def unread_count() -> int:
    return db().execute("SELECT count(*) FROM inbox WHERE read=0").fetchone()[0]


def register(sub) -> None:
    n = sub.add_parser("notify", help="desktop toast + inbox entry")
    n.add_argument("title")
    n.add_argument("message")
    n.add_argument("--source", default="agent")
    n.add_argument("--level", default="info", choices=("info", "warn", "error"))
    n.set_defaults(fn=_cli_notify)

    p = sub.add_parser("inbox", help="persistent results / messages for the user")
    s = p.add_subparsers(dest="action", required=True)
    a = s.add_parser("add")
    a.add_argument("--source", default="agent")
    a.add_argument("--title", required=True)
    a.add_argument("--body", default="")
    a.add_argument("--level", default="info", choices=("info", "warn", "error"))
    a.set_defaults(fn=_cli_add)
    ls = s.add_parser("list")
    ls.add_argument("--unread", action="store_true")
    ls.add_argument("--limit", type=int, default=20)
    ls.add_argument("--mark-read", action="store_true")
    ls.set_defaults(fn=_cli_list)
    r = s.add_parser("read")
    r.add_argument("id", type=int, nargs="?")
    r.add_argument("--all", action="store_true")
    r.set_defaults(fn=_cli_read)

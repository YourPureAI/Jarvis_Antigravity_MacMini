"""Git-based backup to GitHub. SQLite DBs are exported as text SQL dumps (diffable, restorable);
raw *.db files, venvs, logs and secrets are git-ignored. A secret scan blocks risky commits."""
from __future__ import annotations

import re
import sqlite3
import subprocess

from core import BACKUPS_DIR, DATA_DIR, ROOT, fail, out
from skills import _SECRET_RE

DUMP_DIR = BACKUPS_DIR / "db"


def git(*args, check=False, timeout=120) -> subprocess.CompletedProcess:
    r = subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True, text=True, timeout=timeout,
                       encoding="utf-8", errors="replace")
    if check and r.returncode:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip()[-800:]}")
    return r


def dump_sql(con: sqlite3.Connection) -> str:
    """Portable text dump that (unlike iterdump) restores FTS5/virtual tables correctly:
    shadow tables are skipped, external-content FTS indexes are rebuilt, triggers are created last."""
    objs = con.execute("SELECT type, name, sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' "
                       "ORDER BY rowid").fetchall()
    vtabs = [n for t, n, s in objs if t == "table" and s.upper().startswith("CREATE VIRTUAL TABLE")]

    def is_shadow(name: str) -> bool:
        return any(name.startswith(v + "_") for v in vtabs)

    def rows(table: str) -> list[str]:
        cols = [r[1] for r in con.execute(f'PRAGMA table_info("{table}")')]
        if not cols:
            return []
        q = "SELECT " + " || ',' || ".join(f'quote("{c}")' for c in cols) + f' FROM "{table}"'
        return [f'INSERT INTO "{table}" VALUES({r[0]});' for r in con.execute(q)]

    out_lines = ["PRAGMA foreign_keys=OFF;", "BEGIN TRANSACTION;"]
    regular = [(n, s) for t, n, s in objs if t == "table" and n not in vtabs and not is_shadow(n)]
    for n, s in regular:
        out_lines.append(s + ";")
    for n, _ in regular:
        out_lines += rows(n)
    if con.execute("SELECT 1 FROM sqlite_master WHERE name='sqlite_sequence'").fetchone():
        out_lines += [f"INSERT INTO sqlite_sequence VALUES({r[0]}, {r[1]});" for r in
                      con.execute("SELECT quote(name), seq FROM sqlite_sequence")]
    rebuild = []
    for t, n, s in objs:
        if n in vtabs:
            out_lines.append(s + ";")
            m = re.search(r"content\s*=\s*'([^']*)'", s, re.IGNORECASE)
            if m and m.group(1):
                rebuild.append(n)  # external content: index is derived data
            elif not m:
                out_lines += rows(n)  # self-contained virtual table
    out_lines += [f"INSERT INTO \"{n}\"(\"{n}\") VALUES('rebuild');" for n in rebuild]
    for kind in ("index", "view", "trigger"):
        out_lines += [s + ";" for t, n, s in objs if t == kind and not is_shadow(n)]
    out_lines.append("COMMIT;")
    return "\n".join(out_lines) + "\n"


def dump_databases() -> list[str]:
    DUMP_DIR.mkdir(parents=True, exist_ok=True)
    written = []
    for db in sorted(DATA_DIR.rglob("*.db")):
        rel = db.relative_to(DATA_DIR).as_posix()
        target = DUMP_DIR / (rel + ".sql")
        target.parent.mkdir(parents=True, exist_ok=True)
        src = sqlite3.connect(db, timeout=30)
        mem = sqlite3.connect(":memory:")
        src.backup(mem)  # consistent snapshot even while WAL writers are active
        src.close()
        sql = dump_sql(mem)
        mem.close()
        if not target.exists() or target.read_text(encoding="utf-8") != sql:
            target.write_text(sql, encoding="utf-8", newline="\n")
        written.append(rel)
    return written


def _scan_staged() -> list[str]:
    diff = git("diff", "--cached", "-U0", "--no-color").stdout
    hits, current = [], "?"
    for line in diff.splitlines():
        if line.startswith("+++ "):
            current = line[6:]
        elif line.startswith("+") and _SECRET_RE.search(line):
            hits.append(current)
    return sorted(set(hits))


def backup_now(message: str = "backup") -> dict:
    if not (ROOT / ".git").exists():
        return {"ok": False, "error": "git repo not initialised (run setup)"}
    dbs = dump_databases()
    git("add", "-A", check=True)
    hits = _scan_staged()
    if hits:
        git("reset", "-q")
        return {"ok": False, "error": "possible secrets detected - commit aborted", "files": hits}
    if not git("diff", "--cached", "--name-only").stdout.strip():
        committed = False
    else:
        git("commit", "-q", "-m", message, check=True)
        committed = True
    pushed, push_err = None, None
    if git("remote").stdout.split().count("origin"):
        branch = git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        r = git("push", "-q", "-u", "origin", branch, timeout=180)
        pushed, push_err = r.returncode == 0, (r.stderr.strip()[-500:] or None)
    return {"ok": pushed is not False, "committed": committed, "dumped_dbs": dbs, "pushed": pushed,
            "push_error": push_err if pushed is False else None,
            "remote": "origin" if pushed is not None else "none (configure: agent.py backup remote <url>)"}


def _now(a):
    res = backup_now(a.message)
    out(res)
    if not res.get("ok"):
        raise SystemExit(1)


def _status(_a):
    remote = git("remote", "get-url", "origin").stdout.strip() or None
    last = git("log", "-1", "--format=%h %ci %s").stdout.strip() or None
    dirty = bool(git("status", "--porcelain").stdout.strip())
    ahead = None
    if remote:
        r = git("rev-list", "--count", "@{u}..HEAD")
        ahead = int(r.stdout.strip()) if r.returncode == 0 else "no upstream"
    out({"ok": True, "remote": remote, "last_commit": last, "uncommitted_changes": dirty, "unpushed": ahead})


def _remote(a):
    if not re.match(r"^(https://github\.com/|git@github\.com:)[\w.-]+/[\w.-]+?(\.git)?$", a.url):
        fail("expected a GitHub URL like https://github.com/<user>/<repo>.git")
    has = "origin" in git("remote").stdout.split()
    git("remote", "set-url" if has else "add", "origin", a.url, check=True)
    out({"ok": True, "origin": a.url, "note": "repository MUST be private"})


def _restore(a):
    restored, skipped = [], []
    for sql in sorted(DUMP_DIR.rglob("*.sql")):
        rel = sql.relative_to(DUMP_DIR).as_posix()[:-4]
        target = DATA_DIR / rel
        if target.exists() and not a.force:
            skipped.append(rel)
            continue
        if target.exists():
            target.replace(target.with_suffix(target.suffix + ".pre-restore"))
            for ext in ("-wal", "-shm"):
                p = target.with_name(target.name + ext)
                p.unlink(missing_ok=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(target)
        con.executescript(sql.read_text(encoding="utf-8"))
        con.close()
        restored.append(rel)
    out({"ok": True, "restored": restored, "skipped_existing": skipped})


def register(sub) -> None:
    p = sub.add_parser("backup", help="git/GitHub backup")
    s = p.add_subparsers(dest="action", required=True)
    n = s.add_parser("now")
    n.add_argument("-m", "--message", default="backup")
    n.set_defaults(fn=_now)
    s.add_parser("status").set_defaults(fn=_status)
    r = s.add_parser("remote")
    r.add_argument("url")
    r.set_defaults(fn=_remote)
    rs = s.add_parser("restore", help="rebuild data/*.db from backups/db/*.sql")
    rs.add_argument("--force", action="store_true", help="overwrite existing DBs (old kept as .pre-restore)")
    rs.set_defaults(fn=_restore)

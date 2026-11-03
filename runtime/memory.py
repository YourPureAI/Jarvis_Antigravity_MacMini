"""Long-term factual memory: SQLite + FTS5. Pinned facts are rendered into an always-on rule."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from core import RULES_DIR, connect, fail, out, utc_now_iso

KINDS = ("fact", "preference", "person", "place", "device", "account", "event", "project", "note")
PROFILE_RULE = Path(os.environ.get("AGENT_PROFILE_RULE") or RULES_DIR / "90-user-profile.md")
PROFILE_MAX_CHARS = 2500

SCHEMA = """
CREATE TABLE IF NOT EXISTS facts(
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL DEFAULT 'fact',
  subject TEXT NOT NULL,
  content TEXT NOT NULL,
  tags TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL DEFAULT 'conversation',
  pinned INTEGER NOT NULL DEFAULT 0,
  created TEXT NOT NULL,
  updated TEXT NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(
  subject, content, tags, content='facts', content_rowid='id', tokenize='unicode61 remove_diacritics 2'
);
CREATE TRIGGER IF NOT EXISTS facts_ai AFTER INSERT ON facts BEGIN
  INSERT INTO facts_fts(rowid, subject, content, tags) VALUES (new.id, new.subject, new.content, new.tags);
END;
CREATE TRIGGER IF NOT EXISTS facts_ad AFTER DELETE ON facts BEGIN
  INSERT INTO facts_fts(facts_fts, rowid, subject, content, tags) VALUES('delete', old.id, old.subject, old.content, old.tags);
END;
CREATE TRIGGER IF NOT EXISTS facts_au AFTER UPDATE ON facts BEGIN
  INSERT INTO facts_fts(facts_fts, rowid, subject, content, tags) VALUES('delete', old.id, old.subject, old.content, old.tags);
  INSERT INTO facts_fts(rowid, subject, content, tags) VALUES (new.id, new.subject, new.content, new.tags);
END;
"""


def db():
    con = connect()
    con.executescript(SCHEMA)
    return con


def _row(r) -> dict:
    d = dict(r)
    d.pop("created", None)
    return d


def _fts_query(q: str) -> str:
    words = [w for w in re.findall(r"\w+", q, flags=re.UNICODE) if len(w) > 1]
    return " OR ".join(f'"{w}"*' for w in words)


def add(args) -> None:
    if args.kind not in KINDS:
        fail(f"kind must be one of {KINDS}")
    con = db()
    ts = utc_now_iso()
    # De-duplicate: identical subject+kind+content -> just touch it.
    dup = con.execute(
        "SELECT id FROM facts WHERE kind=? AND lower(subject)=lower(?) AND lower(content)=lower(?)",
        (args.kind, args.subject, args.content),
    ).fetchone()
    if dup:
        con.execute("UPDATE facts SET updated=? WHERE id=?", (ts, dup["id"]))
        con.commit()
        out({"ok": True, "id": dup["id"], "dedup": True})
        return
    cur = con.execute(
        "INSERT INTO facts(kind,subject,content,tags,source,pinned,created,updated) VALUES(?,?,?,?,?,?,?,?)",
        (args.kind, args.subject, args.content, args.tags or "", args.source, int(args.pin), ts, ts),
    )
    con.commit()
    if args.pin:
        render_profile()
    # Surface possibly conflicting facts about the same subject so the agent can resolve them.
    similar = [
        _row(r)
        for r in con.execute(
            "SELECT * FROM facts WHERE kind=? AND lower(subject)=lower(?) AND id<>? ORDER BY updated DESC LIMIT 5",
            (args.kind, args.subject, cur.lastrowid),
        )
    ]
    out({"ok": True, "id": cur.lastrowid, "same_subject": similar})


def update(args) -> None:
    con = db()
    r = con.execute("SELECT * FROM facts WHERE id=?", (args.id,)).fetchone()
    if not r:
        fail(f"fact {args.id} not found")
    fields = {k: getattr(args, k) for k in ("subject", "content", "tags", "kind") if getattr(args, k) is not None}
    if args.pin is not None:
        fields["pinned"] = int(args.pin)
    if not fields:
        fail("nothing to update")
    fields["updated"] = utc_now_iso()
    con.execute(
        f"UPDATE facts SET {', '.join(k + '=?' for k in fields)} WHERE id=?", (*fields.values(), args.id)
    )
    con.commit()
    if r["pinned"] or fields.get("pinned"):
        render_profile()
    out({"ok": True, "id": args.id})


def search(args) -> None:
    con = db()
    q = _fts_query(args.query)
    rows = []
    if q:
        sql = (
            "SELECT f.* FROM facts_fts JOIN facts f ON f.id=facts_fts.rowid WHERE facts_fts MATCH ? "
            + ("AND f.kind=? " if args.kind else "")
            + "ORDER BY bm25(facts_fts, 3.0, 1.0, 2.0) LIMIT ?"
        )
        params = (q, args.kind, args.limit) if args.kind else (q, args.limit)
        rows = con.execute(sql, params).fetchall()
    out({"ok": True, "results": [_row(r) for r in rows]})


def list_(args) -> None:
    con = db()
    where, params = [], []
    if args.kind:
        where.append("kind=?")
        params.append(args.kind)
    if args.subject:
        where.append("lower(subject) LIKE lower(?)")
        params.append(f"%{args.subject}%")
    if args.pinned:
        where.append("pinned=1")
    sql = "SELECT * FROM facts" + (" WHERE " + " AND ".join(where) if where else "")
    sql += " ORDER BY updated DESC LIMIT ?"
    rows = con.execute(sql, (*params, args.limit)).fetchall()
    out({"ok": True, "results": [_row(r) for r in rows]})


def forget(args) -> None:
    con = db()
    r = con.execute("SELECT pinned FROM facts WHERE id=?", (args.id,)).fetchone()
    if not r:
        fail(f"fact {args.id} not found")
    con.execute("DELETE FROM facts WHERE id=?", (args.id,))
    con.commit()
    if r["pinned"]:
        render_profile()
    out({"ok": True, "deleted": args.id})


def stats(_args) -> None:
    con = db()
    rows = con.execute("SELECT kind, count(*) n, sum(pinned) p FROM facts GROUP BY kind").fetchall()
    out({"ok": True, "by_kind": {r["kind"]: {"n": r["n"], "pinned": r["p"]} for r in rows}})


def render_profile() -> dict:
    """Write pinned facts to the always-on profile rule, bounded in size."""
    con = db()
    rows = con.execute("SELECT id, kind, subject, content FROM facts WHERE pinned=1 ORDER BY kind, subject").fetchall()
    header = (
        "---\ntrigger: always_on\ndescription: Auto-generated core user profile (pinned memory facts).\n---\n"
        "# User Profile (auto-generated - do not edit; use the memory skill)\n\n"
    )
    lines, size, dropped = [], len(header), 0
    for r in rows:
        line = f"- [{r['kind']}#{r['id']}] {r['subject']}: {r['content']}\n"
        if size + len(line) > PROFILE_MAX_CHARS:
            dropped += 1
            continue
        lines.append(line)
        size += len(line)
    if not lines:
        lines = ["- (no pinned facts yet)\n"]
    PROFILE_RULE.write_text(header + "".join(lines), encoding="utf-8", newline="\n")
    return {"pinned": len(rows), "written": len(lines), "dropped_over_budget": dropped, "chars": size}


def profile(_args) -> None:
    out({"ok": True, **render_profile()})


def export(args) -> None:
    con = db()
    n = 0
    with open(args.file, "w", encoding="utf-8", newline="\n") as f:
        for r in con.execute("SELECT * FROM facts ORDER BY id"):
            f.write(json.dumps(dict(r), ensure_ascii=False) + "\n")
            n += 1
    out({"ok": True, "exported": n, "file": args.file})


def import_(args) -> None:
    con = db()
    n = 0
    with open(args.file, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            d = json.loads(line)
            con.execute(
                "INSERT OR REPLACE INTO facts(id,kind,subject,content,tags,source,pinned,created,updated) "
                "VALUES(:id,:kind,:subject,:content,:tags,:source,:pinned,:created,:updated)",
                d,
            )
            n += 1
    con.commit()
    render_profile()
    out({"ok": True, "imported": n})


def register(sub) -> None:
    p = sub.add_parser("mem", help="long-term factual memory")
    s = p.add_subparsers(dest="action", required=True)

    a = s.add_parser("add")
    a.add_argument("subject")
    a.add_argument("content")
    a.add_argument("--kind", default="fact")
    a.add_argument("--tags", default="")
    a.add_argument("--source", default="conversation")
    a.add_argument("--pin", action="store_true", help="also show in always-on profile")
    a.set_defaults(fn=add)

    u = s.add_parser("update")
    u.add_argument("id", type=int)
    u.add_argument("--subject")
    u.add_argument("--content")
    u.add_argument("--tags")
    u.add_argument("--kind")
    u.add_argument("--pin", type=int, choices=(0, 1))
    u.set_defaults(fn=update)

    q = s.add_parser("search")
    q.add_argument("query")
    q.add_argument("--kind")
    q.add_argument("--limit", type=int, default=8)
    q.set_defaults(fn=search)

    ls = s.add_parser("list")
    ls.add_argument("--kind")
    ls.add_argument("--subject")
    ls.add_argument("--pinned", action="store_true")
    ls.add_argument("--limit", type=int, default=30)
    ls.set_defaults(fn=list_)

    f = s.add_parser("forget")
    f.add_argument("id", type=int)
    f.set_defaults(fn=forget)

    s.add_parser("stats").set_defaults(fn=stats)
    s.add_parser("profile", help="re-render the always-on profile rule").set_defaults(fn=profile)

    e = s.add_parser("export")
    e.add_argument("file")
    e.set_defaults(fn=export)
    i = s.add_parser("import")
    i.add_argument("file")
    i.set_defaults(fn=import_)

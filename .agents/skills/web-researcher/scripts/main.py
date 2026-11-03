"""Web research, article extraction, and research note storage."""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(os.environ.get("AGENT_SKILL_DATA", Path(__file__).resolve().parents[3] / "data" / "skills" / "web-researcher"))
DB_PATH = DATA_DIR / "research.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    url TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '',
    created TEXT NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
    title, summary, tags, content='notes', content_rowid='id'
);
CREATE TRIGGER IF NOT EXISTS notes_ai AFTER INSERT ON notes BEGIN
    INSERT INTO notes_fts(rowid, title, summary, tags) VALUES (new.id, new.title, new.summary, new.tags);
END;
CREATE TRIGGER IF NOT EXISTS notes_ad AFTER DELETE ON notes BEGIN
    INSERT INTO notes_fts(notes_fts, rowid, title, summary, tags) VALUES('delete', old.id, old.title, old.summary, old.tags);
END;
"""


def get_db() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def fetch_url(url: str, max_chars: int = 15000) -> dict:
    import httpx
    from bs4 import BeautifulSoup

    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AntigravityPersonalAgent/1.0"}
    try:
        resp = httpx.get(url, headers=headers, follow_redirects=True, timeout=15.0)
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header", "noscript"]):
            tag.decompose()
        title = soup.title.string.strip() if soup.title and soup.title.string else url
        body_text = re.sub(r"\n\s*\n+", "\n\n", soup.get_text(separator="\n").strip())
        truncated = len(body_text) > max_chars
        return {
            "ok": True,
            "url": str(resp.url),
            "status_code": resp.status_code,
            "title": title,
            "content": body_text[:max_chars],
            "truncated": truncated,
        }
    except Exception as e:
        return {"ok": False, "url": url, "error": str(e)}


def save_note(title: str, summary: str, url: str = "", tags: str = "") -> dict:
    con = get_db()
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    cur = con.execute(
        "INSERT INTO notes(title, summary, url, tags, created) VALUES (?, ?, ?, ?, ?)",
        (title, summary, url, tags, ts),
    )
    con.commit()
    return {"ok": True, "id": cur.lastrowid, "title": title}


def search_notes(query: str, limit: int = 10) -> dict:
    con = get_db()
    words = [w for w in re.findall(r"\w+", query) if len(w) > 1]
    fts_q = " OR ".join(f'"{w}"*' for w in words) if words else "*"
    try:
        rows = con.execute(
            "SELECT n.* FROM notes_fts JOIN notes n ON n.id = notes_fts.rowid WHERE notes_fts MATCH ? ORDER BY rank LIMIT ?",
            (fts_q, limit),
        ).fetchall()
        return {"ok": True, "results": [dict(r) for r in rows]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def list_notes(limit: int = 15) -> dict:
    con = get_db()
    rows = con.execute("SELECT * FROM notes ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return {"ok": True, "notes": [dict(r) for r in rows]}


def main() -> None:
    p = argparse.ArgumentParser(description="Web research & note management")
    sub = p.add_subparsers(dest="action", required=True)

    f = sub.add_parser("fetch")
    f.add_argument("url")
    f.add_argument("--max-chars", type=int, default=15000)

    s = sub.add_parser("save")
    s.add_argument("title")
    s.add_argument("summary")
    s.add_argument("--url", default="")
    s.add_argument("--tags", default="")

    q = sub.add_parser("search")
    q.add_argument("query")
    q.add_argument("--limit", type=int, default=10)

    ls = sub.add_parser("list")
    ls.add_argument("--limit", type=int, default=15)

    a = p.parse_args()

    if a.action == "fetch":
        print(json.dumps(fetch_url(a.url, a.max_chars), indent=2))
    elif a.action == "save":
        print(json.dumps(save_note(a.title, a.summary, a.url, a.tags), indent=2))
    elif a.action == "search":
        print(json.dumps(search_notes(a.query, a.limit), indent=2))
    elif a.action == "list":
        print(json.dumps(list_notes(a.limit), indent=2))


if __name__ == "__main__":
    main()

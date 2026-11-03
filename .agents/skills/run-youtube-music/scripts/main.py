"""YouTube & YouTube Music player, search resolver, and playlist manager.

Bypasses confirmation prompts and bot-checks by:
1. Resolving search queries directly into exact top-match video IDs instead of opening search pages.
2. Launching native default browser sessions with `autoplay=1` (or embedding directly in the dashboard).
3. Utilizing user's existing authenticated browser profile (high MEI, no cookie/bot popups).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

PRESETS = [
    {
        "name": "☕ Lofi Study & Relax",
        "description": "Chill lofi hip-hop radio beats for focus and study",
        "query": "lofi hip hop radio beats to relax study to",
    },
    {
        "name": "⚡ Synthwave & Cyberpunk",
        "description": "Retro synthwave, outrun, and cyberpunk electronic beats",
        "query": "synthwave radio chill beats to game to",
    },
    {
        "name": "🎻 Classical Deep Focus",
        "description": "Instrumental piano and orchestral pieces for deep cognitive work",
        "query": "classical music for studying deep focus",
    },
    {
        "name": "🎧 Ambient Coding Flow",
        "description": "Minimal ambient electronic soundscapes for programming",
        "query": "ambient electronic music for programming",
    },
    {
        "name": "🔥 High Energy Workout",
        "description": "Motivational gym and workout music mix",
        "query": "workout motivation music mix",
    },
]


def get_db_path() -> Path:
    data_dir = os.environ.get("AGENT_SKILL_DATA")
    if data_dir:
        base = Path(data_dir)
    else:
        base = Path(__file__).resolve().parents[3] / "data" / "skills" / "run-youtube-music"
    base.mkdir(parents=True, exist_ok=True)
    return base / "playlists.db"


def init_db(db_path: Optional[Path] = None) -> sqlite3.Connection:
    path = db_path or get_db_path()
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS playlists (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            description TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS playlist_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            playlist_id INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
            video_id TEXT NOT NULL,
            title TEXT NOT NULL,
            url TEXT NOT NULL,
            position INTEGER DEFAULT 0,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS playback_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            state TEXT NOT NULL DEFAULT 'stopped',
            query TEXT,
            title TEXT,
            video_id TEXT,
            target TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    conn.commit()
    return conn


def set_playback_state(
    state: str,
    item: Optional[Dict[str, Any]] = None,
    target: str = "youtube",
    query: str = "",
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Record active YouTube/Music playback status."""
    conn = init_db(db_path)
    with conn:
        conn.execute(
            """
            INSERT INTO playback_state (id, state, query, title, video_id, target, updated_at)
            VALUES (1, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(id) DO UPDATE SET
                state = excluded.state,
                query = COALESCE(excluded.query, playback_state.query),
                title = COALESCE(excluded.title, playback_state.title),
                video_id = COALESCE(excluded.video_id, playback_state.video_id),
                target = COALESCE(excluded.target, playback_state.target),
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                state,
                query or (item.get("query") if item else None),
                item.get("title") if item else None,
                item.get("video_id") if item else None,
                target,
            ),
        )
        row = conn.execute(
            "SELECT id, state, query, title, video_id, target, updated_at FROM playback_state WHERE id = 1"
        ).fetchone()
        if row:
            return {
                "id": row[0],
                "state": row[1],
                "query": row[2],
                "title": row[3],
                "video_id": row[4],
                "target": row[5],
                "updated_at": row[6],
            }
        return {"state": state}


def get_playback_state(db_path: Optional[Path] = None) -> Dict[str, Any]:
    """Retrieve active YouTube playback state."""
    conn = init_db(db_path)
    with conn:
        row = conn.execute(
            "SELECT id, state, query, title, video_id, target, updated_at FROM playback_state WHERE id = 1"
        ).fetchone()
        if row:
            return {
                "id": row[0],
                "state": row[1],
                "query": row[2],
                "title": row[3],
                "video_id": row[4],
                "target": row[5],
                "updated_at": row[6],
            }
        return {"id": 1, "state": "stopped", "query": None, "title": None, "video_id": None, "target": "youtube"}


def extract_video_id(input_str: str) -> Optional[str]:
    """Extract YouTube 11-char video ID from URL or raw ID."""
    clean = input_str.strip()
    if re.fullmatch(r"[a-zA-Z0-9_-]{11}", clean):
        return clean

    patterns = [
        r"(?:v=|\/v\/|embed\/|watch\?v=|\/shorts\/)([a-zA-Z0-9_-]{11})",
        r"youtu\.be\/([a-zA-Z0-9_-]{11})",
        r"music\.youtube\.com\/watch\?v=([a-zA-Z0-9_-]{11})",
    ]
    for pattern in patterns:
        m = re.search(pattern, clean)
        if m:
            return m.group(1)
    return None


def search_youtube(query: str, limit: int = 5) -> List[Dict[str, str]]:
    """Search YouTube and return video IDs and titles without opening search pages."""
    encoded_query = urllib.parse.quote_plus(query)
    search_url = f"https://www.youtube.com/results?search_query={encoded_query}"
    req = urllib.request.Request(
        search_url,
        headers={"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"},
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
    except Exception as e:
        return []

    results: List[Dict[str, str]] = []
    seen = set()

    # Strategy 1: Parse ytInitialData JSON
    m = re.search(r"var ytInitialData = ({.*?});</script>", html)
    if m:
        try:
            data = json.loads(m.group(1))
            contents = (
                data.get("contents", {})
                .get("twoColumnSearchResultsRenderer", {})
                .get("primaryContents", {})
                .get("sectionListRenderer", {})
                .get("contents", [])
            )
            for sec in contents:
                for item in sec.get("itemSectionRenderer", {}).get("contents", []):
                    if "videoRenderer" in item:
                        vr = item["videoRenderer"]
                        vid = vr.get("videoId")
                        if vid and vid not in seen:
                            seen.add(vid)
                            title_runs = vr.get("title", {}).get("runs", [])
                            title = "".join(r.get("text", "") for r in title_runs) or query
                            results.append({
                                "id": vid,
                                "title": title,
                                "url": f"https://www.youtube.com/watch?v={vid}",
                                "music_url": f"https://music.youtube.com/watch?v={vid}",
                                "embed_url": f"https://www.youtube-nocookie.com/embed/{vid}?autoplay=1&enablejsapi=1",
                            })
                            if len(results) >= limit:
                                return results
        except Exception:
            pass

    # Strategy 2: Fallback regex matching
    if not results:
        raw_ids = re.findall(r"/watch\?v=([a-zA-Z0-9_-]{11})", html)
        for vid in raw_ids:
            if vid not in seen:
                seen.add(vid)
                results.append({
                    "id": vid,
                    "title": query,
                    "url": f"https://www.youtube.com/watch?v={vid}",
                    "music_url": f"https://music.youtube.com/watch?v={vid}",
                    "embed_url": f"https://www.youtube-nocookie.com/embed/{vid}?autoplay=1&enablejsapi=1",
                })
                if len(results) >= limit:
                    break

    return results


def resolve_playable_item(query_or_url: str, target: str = "youtube") -> Optional[Dict[str, str]]:
    """Resolves an input string into a direct auto-playing YouTube or YouTube Music link."""
    vid = extract_video_id(query_or_url)
    if vid:
        title = f"YouTube Track ({vid})"
        yt_url = f"https://www.youtube.com/watch?v={vid}&autoplay=1"
        ytm_url = f"https://music.youtube.com/watch?v={vid}&autoplay=1"
        embed_url = f"https://www.youtube.com/embed/{vid}?autoplay=1&enablejsapi=1&playsinline=1"
        return {
            "id": vid,
            "title": title,
            "play_url": ytm_url if target == "music" else yt_url,
            "youtube_url": yt_url,
            "music_url": ytm_url,
            "embed_url": embed_url,
            "target": target,
        }

    matches = search_youtube(query_or_url, limit=1)
    if not matches:
        return None

    top = matches[0]
    vid = top["id"]
    title = top["title"]
    yt_url = f"https://www.youtube.com/watch?v={vid}&autoplay=1"
    ytm_url = f"https://music.youtube.com/watch?v={vid}&autoplay=1"
    embed_url = f"https://www.youtube.com/embed/{vid}?autoplay=1&enablejsapi=1&playsinline=1"
    return {
        "id": vid,
        "title": title,
        "play_url": ytm_url if target == "music" else yt_url,
        "youtube_url": yt_url,
        "music_url": ytm_url,
        "embed_url": embed_url,
        "target": target,
    }


def open_browser_cleanly(url: str) -> bool:
    """Launches the user's default browser directly with existing profile."""
    try:
        return webbrowser.open(url)
    except Exception:
        try:
            if sys.platform == "win32":
                os.startfile(url)
                return True
        except Exception:
            pass
    return False


# ------------------- Playlist Store -------------------

def playlist_create(name: str, description: str = "", db_path: Optional[Path] = None) -> Dict[str, Any]:
    conn = init_db(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO playlists (name, description) VALUES (?, ?)",
            (name.strip(), description.strip()),
        )
        conn.commit()
        pid = cur.lastrowid
        return {"ok": True, "id": pid, "name": name.strip(), "description": description.strip()}
    except sqlite3.IntegrityError:
        return {"ok": False, "error": f"Playlist '{name}' already exists."}
    finally:
        conn.close()


def playlist_list(db_path: Optional[Path] = None) -> Dict[str, Any]:
    conn = init_db(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT p.id, p.name, p.description, p.created_at, COUNT(i.id) as track_count
            FROM playlists p
            LEFT JOIN playlist_items i ON p.id = i.playlist_id
            GROUP BY p.id
            ORDER BY p.name ASC
            """
        )
        rows = cur.fetchall()
        items = [
            {
                "id": r[0],
                "name": r[1],
                "description": r[2],
                "created_at": r[3],
                "track_count": r[4],
            }
            for r in rows
        ]
        return {"ok": True, "playlists": items}
    finally:
        conn.close()


def playlist_show(name: str, db_path: Optional[Path] = None) -> Dict[str, Any]:
    conn = init_db(db_path)
    try:
        cur = conn.cursor()
        cur.execute("SELECT id, name, description, created_at FROM playlists WHERE name = ?", (name.strip(),))
        p = cur.fetchone()
        if not p:
            return {"ok": False, "error": f"Playlist '{name}' not found."}
        pid, pname, pdesc, pcreated = p
        cur.execute(
            """
            SELECT id, video_id, title, url, position, added_at
            FROM playlist_items
            WHERE playlist_id = ?
            ORDER BY position ASC, id ASC
            """,
            (pid,),
        )
        tracks = [
            {
                "id": r[0],
                "video_id": r[1],
                "title": r[2],
                "url": r[3],
                "position": r[4],
                "added_at": r[5],
                "music_url": f"https://music.youtube.com/watch?v={r[1]}&autoplay=1",
                "embed_url": f"https://www.youtube-nocookie.com/embed/{r[1]}?autoplay=1&enablejsapi=1",
            }
            for r in tracks_raw
        ] if (tracks_raw := cur.fetchall()) else []
        return {
            "ok": True,
            "playlist": {
                "id": pid,
                "name": pname,
                "description": pdesc,
                "created_at": pcreated,
                "tracks": tracks,
            },
        }
    finally:
        conn.close()


def playlist_add(name: str, query_or_url: str, custom_title: str = "", db_path: Optional[Path] = None) -> Dict[str, Any]:
    conn = init_db(db_path)
    try:
        cur = conn.cursor()
        cur.execute("SELECT id FROM playlists WHERE name = ?", (name.strip(),))
        row = cur.fetchone()
        if not row:
            # Auto-create if it doesn't exist
            res = playlist_create(name, f"Playlist {name}", db_path=db_path)
            if not res.get("ok"):
                return res
            pid = res["id"]
        else:
            pid = row[0]

        resolved = resolve_playable_item(query_or_url)
        if not resolved:
            return {"ok": False, "error": f"Could not find video for '{query_or_url}'"}

        title = custom_title.strip() or resolved["title"]
        vid = resolved["id"]
        url = resolved["youtube_url"]

        cur.execute("SELECT COALESCE(MAX(position), 0) + 1 FROM playlist_items WHERE playlist_id = ?", (pid,))
        pos = cur.fetchone()[0]

        cur.execute(
            """
            INSERT INTO playlist_items (playlist_id, video_id, title, url, position)
            VALUES (?, ?, ?, ?, ?)
            """,
            (pid, vid, title, url, pos),
        )
        conn.commit()
        item_id = cur.lastrowid
        return {
            "ok": True,
            "item": {
                "id": item_id,
                "playlist_id": pid,
                "playlist_name": name,
                "video_id": vid,
                "title": title,
                "url": url,
                "music_url": resolved["music_url"],
                "embed_url": resolved["embed_url"],
            },
        }
    finally:
        conn.close()


def playlist_remove(name: str, item_id: int, db_path: Optional[Path] = None) -> Dict[str, Any]:
    conn = init_db(db_path)
    try:
        cur = conn.cursor()
        cur.execute("SELECT id FROM playlists WHERE name = ?", (name.strip(),))
        p = cur.fetchone()
        if not p:
            return {"ok": False, "error": f"Playlist '{name}' not found."}
        cur.execute("DELETE FROM playlist_items WHERE id = ? AND playlist_id = ?", (item_id, p[0]))
        conn.commit()
        return {"ok": True, "deleted_item_id": item_id}
    finally:
        conn.close()


def playlist_delete(name: str, db_path: Optional[Path] = None) -> Dict[str, Any]:
    conn = init_db(db_path)
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM playlists WHERE name = ?", (name.strip(),))
        conn.commit()
        return {"ok": True, "deleted_playlist": name}
    finally:
        conn.close()


def playlist_seed_presets(db_path: Optional[Path] = None) -> Dict[str, Any]:
    created = []
    for p in PRESETS:
        res = playlist_create(p["name"], p["description"], db_path=db_path)
        if res.get("ok"):
            playlist_add(p["name"], p["query"], p["name"], db_path=db_path)
            created.append(p["name"])
    return {"ok": True, "seeded": created}


# ------------------- CLI Entrypoint -------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="run-youtube-music",
        description="Search, launch, and manage YouTube and YouTube Music playlists with zero-confirmation autoplay.",
    )
    sub = p.add_subparsers(dest="command")

    # play
    p_play = sub.add_parser("play", help="Play a search query, video ID, or URL directly.")
    p_play.add_argument("query", help="Track name, artist, YouTube URL, or video ID")
    p_play.add_argument("--target", choices=["youtube", "music"], default="youtube", help="Target platform (youtube or music)")
    p_play.add_argument("--no-open", action="store_true", help="Resolve video without launching external browser")
    p_play.add_argument("--open", action="store_true", default=True, help="Launch default browser with autoplay=1")

    # pause
    sub.add_parser("pause", help="Pause active YouTube/Music playback")

    # resume
    sub.add_parser("resume", help="Resume active YouTube/Music playback")

    # stop
    sub.add_parser("stop", help="Stop active YouTube/Music playback")

    # status
    sub.add_parser("status", help="Get current YouTube/Music playback status")

    # search
    p_search = sub.add_parser("search", help="Search YouTube and return matching videos as JSON.")
    p_search.add_argument("query", help="Search query")
    p_search.add_argument("--limit", type=int, default=5, help="Maximum number of results (default 5)")

    # presets
    p_presets = sub.add_parser("presets", help="List or seed predefined focus playlists.")
    p_presets.add_argument("--seed", action="store_true", help="Seed presets into your playlists database")

    # playlist subcommands
    p_pl = sub.add_parser("playlist", help="Manage custom playlists.")
    pl_sub = p_pl.add_subparsers(dest="playlist_action")

    # playlist create
    p_pl_new = pl_sub.add_parser("create", help="Create a new playlist.")
    p_pl_new.add_argument("name", help="Name of the playlist")
    p_pl_new.add_argument("--description", default="", help="Optional description")

    # playlist list
    pl_sub.add_parser("list", help="List all playlists.")

    # playlist show
    p_pl_show = pl_sub.add_parser("show", help="Show tracks in a playlist.")
    p_pl_show.add_argument("name", help="Name of the playlist")

    # playlist add
    p_pl_add = pl_sub.add_parser("add", help="Add a track or search query to a playlist.")
    p_pl_add.add_argument("name", help="Name of the playlist")
    p_pl_add.add_argument("track", help="Song name, YouTube URL, or search query")
    p_pl_add.add_argument("--title", default="", help="Custom title override")

    # playlist remove
    p_pl_rm = pl_sub.add_parser("remove", help="Remove an item from a playlist.")
    p_pl_rm.add_argument("name", help="Name of the playlist")
    p_pl_rm.add_argument("item_id", type=int, help="Track item ID to remove")

    # playlist delete
    p_pl_del = pl_sub.add_parser("delete", help="Delete an entire playlist.")
    p_pl_del.add_argument("name", help="Name of the playlist")

    # playlist play
    p_pl_play = pl_sub.add_parser("play", help="Play a playlist starting from the first track.")
    p_pl_play.add_argument("name", help="Name of the playlist")
    p_pl_play.add_argument("--target", choices=["youtube", "music"], default="youtube", help="Target platform")
    p_pl_play.add_argument("--no-open", action="store_true", help="Do not launch browser")

    return p


def main(argv: Optional[List[str]] = None) -> None:
    p = build_parser()
    args = p.parse_args(argv)

    if not args.command:
        # Default behavior: output help JSON
        p.print_help()
        return

    if args.command == "play":
        target = args.target
        resolved = resolve_playable_item(args.query, target=target)
        if not resolved:
            print(json.dumps({"ok": False, "error": f"No video found for query: '{args.query}'"}))
            sys.exit(1)

        set_playback_state("playing", resolved, target=target, query=args.query)

        opened = False
        if not args.no_open:
            opened = open_browser_cleanly(resolved["play_url"])

        out = {
            "ok": True,
            "action": "play",
            "query": args.query,
            "video_id": resolved["id"],
            "title": resolved["title"],
            "play_url": resolved["play_url"],
            "target": target,
            "browser_opened": opened,
            "embed_url": resolved["embed_url"],
        }
        print(json.dumps(out, ensure_ascii=False))

    elif args.command == "pause":
        st = set_playback_state("paused")
        print(json.dumps({"ok": True, "action": "pause", "state": "paused", "playback": st}, ensure_ascii=False))

    elif args.command == "resume":
        st = set_playback_state("playing")
        print(json.dumps({"ok": True, "action": "resume", "state": "playing", "playback": st}, ensure_ascii=False))

    elif args.command == "stop":
        st = set_playback_state("stopped")
        print(json.dumps({"ok": True, "action": "stop", "state": "stopped", "playback": st}, ensure_ascii=False))

    elif args.command == "status":
        st = get_playback_state()
        print(json.dumps({"ok": True, "playback": st}, ensure_ascii=False))

    elif args.command == "search":
        items = search_youtube(args.query, limit=args.limit)
        print(json.dumps({"ok": True, "query": args.query, "results": items}, ensure_ascii=False))

    elif args.command == "presets":
        if getattr(args, "seed", False):
            res = playlist_seed_presets()
            print(json.dumps(res, ensure_ascii=False))
        else:
            print(json.dumps({"ok": True, "presets": PRESETS}, ensure_ascii=False))

    elif args.command == "playlist":
        act = args.playlist_action
        if act == "create":
            res = playlist_create(args.name, args.description)
            print(json.dumps(res, ensure_ascii=False))
        elif act == "list":
            res = playlist_list()
            print(json.dumps(res, ensure_ascii=False))
        elif act == "show":
            res = playlist_show(args.name)
            print(json.dumps(res, ensure_ascii=False))
        elif act == "add":
            res = playlist_add(args.name, args.track, custom_title=args.title)
            print(json.dumps(res, ensure_ascii=False))
        elif act == "remove":
            res = playlist_remove(args.name, args.item_id)
            print(json.dumps(res, ensure_ascii=False))
        elif act == "delete":
            res = playlist_delete(args.name)
            print(json.dumps(res, ensure_ascii=False))
        elif act == "play":
            shown = playlist_show(args.name)
            if not shown.get("ok"):
                print(json.dumps(shown, ensure_ascii=False))
                sys.exit(1)
            tracks = shown.get("playlist", {}).get("tracks", [])
            if not tracks:
                print(json.dumps({"ok": False, "error": f"Playlist '{args.name}' has no tracks"}))
                sys.exit(1)
            first = tracks[0]
            url = first["music_url"] if args.target == "music" else f"https://www.youtube.com/watch?v={first['video_id']}&autoplay=1"
            opened = False
            if not args.no_open:
                opened = open_browser_cleanly(url)
            print(
                json.dumps(
                    {
                        "ok": True,
                        "action": "play_playlist",
                        "playlist": args.name,
                        "total_tracks": len(tracks),
                        "first_track": first,
                        "url": url,
                        "browser_opened": opened,
                    },
                    ensure_ascii=False,
                )
            )
        else:
            p.print_help()


if __name__ == "__main__":
    main()

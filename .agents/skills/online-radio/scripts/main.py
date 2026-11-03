#!/usr/bin/env python3
"""Online Radio Player & Station Manager Skill.

Provides streaming endpoints, station management, and search for Czech,
New Zealand, and worldwide internet radio stations.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any, Dict, List, Optional

# Default stations seeded on first run
DEFAULT_STATIONS = [
    {
        "name": "Radio Krokodýl",
        "url": "http://icecast4.play.cz/krokodyl128.mp3",
        "country": "CZ",
        "genre": "Pop & Hits",
        "description": "Brno & South Moravia - Nejhrajnější rádio",
    },
    {
        "name": "Český Rozhlas Radiožurnál",
        "url": "http://icecast7.play.cz/cro1-128.mp3",
        "country": "CZ",
        "genre": "News & Talk",
        "description": "Zpravodajství, publicistika a hudba ČRo",
    },
    {
        "name": "Radio Beat",
        "url": "http://icecast1.play.cz/radiobeat128.mp3",
        "country": "CZ",
        "genre": "Classic Rock",
        "description": "První rockové rádio v ČR",
    },
    {
        "name": "Radio 1",
        "url": "http://icecast1.play.cz/radio1.mp3",
        "country": "CZ",
        "genre": "Alternative & Indie",
        "description": "Pražská nezávislá alternativní scéna",
    },
    {
        "name": "RNZ National",
        "url": "http://radionz-ice.streamguys.com/national.mp3",
        "country": "NZ",
        "genre": "Public Radio & Stories",
        "description": "Radio New Zealand National - News, in-depth features & music",
    },
    {
        "name": "George FM",
        "url": "https://mediaworks.streamguys1.com/george_net_icy",
        "country": "NZ",
        "genre": "Electronic & Dance",
        "description": "New Zealand's dance, house, and drum & bass station",
    },
    {
        "name": "95bFM Auckland",
        "url": "https://streams.95bfm.com/stream95",
        "country": "NZ",
        "genre": "Independent & Alternative",
        "description": "Student Radio Network Auckland - Independent broadcasting since 1969",
    },
]


def get_db_path() -> Path:
    """Resolve database path from AGENT_SKILL_DATA or workspace fallback."""
    base_dir = os.environ.get("AGENT_SKILL_DATA")
    if base_dir:
        target = Path(base_dir)
    else:
        # Default workspace location
        target = Path(__file__).resolve().parent.parent / "data"
    target.mkdir(parents=True, exist_ok=True)
    return target / "state.db"


def init_db(db_path: Path) -> sqlite3.Connection:
    """Initialize SQLite database with schema and default presets."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    with conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS stations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                url TEXT NOT NULL,
                country TEXT NOT NULL,
                genre TEXT DEFAULT 'General',
                description TEXT DEFAULT '',
                is_default INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS playback (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                state TEXT NOT NULL DEFAULT 'stopped',
                station_name TEXT,
                station_id INTEGER,
                url TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        # Check if table is empty, seed defaults
        cursor = conn.execute("SELECT COUNT(*) AS cnt FROM stations")
        if cursor.fetchone()["cnt"] == 0:
            for s in DEFAULT_STATIONS:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO stations (name, url, country, genre, description, is_default)
                    VALUES (?, ?, ?, ?, ?, 1)
                    """,
                    (s["name"], s["url"], s["country"], s["genre"], s["description"]),
                )
    return conn


def set_playback_state(state: str, station: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Update active playback status (playing, paused, stopped)."""
    db_path = get_db_path()
    conn = init_db(db_path)
    with conn:
        conn.execute(
            """
            INSERT INTO playback (id, state, station_name, station_id, url, updated_at)
            VALUES (1, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(id) DO UPDATE SET
                state = excluded.state,
                station_name = COALESCE(excluded.station_name, playback.station_name),
                station_id = COALESCE(excluded.station_id, playback.station_id),
                url = COALESCE(excluded.url, playback.url),
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                state,
                station.get("name") if station else None,
                station.get("id") if station else None,
                station.get("url") if station else None,
            ),
        )
        row = conn.execute("SELECT * FROM playback WHERE id = 1").fetchone()
        return dict(row) if row else {"state": state}


def get_playback_state() -> Dict[str, Any]:
    """Retrieve current radio playback state."""
    db_path = get_db_path()
    conn = init_db(db_path)
    with conn:
        row = conn.execute("SELECT * FROM playback WHERE id = 1").fetchone()
        if row:
            return dict(row)
        return {"id": 1, "state": "stopped", "station_name": None, "station_id": None, "url": None}


def list_stations(country: Optional[str] = None) -> List[Dict[str, Any]]:
    """List stations, optionally filtered by country code."""
    db_path = get_db_path()
    conn = init_db(db_path)
    with conn:
        if country:
            cursor = conn.execute(
                "SELECT * FROM stations WHERE UPPER(country) = ? ORDER BY is_default DESC, name ASC",
                (country.upper(),),
            )
        else:
            cursor = conn.execute(
                "SELECT * FROM stations ORDER BY country ASC, is_default DESC, name ASC"
            )
        rows = [dict(r) for r in cursor.fetchall()]
    return rows


def add_station(
    name: str,
    url: str,
    country: str = "CZ",
    genre: str = "General",
    description: str = "",
) -> Dict[str, Any]:
    """Add a new radio station to the database."""
    name = name.strip()
    url = url.strip()
    country = country.strip().upper()
    genre = genre.strip() or "General"
    description = description.strip()

    if not name:
        raise ValueError("Station name cannot be empty")
    if not url:
        raise ValueError("Station stream URL cannot be empty")

    db_path = get_db_path()
    conn = init_db(db_path)
    with conn:
        conn.execute(
            """
            INSERT INTO stations (name, url, country, genre, description, is_default)
            VALUES (?, ?, ?, ?, ?, 0)
            ON CONFLICT(name) DO UPDATE SET
                url = excluded.url,
                country = excluded.country,
                genre = excluded.genre,
                description = excluded.description
            """,
            (name, url, country, genre, description),
        )
        row = conn.execute("SELECT * FROM stations WHERE name = ?", (name,)).fetchone()
        return dict(row)


def remove_station(station_id: Optional[int] = None, name: Optional[str] = None) -> bool:
    """Remove a station by ID or Name."""
    db_path = get_db_path()
    conn = init_db(db_path)
    with conn:
        if station_id is not None:
            cur = conn.execute("DELETE FROM stations WHERE id = ?", (station_id,))
        elif name:
            cur = conn.execute("DELETE FROM stations WHERE LOWER(name) = LOWER(?)", (name.strip(),))
        else:
            raise ValueError("Either station_id or name must be provided")
        return cur.rowcount > 0


def reset_stations() -> List[Dict[str, Any]]:
    """Reset stations database to factory defaults."""
    db_path = get_db_path()
    conn = init_db(db_path)
    with conn:
        conn.execute("DELETE FROM stations")
        for s in DEFAULT_STATIONS:
            conn.execute(
                """
                INSERT INTO stations (name, url, country, genre, description, is_default)
                VALUES (?, ?, ?, ?, ?, 1)
                """,
                (s["name"], s["url"], s["country"], s["genre"], s["description"]),
            )
        cursor = conn.execute("SELECT * FROM stations ORDER BY country ASC, name ASC")
        return [dict(r) for r in cursor.fetchall()]


def _normalize_name(s: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)).lower()


def find_station(identifier: str) -> Optional[Dict[str, Any]]:
    """Find a station by ID, exact name, partial match, or stemmed/unaccented match."""
    db_path = get_db_path()
    conn = init_db(db_path)
    identifier = identifier.strip()
    with conn:
        if identifier.isdigit():
            cur = conn.execute("SELECT * FROM stations WHERE id = ?", (int(identifier),))
            row = cur.fetchone()
            if row:
                return dict(row)

        cur = conn.execute("SELECT * FROM stations WHERE LOWER(name) = LOWER(?)", (identifier,))
        row = cur.fetchone()
        if row:
            return dict(row)

        cur = conn.execute(
            "SELECT * FROM stations WHERE LOWER(name) LIKE LOWER(?) LIMIT 1",
            (f"%{identifier}%",),
        )
        row = cur.fetchone()
        if row:
            return dict(row)

        # Unaccented and stemmed Czech inflection matching
        all_rows = [dict(r) for r in conn.execute("SELECT * FROM stations").fetchall()]
        norm_id = _normalize_name(identifier)
        for r in all_rows:
            norm_name = _normalize_name(r["name"])
            if norm_id in norm_name or norm_name in norm_id:
                return r

        # Czech inflection stem (e.g. krokodýla -> krokodýl)
        stem_id = norm_id.rstrip("aeiouyáéíóúůýě")
        if len(stem_id) >= 3:
            for r in all_rows:
                norm_name = _normalize_name(r["name"])
                if stem_id in norm_name:
                    return r

    return None


def search_online_directory(query: str, limit: int = 10) -> List[Dict[str, Any]]:
    """Search for radio stations across public Radio-Browser API."""
    query = query.strip()
    if not query:
        return []

    encoded = urllib.parse.quote(query)
    api_url = f"https://de1.api.radio-browser.info/json/stations/byname/{encoded}?limit={limit}"
    req = urllib.request.Request(
        api_url,
        headers={"User-Agent": "AntigravityOnlineRadio/1.0", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
            results = []
            for item in data:
                url_resolved = item.get("url_resolved") or item.get("url")
                if not url_resolved:
                    continue
                results.append(
                    {
                        "name": item.get("name", "").strip(),
                        "url": url_resolved,
                        "country": item.get("countrycode", "GLOBAL").upper() or "GLOBAL",
                        "genre": item.get("tags", "").split(",")[0].title() if item.get("tags") else "Radio",
                        "homepage": item.get("homepage", ""),
                        "codec": item.get("codec", "MP3"),
                        "bitrate": item.get("bitrate", 128),
                    }
                )
            return results
    except Exception:
        # Fallback if external API is unreachable or rate-limited
        return []


def main() -> int:
    parser = argparse.ArgumentParser(description="Online Radio Skill CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # list
    p_list = subparsers.add_parser("list", help="List radio stations")
    p_list.add_argument("--country", "-c", help="Filter by country code (e.g. CZ, NZ)")

    # play
    p_play = subparsers.add_parser("play", help="Play or get stream info for a station")
    p_play.add_argument("station", help="Station ID or Name")
    p_play.add_argument("--open", dest="open_browser", action="store_true", default=False, help="Open stream in default browser/media app")

    # pause
    subparsers.add_parser("pause", help="Pause active radio stream")

    # stop
    subparsers.add_parser("stop", help="Stop active radio stream")

    # status
    subparsers.add_parser("status", help="Get current radio playback status")

    # add
    p_add = subparsers.add_parser("add", help="Add or update a radio station")
    p_add.add_argument("--name", "-n", required=True, help="Station name")
    p_add.add_argument("--url", "-u", required=True, help="Stream URL (MP3/AAC/HLS)")
    p_add.add_argument("--country", "-c", default="CZ", help="Country code (e.g. CZ, NZ, US)")
    p_add.add_argument("--genre", "-g", default="General", help="Genre or tags")
    p_add.add_argument("--desc", "-d", default="", help="Description")

    # remove
    p_rm = subparsers.add_parser("remove", help="Remove a radio station")
    p_rm.add_argument("--id", type=int, help="Station ID")
    p_rm.add_argument("--name", help="Station Name")

    # search
    p_search = subparsers.add_parser("search", help="Search online radio directory")
    p_search.add_argument("query", help="Search query (station name, city, tag)")
    p_search.add_argument("--limit", type=int, default=8, help="Max results")

    # reset
    subparsers.add_parser("reset", help="Reset to factory preset stations")

    args = parser.parse_args()

    try:
        if args.command == "list":
            stations = list_stations(args.country)
            print(json.dumps({"ok": True, "count": len(stations), "stations": stations}, ensure_ascii=False, indent=2))
            return 0

        elif args.command == "play":
            st = find_station(args.station)
            if not st:
                print(json.dumps({"ok": False, "error": f"Station '{args.station}' not found in database"}, ensure_ascii=False))
                return 1
            set_playback_state("playing", st)
            if args.open_browser:
                webbrowser.open(st["url"])
            print(json.dumps({"ok": True, "station": st, "action": "play", "state": "playing", "opened_browser": args.open_browser}, ensure_ascii=False, indent=2))
            return 0

        elif args.command == "pause":
            st = set_playback_state("paused")
            print(json.dumps({"ok": True, "action": "pause", "state": "paused", "playback": st}, ensure_ascii=False, indent=2))
            return 0

        elif args.command == "stop":
            st = set_playback_state("stopped")
            print(json.dumps({"ok": True, "action": "stop", "state": "stopped", "playback": st}, ensure_ascii=False, indent=2))
            return 0

        elif args.command == "status":
            st = get_playback_state()
            print(json.dumps({"ok": True, "playback": st}, ensure_ascii=False, indent=2))
            return 0

        elif args.command == "add":
            station = add_station(
                name=args.name,
                url=args.url,
                country=args.country,
                genre=args.genre,
                description=args.desc,
            )
            print(json.dumps({"ok": True, "message": f"Station '{station['name']}' saved successfully", "station": station}, ensure_ascii=False, indent=2))
            return 0

        elif args.command == "remove":
            if not args.id and not args.name:
                print(json.dumps({"ok": False, "error": "Specify --id or --name"}, ensure_ascii=False))
                return 1
            ok = remove_station(station_id=args.id, name=args.name)
            if ok:
                print(json.dumps({"ok": True, "message": "Station removed successfully"}, ensure_ascii=False))
                return 0
            else:
                print(json.dumps({"ok": False, "error": "Station not found"}, ensure_ascii=False))
                return 1

        elif args.command == "search":
            results = search_online_directory(args.query, limit=args.limit)
            print(json.dumps({"ok": True, "query": args.query, "count": len(results), "results": results}, ensure_ascii=False, indent=2))
            return 0

        elif args.command == "reset":
            stations = reset_stations()
            print(json.dumps({"ok": True, "message": "Stations reset to defaults", "stations": stations}, ensure_ascii=False, indent=2))
            return 0

    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())

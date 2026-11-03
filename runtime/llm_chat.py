"""LLM Chat Engine with autonomous tool-calling for modular skills, memory, and devices.
Supports local LLM on Mac Mini (LM Studio) and Google Gemini API via OpenAI-compatible endpoints."""
from __future__ import annotations

import json
import os
import re
import sqlite3
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "runtime"))
import core  # noqa: E402
import inbox  # noqa: E402
import memory  # noqa: E402
import scheduler  # noqa: E402
import skills  # noqa: E402

ENV_FILE = ROOT / ".env"
ENV_EXAMPLE = ROOT / ".env.example"
SKILLS_DIR = ROOT / ".agents" / "skills"

SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL DEFAULT 'default',
    role TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    tool_calls TEXT,
    tool_call_id TEXT,
    name TEXT,
    prompt_tokens INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    total_tokens INTEGER DEFAULT 0,
    cached_tokens INTEGER DEFAULT 0,
    created_at TEXT NOT NULL
);
"""


def load_env() -> dict[str, str]:
    """Parse .env file without external dependencies."""
    vals = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                vals[k.strip()] = v.strip().strip("'\"")
    return vals


def save_env(updates: dict[str, str]) -> None:
    current = load_env()
    current.update(updates)
    lines = [f"{k}={v}" for k, v in sorted(current.items())]
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def get_config() -> dict:
    env = load_env()
    from secrets_store import get_secret
    gemini_key = env.get("GEMINI_API_KEY") or get_secret("GEMINI_API_KEY") or ""

    default_mac_url = "http://127.0.0.1:1234/v1" if sys.platform == "darwin" else "http://192.168.1.100:1234/v1"
    provider = env.get("LLM_PROVIDER", "macmini").lower()
    return {
        "provider": provider,
        "macmini": {
            "base_url": env.get("MACMINI_BASE_URL", default_mac_url),
            "model": env.get("MACMINI_MODEL", "nvidia-nemotron-3.5-bolt-e4b-mlx"),
            "api_key": env.get("MACMINI_API_KEY", "lm-studio"),
        },
        "gemini": {
            "base_url": env.get("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai"),
            "model": env.get("GEMINI_MODEL", "gemini-3.8-flash"),
            "api_key": gemini_key,
        },
    }


def get_active_provider_info() -> dict:
    cfg = get_config()
    p = cfg["provider"]
    target = cfg.get(p, cfg["macmini"])
    return {
        "provider": p,
        "base_url": target.get("base_url"),
        "model": target.get("model"),
        "has_key": bool(target.get("api_key")),
    }


def db() -> sqlite3.Connection:
    con = core.connect()
    con.executescript(SCHEMA)
    # Ensure token columns exist on older tables
    cols = [r["name"] for r in con.execute("PRAGMA table_info(chat_messages)").fetchall()]
    for col_name in ("prompt_tokens", "completion_tokens", "total_tokens", "cached_tokens"):
        if col_name not in cols:
            con.execute(f"ALTER TABLE chat_messages ADD COLUMN {col_name} INTEGER DEFAULT 0")
    con.commit()
    return con


# ---- Dynamic Skills Discovery & Tools Registry -----------------------------

def get_installed_skills_info() -> list[dict]:
    """Scan .agents/skills/ to extract all registered skills and their descriptions."""
    items = []
    if not SKILLS_DIR.exists():
        return items
    for d in sorted(p for p in SKILLS_DIR.iterdir() if p.is_dir()):
        md = d / "SKILL.md"
        desc = ""
        if md.exists():
            try:
                text = md.read_text(encoding="utf-8")
                m = re.search(r"^description:\s*(.+)$", text, re.MULTILINE)
                if m:
                    desc = m.group(1).strip().strip("'\"")
            except Exception:
                pass
        items.append({"name": d.name, "description": desc})
    return items


WMO_CODES = {
    0: ("Jasno", "Clear sky"),
    1: ("Převážně jasno", "Mainly clear"),
    2: ("Polojasno", "Partly cloudy"),
    3: ("Zataženo", "Overcast"),
    45: ("Mlha", "Fog"),
    48: ("Námraza a mlha", "Depositing rime fog"),
    51: ("Slabé mrholení", "Light drizzle"),
    53: ("Mírné mrholení", "Moderate drizzle"),
    55: ("Husté mrholení", "Dense drizzle"),
    61: ("Slabý déšť", "Slight rain"),
    63: ("Mírný déšť", "Moderate rain"),
    65: ("Silný déšť", "Heavy rain"),
    71: ("Slabé sněžení", "Slight snow fall"),
    73: ("Mírné sněžení", "Moderate snow fall"),
    75: ("Silné sněžení", "Heavy snow fall"),
    80: ("Slabé dešťové přeháňky", "Slight rain showers"),
    81: ("Mírné přeháňky", "Moderate rain showers"),
    82: ("Prudké přeháňky", "Violent rain showers"),
    95: ("Bouřka", "Thunderstorm"),
}


def fetch_weather(location: str) -> str:
    """Fetch live weather and today's forecast for any city or location via Open-Meteo."""
    def _open_url(url: str):
        req = urllib.request.Request(url, headers={"User-Agent": "AntigravityPersonalAgent/1.0"})
        try:
            return urllib.request.urlopen(req, timeout=12)
        except (ssl.SSLError, urllib.error.URLError):
            ctx = ssl._create_unverified_context()
            return urllib.request.urlopen(req, context=ctx, timeout=12)

    try:
        geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={urllib.parse.quote(location)}&count=1&language=en&format=json"
        with _open_url(geo_url) as resp:
            geo_data = json.loads(resp.read().decode("utf-8"))
        results = geo_data.get("results")
        if not results:
            return json.dumps({"ok": False, "error": f"Location '{location}' not found."})

        place = results[0]
        lat, lon = place["latitude"], place["longitude"]
        city_name = place.get("name", location)
        country = place.get("country", "")

        forecast_url = (
            f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
            "&current=temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,weather_code,wind_speed_10m"
            "&daily=weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum"
            "&timezone=auto"
        )
        with _open_url(forecast_url) as resp:
            wdata = json.loads(resp.read().decode("utf-8"))

        curr = wdata.get("current", {})
        daily = wdata.get("daily", {})
        code = curr.get("weather_code", 0)
        cz_desc, en_desc = WMO_CODES.get(code, ("Proměnlivo", "Variable"))

        summary = {
            "ok": True,
            "location": f"{city_name}, {country}",
            "current": {
                "temperature_celsius": curr.get("temperature_2m"),
                "apparent_temperature_celsius": curr.get("apparent_temperature"),
                "condition_cz": cz_desc,
                "condition_en": en_desc,
                "humidity_percent": curr.get("relative_humidity_2m"),
                "wind_speed_kmh": curr.get("wind_speed_10m"),
            },
            "today_forecast": {
                "temp_max_celsius": daily.get("temperature_2m_max", [None])[0],
                "temp_min_celsius": daily.get("temperature_2m_min", [None])[0],
                "precipitation_sum_mm": daily.get("precipitation_sum", [None])[0],
            },
        }
        return json.dumps(summary, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"ok": False, "error": f"Failed to fetch weather for '{location}': {e}"})


def build_tools() -> list[dict]:
    """Expose agent skills, devices, memory, and weather as OpenAI-compatible function definitions."""
    installed = [s["name"] for s in get_installed_skills_info()]
    skills_names_str = ", ".join(f"'{name}'" for name in installed)

    return [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get real-time current weather conditions and today's forecast for any city or location (e.g. Prague, Brno, Ostrava, London, New York).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "location": {"type": "string", "description": "Name of the city or location (e.g. 'Praha', 'Brno', 'Prague')"}
                    },
                    "required": ["location"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "check_macmini",
                "description": "Check reachability and Apple M4 system vitals (CPU load, memory %, disk %, uptime) of the Mac Mini at 192.168.1.100.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["status", "wake", "history"],
                            "description": "Action to perform: 'status' to probe live vitals, 'wake' for Wake-on-LAN, 'history' for check log.",
                        }
                    },
                    "required": ["action"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "execute_skill",
                "description": (
                    f"Execute any modular skill in its isolated environment. Installed skills: {skills_names_str}. "
                    "Examples: skill='audio-control', args=['volume', '50'] or args=['pause'] or args=['mute'] or args=['stop']; "
                    "skill='online-radio', args=['play', 'Radio Krokodýl']; "
                    "skill='run-youtube-music', args=['play', 'lofi beats']; "
                    "skill='crypto-tracker', args=['get', 'BTC']; "
                    "skill='home-device-control', args=['ping', '192.168.1.1']; "
                    "skill='web-researcher', args=['search', 'topic']."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "skill": {"type": "string", "description": f"Name of the installed skill ({skills_names_str})"},
                        "script": {"type": "string", "default": "main.py", "description": "Script name inside skill (default 'main.py')"},
                        "args": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Command line arguments for the skill",
                        },
                    },
                    "required": ["skill", "args"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "search_memory",
                "description": "Search user's long-term personal memory, facts, hardware devices, and preferences via FTS5 full-text search.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search keywords (supports diacritics and partial words)"}
                    },
                    "required": ["query"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "add_memory",
                "description": "Save a new factual memory or user preference into long-term storage.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "subject": {"type": "string", "description": "Subject title (e.g. Car, Living Room Lights, Workstation)"},
                        "content": {"type": "string", "description": "Factual details to remember"},
                        "kind": {
                            "type": "string",
                            "enum": ["fact", "preference", "device", "person", "place", "account", "project"],
                            "default": "fact",
                        },
                        "pin": {"type": "boolean", "default": False, "description": "Whether to pin into the user profile rule"},
                    },
                    "required": ["subject", "content"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "send_notification",
                "description": "Emit a desktop toast notification and persist a message to the user's Inbox.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "Notification title"},
                        "message": {"type": "string", "description": "Notification message body"},
                    },
                    "required": ["title", "message"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "list_scheduled_jobs",
                "description": "List all active scheduled automations, timers, and recurring jobs.",
                "parameters": {"type": "object", "properties": {}},
            },
        },
    ]


def execute_tool_call(name: str, arguments: dict) -> str:
    """Execute tool call and return JSON or string result safely without terminating."""
    try:
        if name == "get_weather":
            loc = arguments.get("location") or "Praha"
            return fetch_weather(loc)

        elif name == "check_macmini":
            action = arguments.get("action", "status")
            rc, out = skills.run_skill("macmini-monitor", "main.py", [action], capture=True, timeout=15)
            return out.strip() or f"macmini-monitor exited with code {rc}"

        elif name == "execute_skill":
            raw_skill = str(arguments.get("skill") or "").strip().lower()
            installed = [d.name for d in SKILLS_DIR.iterdir() if d.is_dir()]

            # Smart skill name resolution
            target_skill = raw_skill.replace("_", "-")
            if target_skill not in installed:
                skill_map = {
                    "radio": "online-radio",
                    "online_radio": "online-radio",
                    "youtube": "run-youtube-music",
                    "music": "run-youtube-music",
                    "crypto": "crypto-tracker",
                    "weather": "get_weather",
                    "macmini": "macmini-monitor",
                }
                for k, v in skill_map.items():
                    if k in target_skill:
                        target_skill = v
                        break
                else:
                    for ins in installed:
                        if target_skill in ins or ins in target_skill:
                            target_skill = ins
                            break

            if target_skill == "get_weather":
                loc = arguments.get("args", ["Praha"])[0] if arguments.get("args") else "Praha"
                return fetch_weather(loc)

            script = str(arguments.get("script") or "").strip()
            raw_args = arguments.get("args", [])
            if isinstance(raw_args, str):
                raw_args = [raw_args]

            import shlex
            args = []
            for a in raw_args:
                sa = str(a).strip()
                if " " in sa and not (sa.startswith('"') and sa.endswith('"')):
                    try:
                        args.extend(shlex.split(sa))
                    except Exception:
                        args.extend(sa.split())
                else:
                    args.append(sa)

            # If model repeated skill name as script or left empty, default to main.py
            if not script or script == target_skill or script.replace("-", "_") == target_skill or script in ("main", "run"):
                script = "main.py"

            # If script is actually a subcommand, prepend to args
            if script in ("play", "list", "status", "search", "add", "remove", "reset", "wake"):
                args.insert(0, script)
                script = "main.py"

            # Special case: online-radio (ensure 'play' action)
            if target_skill == "online-radio":
                if not args or args[0] not in ("play", "pause", "stop", "status", "list", "add", "remove", "search", "reset"):
                    args.insert(0, "play")
                # Note: We do NOT append --open here; that way the stream URL is returned cleanly
                # and plays inside the dashboard widget without opening redundant external browser tabs.

            # Special case: run-youtube-music (ensure 'play' action and resolve metadata cleanly)
            if target_skill == "run-youtube-music":
                if not args or args[0] not in ("play", "pause", "stop", "resume", "status", "search", "presets", "playlist"):
                    args.insert(0, "play")
                if args and args[0] == "play" and "--no-open" not in args and "--open" not in args:
                    args.append("--no-open")

            rc, out = skills.run_skill(target_skill, script, args, capture=True, timeout=30)
            return out.strip() or f"Skill {target_skill} completed with code {rc}"

        elif name == "search_memory":
            q = arguments.get("query", "")
            con = memory.db()
            fts_q = memory._fts_query(q)
            if not fts_q:
                return "No matching memories found."
            sql = "SELECT f.* FROM facts_fts JOIN facts f ON f.id=facts_fts.rowid WHERE facts_fts MATCH ? ORDER BY rank LIMIT 5"
            rows = [memory._row(r) for r in con.execute(sql, (fts_q,))]
            return json.dumps(rows, ensure_ascii=False, indent=2)

        elif name == "add_memory":
            import argparse
            ns = argparse.Namespace(
                subject=arguments.get("subject", ""),
                content=arguments.get("content", ""),
                kind=arguments.get("kind", "fact"),
                tags="",
                source="chat",
                pin=arguments.get("pin", False),
            )
            con = memory.db()
            ts = core.utc_now_iso()
            cur = con.execute(
                "INSERT INTO facts(kind,subject,content,tags,source,pinned,created,updated) VALUES(?,?,?,?,?,?,?,?)",
                (ns.kind, ns.subject, ns.content, "", ns.source, int(ns.pin), ts, ts),
            )
            con.commit()
            if ns.pin:
                memory.render_profile()
            return f"Successfully saved memory #{cur.lastrowid}: '{ns.subject}'"

        elif name == "send_notification":
            title = arguments.get("title", "Alert")
            msg = arguments.get("message", "")
            res = inbox.notify(title, msg, source="chat")
            return json.dumps(res)

        elif name == "list_scheduled_jobs":
            con = scheduler.db()
            rows = [dict(r) for r in con.execute("SELECT id, kind, sched_type, spec, enabled, next_run, last_status FROM jobs")]
            return json.dumps(rows, ensure_ascii=False, indent=2)

        return f"Unknown tool: {name}"
    except Exception as e:
        return f"Tool execution error ({name}): {e}"


# ---- Chat API Interaction --------------------------------------------------

def call_llm(messages: list[dict], tools: list[dict] | None = None, timeout: int = 45) -> dict:
    cfg = get_config()
    provider = cfg["provider"]
    target = cfg.get(provider, cfg["macmini"])
    base_url = target.get("base_url", "").rstrip("/")
    model = target.get("model")
    api_key = target.get("api_key", "")

    endpoint = f"{base_url}/chat/completions"
    payload = {
        "model": model,
        "messages": messages,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    headers = {
        "Content-Type": "application/json",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    req_data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(endpoint, data=req_data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return {"ok": True, "data": data}
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        return {"ok": False, "error": f"HTTP {e.code}: {err_body}"}
    except (ssl.SSLError, urllib.error.URLError) as e:
        try:
            ctx = ssl._create_unverified_context()
            with urllib.request.urlopen(req, context=ctx, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return {"ok": True, "data": data}
        except Exception as e2:
            return {"ok": False, "error": f"SSL fallback error: {e2}"}
    except Exception as e:
        return {"ok": False, "error": f"Network/Connection error: {e}"}


def get_system_prompt() -> str:
    skills_info = get_installed_skills_info()
    skills_lines = "\n".join(f"- `{s['name']}`: {s['description']}" for s in skills_info)
    return f"""You are the personal AI assistant living inside Google Antigravity.
You are equipped with real tools to inspect devices, monitor systems, search memory, control smart capabilities, get weather forecasts, and run skills.

Installed Skills Available via `execute_skill`:
{skills_lines}

Key Directives:
1. When asked about weather or forecast (e.g. 'Jaké je dnes počasí v Praze?'), CALL `get_weather(location=...)`.
2. When asked to play music or radio:
   - For radio stations (e.g. 'spusť rádio krokodýl', 'přehraj rádio Beat', Radiožurnál, Radio 1), CALL `execute_skill` with skill='online-radio', args=['play', '<Station Name>', '--open'].
   - For YouTube music or tracks/playlists (e.g. 'přehraj synthwave na youtube'), CALL `execute_skill` with skill='run-youtube-music', args=['play', '<query>'].
3. When asked about the Apple Mac Mini (M4, 16GB at 192.168.1.100), CALL `check_macmini(action='status')`.
4. When asked about smart home devices, CALL `execute_skill` with skill='home-device-control'.
5. When asked to remember something or search memories, CALL `add_memory` or `search_memory`.
6. Be concise, direct, and factual. Always answer in the language the user speaks (Czech or English)."""


def send_chat_message(user_text: str, session_id: str = "default") -> dict:
    con = db()
    now_ts = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # 1. Save user message to database
    con.execute(
        "INSERT INTO chat_messages(session_id, role, content, created_at) VALUES(?,?,?,?)",
        (session_id, "user", user_text, now_ts),
    )
    con.commit()

    # 2. Build history for LLM (last 20 messages for context)
    rows = con.execute(
        "SELECT id, role, content, tool_calls, tool_call_id, name FROM chat_messages WHERE session_id=? ORDER BY id DESC LIMIT 20",
        (session_id,),
    ).fetchall()
    rows = list(reversed(rows))

    messages = [{"role": "system", "content": get_system_prompt()}]
    for r in rows:
        msg = {"role": r["role"], "content": r["content"] or ""}
        if r["tool_calls"]:
            try:
                msg["tool_calls"] = json.loads(r["tool_calls"])
            except Exception:
                pass
        if r["tool_call_id"]:
            msg["tool_call_id"] = r["tool_call_id"]
        if r["name"]:
            msg["name"] = r["name"]
        messages.append(msg)

    tools = build_tools()
    executed_tools = []

    turn_prompt_tokens = 0
    turn_completion_tokens = 0
    turn_total_tokens = 0
    turn_cached_tokens = 0

    # 3. Autonomous Tool-Calling Loop (up to 5 turns)
    for _ in range(5):
        resp = call_llm(messages, tools=tools)
        if not resp["ok"]:
            err_msg = f"LLM error: {resp.get('error')}"
            con.execute(
                "INSERT INTO chat_messages(session_id, role, content, created_at) VALUES(?,?,?,?)",
                (session_id, "assistant", err_msg, core.utc_now_iso()),
            )
            con.commit()
            return {"ok": False, "error": err_msg, "executed_tools": executed_tools}

        resp_data = resp.get("data") or {}
        usage = resp_data.get("usage") or {}
        p_tok = usage.get("prompt_tokens") or 0
        c_tok = usage.get("completion_tokens") or 0
        t_tok = usage.get("total_tokens") or (p_tok + c_tok)
        cached_tok = (usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0

        turn_prompt_tokens += p_tok
        turn_completion_tokens += c_tok
        turn_total_tokens += t_tok
        turn_cached_tokens += cached_tok

        choice = resp_data.get("choices", [{}])[0]
        asst_msg = choice.get("message", {})
        tool_calls = asst_msg.get("tool_calls") or []

        # If LLM requested tool execution
        if tool_calls:
            con.execute(
                "INSERT INTO chat_messages(session_id, role, content, tool_calls, created_at) VALUES(?,?,?,?,?)",
                (session_id, "assistant", asst_msg.get("content") or "", json.dumps(tool_calls), core.utc_now_iso()),
            )
            con.commit()
            messages.append(asst_msg)

            for tc in tool_calls:
                call_id = tc.get("id") or "call_0"
                func = tc.get("function", {})
                fn_name = func.get("name")
                try:
                    args = json.loads(func.get("arguments") or "{}")
                except Exception:
                    args = {}

                tool_result = execute_tool_call(fn_name, args)
                executed_tools.append({
                    "name": fn_name,
                    "arguments": args,
                    "result": tool_result[:500] if len(tool_result) > 500 else tool_result,
                })

                tool_msg = {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "name": fn_name,
                    "content": str(tool_result),
                }
                messages.append(tool_msg)
                con.execute(
                    "INSERT INTO chat_messages(session_id, role, content, tool_call_id, name, created_at) VALUES(?,?,?,?,?,?)",
                    (session_id, "tool", str(tool_result), call_id, fn_name, core.utc_now_iso()),
                )
                con.commit()
            continue

        # Normal text response received
        final_text = (asst_msg.get("content") or "").strip()
        # Clean internal chain-of-thought tokens if emitted by Nemotron / DeepSeek
        final_text = re.sub(r"<\|channel\|?>thought.*?<\|?channel\|?>", "", final_text, flags=re.DOTALL).strip()
        final_text = re.sub(r"<think>.*?</think>", "", final_text, flags=re.DOTALL).strip()
        if not final_text and executed_tools:
            last_tool = executed_tools[-1]
            tname = last_tool.get("name")
            if tname == "execute_skill":
                target_sk = str(last_tool.get("arguments", {}).get("skill", ""))
                if "audio" in target_sk or "volume" in target_sk:
                    final_text = "Ovládání zvuku a přehrávání bylo úspěšně provedeno."
                elif "radio" in target_sk:
                    final_text = "Rádio bylo úspěšně spuštěno."
                elif "music" in target_sk or "youtube" in target_sk:
                    final_text = "Přehrávání na YouTube bylo spuštěno."
                else:
                    final_text = f"Skill `{target_sk}` byl úspěšně spuštěn."
            elif tname == "get_weather":
                final_text = "Předpověď počasí byla načtena."
            elif tname == "check_macmini":
                final_text = "Stav Mac Mini byl zkontrolován."
            else:
                final_text = f"Akce `{tname}` byla úspěšně provedena."

        con.execute(
            """INSERT INTO chat_messages(session_id, role, content, prompt_tokens, completion_tokens, total_tokens, cached_tokens, created_at)
               VALUES(?,?,?,?,?,?,?,?)""",
            (session_id, "assistant", final_text, turn_prompt_tokens, turn_completion_tokens, turn_total_tokens, turn_cached_tokens, core.utc_now_iso()),
        )
        con.commit()

        target_skill = None
        skill_args = []
        for t in executed_tools:
            if t.get("name") == "execute_skill":
                raw_sk = str(t.get("arguments", {}).get("skill", "")).strip().lower().replace("_", "-")
                if "audio" in raw_sk or "volume" in raw_sk:
                    target_skill = "audio-control"
                elif "radio" in raw_sk:
                    target_skill = "online-radio"
                elif "youtube" in raw_sk or "music" in raw_sk:
                    target_skill = "run-youtube-music"
                elif "crypto" in raw_sk:
                    target_skill = "crypto-tracker"
                else:
                    target_skill = raw_sk
                skill_args = t.get("arguments", {}).get("args", [])
                break
            elif t.get("name") == "check_macmini":
                target_skill = "macmini-monitor"
                break

        return {
            "ok": True,
            "reply": final_text,
            "response": final_text,
            "executed_tools": executed_tools,
            "open_skill": target_skill,
            "skill_args": skill_args,
            "usage": {
                "prompt_tokens": turn_prompt_tokens,
                "completion_tokens": turn_completion_tokens,
                "total_tokens": turn_total_tokens,
                "cached_tokens": turn_cached_tokens,
            },
        }

    return {
        "ok": True,
        "reply": "Completed tool actions.",
        "executed_tools": executed_tools,
        "usage": {
            "prompt_tokens": turn_prompt_tokens,
            "completion_tokens": turn_completion_tokens,
            "total_tokens": turn_total_tokens,
            "cached_tokens": turn_cached_tokens,
        },
    }


def get_chat_history(session_id: str = "default", limit: int = 50) -> list[dict]:
    con = db()
    rows = con.execute(
        """SELECT id, role, content, tool_calls, tool_call_id, name,
                  prompt_tokens, completion_tokens, total_tokens, cached_tokens, created_at
           FROM chat_messages WHERE session_id=? ORDER BY id ASC LIMIT ?""",
        (session_id, limit),
    ).fetchall()
    items = []
    for r in rows:
        item = {
            "id": r["id"],
            "role": r["role"],
            "content": r["content"],
            "prompt_tokens": r["prompt_tokens"] or 0,
            "completion_tokens": r["completion_tokens"] or 0,
            "total_tokens": r["total_tokens"] or 0,
            "cached_tokens": r["cached_tokens"] or 0,
            "created_at": r["created_at"],
        }
        if r["tool_calls"]:
            try:
                item["tool_calls"] = json.loads(r["tool_calls"])
            except Exception:
                pass
        if r["tool_call_id"]:
            item["tool_call_id"] = r["tool_call_id"]
        if r["name"]:
            item["name"] = r["name"]
        items.append(item)
    return items


def get_chat_stats(session_id: str = "default") -> dict:
    """Calculate aggregated conversation tokens across the session."""
    con = db()
    row = con.execute(
        """SELECT COUNT(*) as msg_count,
                  COALESCE(SUM(prompt_tokens), 0) as tot_prompt,
                  COALESCE(SUM(completion_tokens), 0) as tot_comp,
                  COALESCE(SUM(total_tokens), 0) as tot_tokens,
                  COALESCE(SUM(cached_tokens), 0) as tot_cached
           FROM chat_messages WHERE session_id=?""",
        (session_id,),
    ).fetchone()
    if not row:
        return {
            "message_count": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "cached_tokens": 0,
        }
    return {
        "message_count": int(row["msg_count"]),
        "prompt_tokens": int(row["tot_prompt"]),
        "completion_tokens": int(row["tot_comp"]),
        "total_tokens": int(row["tot_tokens"]),
        "cached_tokens": int(row["tot_cached"]),
    }


def clear_chat_history(session_id: str = "default") -> None:
    con = db()
    con.execute("DELETE FROM chat_messages WHERE session_id=?", (session_id,))
    con.commit()

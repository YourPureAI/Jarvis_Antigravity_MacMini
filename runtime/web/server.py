"""Lightweight, zero-dependency HTTP server providing the Web UI and REST API for the personal agent."""
from __future__ import annotations

import json
import mimetypes
import os
import re
import socketserver
import subprocess
import sys
import threading
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "runtime"
STATIC_DIR = Path(__file__).resolve().parent / "static"
SKILLS_DIR = ROOT / ".agents" / "skills"

# Protect the web server from any helper or skill raising sys.exit()
os.environ["AGENT_NO_EXIT"] = "1"

sys.path.insert(0, str(RUNTIME))
import backup  # noqa: E402
import core  # noqa: E402
import inbox  # noqa: E402
import llm_chat  # noqa: E402
import memory  # noqa: E402
import scheduler  # noqa: E402
import secrets_store  # noqa: E402
import skills  # noqa: E402
import voice  # noqa: E402


class AgentApiHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

    def log_message(self, format, *args):
        # Keep console output lean
        if os.environ.get("AGENT_WEB_DEBUG"):
            sys.stderr.write("%s - - [%s] %s\n" % (self.address_string(), self.log_date_time_string(), format % args))

    def send_json(self, data: dict, status: int = 200) -> None:
        payload = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        self.wfile.write(payload)

    def read_json_body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", 0))
            if not length:
                return {}
            raw = self.rfile.read(length).decode("utf-8")
            return json.loads(raw) if raw.strip() else {}
        except Exception as e:
            return {"_parse_error": str(e)}

    def do_OPTIONS(self):
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        query = parse_qs(parsed.query)

        # Static assets routing
        if path == "" or path == "/":
            self.serve_file(STATIC_DIR / "index.html", "text/html; charset=utf-8")
            return

        if not path.startswith("/api/"):
            # Check static files
            rel = path.lstrip("/")
            file_path = (STATIC_DIR / rel).resolve()
            if STATIC_DIR in file_path.parents and file_path.is_file():
                mime, _ = mimetypes.guess_type(str(file_path))
                self.serve_file(file_path, mime or "application/octet-stream")
                return
            self.serve_file(STATIC_DIR / "index.html", "text/html; charset=utf-8")
            return

        # API Routes
        try:
            if path == "/api/overview":
                self.api_overview()
            elif path == "/api/skills":
                self.api_skills_list()
            elif path.startswith("/api/skills/") and path.endswith("/widget"):
                skill_name = path.split("/")[3]
                self.api_skill_widget(skill_name)
            elif path.startswith("/api/skills/") and path.endswith("/details"):
                skill_name = path.split("/")[3]
                self.api_skill_details(skill_name)
            elif path == "/api/memory":
                q = query.get("q", [""])[0]
                limit = int(query.get("limit", [50])[0])
                self.api_memory_list(q, limit)
            elif path == "/api/jobs":
                self.api_jobs_list()
            elif path == "/api/inbox":
                unread = query.get("unread", ["0"])[0] in ("1", "true")
                self.api_inbox_list(unread)
            elif path == "/api/backup/status":
                self.api_backup_status()
            elif path == "/api/profile":
                self.api_profile()
            elif path == "/api/chat/messages":
                session_id = query.get("session_id", ["default"])[0]
                self.send_json({
                    "ok": True,
                    "messages": llm_chat.get_chat_history(session_id),
                    "stats": llm_chat.get_chat_stats(session_id),
                })
            elif path == "/api/chat/config":
                self.send_json({"ok": True, "config": llm_chat.get_config(), "active": llm_chat.get_active_provider_info()})
            elif path == "/api/voice/status":
                self.send_json({"ok": True, "status": voice.get_status()})
            else:
                self.send_json({"ok": False, "error": f"Unknown endpoint: {path}"}, 404)
        except Exception as e:
            self.send_json({"ok": False, "error": f"Internal server error: {e}"}, 500)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        body = self.read_json_body()
        if "_parse_error" in body:
            self.send_json({"ok": False, "error": f"Malformed JSON: {body['_parse_error']}"}, 400)
            return

        try:
            if path.startswith("/api/skills/") and path.endswith("/run"):
                skill_name = path.split("/")[3]
                self.api_skill_run(skill_name, body)
            elif path.startswith("/api/skills/") and path.endswith("/test"):
                skill_name = path.split("/")[3]
                self.api_skill_test(skill_name)
            elif path.startswith("/api/skills/") and path.endswith("/lint"):
                skill_name = path.split("/")[3]
                self.api_skill_lint(skill_name)
            elif path == "/api/skills/create":
                self.api_skill_create(body)
            elif path == "/api/memory/add":
                self.api_memory_add(body)
            elif path == "/api/memory/forget":
                self.api_memory_forget(body)
            elif path == "/api/jobs/run":
                self.api_job_run(body)
            elif path == "/api/jobs/toggle":
                self.api_job_toggle(body)
            elif path == "/api/inbox/read":
                self.api_inbox_read(body)
            elif path == "/api/backup/now":
                self.api_backup_now(body)
            elif path == "/api/notify":
                self.api_notify(body)
            elif path == "/api/chat/send":
                self.api_chat_send(body)
            elif path == "/api/chat/config":
                self.api_chat_save_config(body)
            elif path == "/api/chat/clear":
                self.api_chat_clear(body)
            elif path == "/api/voice/start":
                session_id = body.get("session_id", "default")
                lang = body.get("language", "cs")
                default_voice = "Samantha" if lang == "en" else "Zuzana (Premium)"
                voice_name = body.get("voice") or default_voice
                device = body.get("device", None)
                res = voice.start(session_id=session_id, voice=voice_name, device=device, language=lang)
                self.send_json(res)
            elif path == "/api/voice/stop":
                res = voice.stop()
                self.send_json(res)
            elif path == "/api/voice/speak":
                text = body.get("text", "")
                voice_name = body.get("voice")
                ok = voice.speak_text(text, voice=voice_name)
                self.send_json({"ok": ok})
            else:
                self.send_json({"ok": False, "error": f"Unknown action: {path}"}, 404)
        except Exception as e:
            self.send_json({"ok": False, "error": f"Execution error: {e}"}, 500)

    def serve_file(self, path: Path, content_type: str) -> None:
        if not path.is_file():
            self.send_response(HTTPStatus.NOT_FOUND)
            self.end_headers()
            return
        content = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    # --- API Implementations ---

    def api_overview(self):
        hb = scheduler.heartbeat_status()
        unread = inbox.unread_count()
        con = core.connect()
        mem_count = con.execute("SELECT count(*) FROM facts").fetchone()[0]
        jobs_count = con.execute("SELECT count(*) FROM jobs WHERE enabled=1").fetchone()[0]
        remote = backup.git("remote", "get-url", "origin").stdout.strip() or None
        last_commit = backup.git("log", "-1", "--format=%h %ci %s").stdout.strip() or None

        self.send_json({
            "ok": True,
            "agent": {
                "name": "Antigravity Personal Agent",
                "version": "1.0.0",
                "root": str(ROOT),
                "python": sys.version.split()[0],
                "heartbeat": hb,
                "unread_inbox": unread,
                "memory_facts": mem_count,
                "active_jobs": jobs_count,
                "git_remote": remote,
                "last_commit": last_commit,
            }
        })

    def api_skills_list(self):
        items = []
        for d in sorted(p for p in SKILLS_DIR.iterdir() if p.is_dir()):
            md = d / "SKILL.md"
            fm = skills.parse_frontmatter(md.read_text(encoding="utf-8")) if md.exists() else {}
            widget_path = d / "widget.html"
            has_widget = widget_path.exists()
            items.append({
                "name": d.name,
                "description": fm.get("description", ""),
                "has_widget": has_widget,
                "venv": (core.ENVS_DIR / d.name).exists(),
            })
        self.send_json({"ok": True, "skills": items})

    def api_skill_widget(self, name: str):
        sdir = SKILLS_DIR / name
        widget_file = sdir / "widget.html"
        if widget_file.exists():
            html = widget_file.read_text(encoding="utf-8")
            self.send_json({"ok": True, "name": name, "custom": True, "html": html})
        else:
            # Generate fallback auto-widget
            self.send_json({"ok": True, "name": name, "custom": False, "html": self.generate_auto_widget(name, sdir)})

    def generate_auto_widget(self, name: str, sdir: Path) -> str:
        md = sdir / "SKILL.md"
        desc = ""
        if md.exists():
            fm = skills.parse_frontmatter(md.read_text(encoding="utf-8"))
            desc = fm.get("description", "")
        scripts = [f.name for f in (sdir / "scripts").glob("*.py")] if (sdir / "scripts").exists() else []
        scripts_opts = "".join(f"<option value='{s}'>{s}</option>" for s in scripts)
        return f"""
        <div class="auto-widget p-4 space-y-4">
            <div class="text-sm text-gray-400">{desc}</div>
            <div class="bg-gray-800/60 p-4 rounded-xl border border-gray-700/50 space-y-3">
                <label class="block text-xs uppercase font-semibold text-gray-400">Select Script</label>
                <select id="auto-script" class="w-full bg-gray-900 border border-gray-700 rounded-lg p-2 text-sm text-gray-200">
                    {scripts_opts or "<option value='main.py'>main.py</option>"}
                </select>
                <label class="block text-xs uppercase font-semibold text-gray-400">Arguments (space separated or flags)</label>
                <input id="auto-args" type="text" placeholder="--help" class="w-full bg-gray-900 border border-gray-700 rounded-lg p-2 text-sm text-gray-200" />
                <button onclick="runAutoSkill('{name}')" class="px-4 py-2 bg-indigo-600 hover:bg-indigo-500 rounded-lg text-sm font-medium transition text-white">
                    Execute Script
                </button>
            </div>
            <div id="auto-output" class="hidden">
                <label class="block text-xs uppercase font-semibold text-gray-400 mb-1">Execution Output</label>
                <pre id="auto-output-text" class="bg-black/80 p-3 rounded-lg text-xs font-mono text-green-400 overflow-x-auto max-h-80 whitespace-pre-wrap"></pre>
            </div>
        </div>
        <script>
            function runAutoSkill(skill) {{
                const script = document.getElementById('auto-script').value;
                const rawArgs = document.getElementById('auto-args').value.trim();
                const args = rawArgs ? rawArgs.split(/\\s+/) : [];
                const outBox = document.getElementById('auto-output');
                const outText = document.getElementById('auto-output-text');
                outBox.classList.remove('hidden');
                outText.textContent = 'Running in isolated virtualenv...';
                window.AgentBridge.runSkill(skill, script, args).then(res => {{
                    outText.textContent = res.output || JSON.stringify(res, null, 2);
                }}).catch(err => {{
                    outText.textContent = 'Error: ' + err.message;
                }});
            }}
        </script>
        """

    def api_skill_details(self, name: str):
        sdir = SKILLS_DIR / name
        if not sdir.is_dir():
            self.send_json({"ok": False, "error": f"Skill '{name}' not found"}, 404)
            return
        md = sdir / "SKILL.md"
        content = md.read_text(encoding="utf-8") if md.exists() else ""
        pp = sdir / "pyproject.toml"
        pyproject = pp.read_text(encoding="utf-8") if pp.exists() else ""
        widget_file = sdir / "widget.html"
        self.send_json({
            "ok": True,
            "name": name,
            "skill_md": content,
            "pyproject": pyproject,
            "has_widget": widget_file.exists(),
            "venv_exists": (core.ENVS_DIR / name).exists(),
        })

    def api_skill_run(self, name: str, body: dict):
        script = body.get("script", "main.py")
        args = body.get("args", [])
        if isinstance(args, str):
            args = [a for a in args.split() if a]
        timeout = int(body.get("timeout", 60))
        rc, output = skills.run_skill(name, script, args, capture=True, timeout=timeout)
        parsed_out = None
        try:
            parsed_out = json.loads(output)
        except Exception:
            pass
        self.send_json({
            "ok": rc == 0,
            "returncode": rc,
            "output": output,
            "json": parsed_out,
        })

    def api_skill_test(self, name: str):
        try:
            sdir = skills.skill_dir(name)
            tdir = sdir / "tests"
            if not tdir.is_dir():
                self.send_json({"ok": False, "error": "Skill has no tests/ directory"}, 400)
                return
            env = skills._skill_env(name, skills._secrets_for(sdir))
            unit = ["python", "-m", "unittest", "discover", "-s", ".", "-p", "test_*.py", "-v"]
            if (sdir / "pyproject.toml").exists():
                cmd = [skills.uv_exe(), "run", "--quiet", "--project", str(sdir), *unit]
            else:
                cmd = [sys.executable, *unit[1:]]
            r = subprocess.run(cmd, env=env, cwd=str(tdir), capture_output=True, text=True, timeout=30)
            self.send_json({"ok": r.returncode == 0, "returncode": r.returncode, "output": r.stdout + r.stderr})
        except Exception as e:
            self.send_json({"ok": False, "error": str(e)}, 500)

    def api_skill_lint(self, name: str):
        errs, warns = skills.lint_skill(skills.skill_dir(name))
        self.send_json({"ok": len(errs) == 0, "errors": errs, "warnings": warns})

    def api_skill_create(self, body: dict):
        name = body.get("name", "").strip().lower()
        desc = body.get("description", "").strip()
        packages = body.get("packages", [])
        create_widget = bool(body.get("create_widget", False))
        widget_desc = body.get("widget_desc", "").strip()

        if not name or not desc:
            self.send_json({"ok": False, "error": "Name and description are required"}, 400)
            return

        import argparse
        ns = argparse.Namespace(name=name, description=desc)
        try:
            skills._new(ns)
        except Exception as e:
            self.send_json({"ok": False, "error": str(e)}, 400)
            return

        if packages:
            skills._env_add(argparse.Namespace(name=name, packages=packages))

        # Scaffold widget if requested
        if create_widget:
            sdir = skills.skill_dir(name)
            wfile = sdir / "widget.html"
            wfile.write_text(self.scaffold_widget_html(name, desc, widget_desc), encoding="utf-8")

        self.send_json({"ok": True, "name": name, "has_widget": create_widget})

    def scaffold_widget_html(self, name: str, desc: str, widget_desc: str) -> str:
        title = name.replace("-", " ").title()
        hint = widget_desc or "Interactive controls and status for " + title
        return f"""<div class="skill-widget p-4 space-y-4">
    <div class="border-b border-gray-800 pb-3">
        <h3 class="text-lg font-semibold text-gray-100">{title} Control Widget</h3>
        <p class="text-xs text-gray-400 mt-1">{hint}</p>
    </div>

    <!-- Quick Action Card -->
    <div class="bg-gray-800/50 p-4 rounded-xl border border-gray-700/60 space-y-3">
        <div class="text-xs font-semibold text-indigo-400 uppercase tracking-wider">Execute Action</div>
        <div class="flex gap-2">
            <input id="action-input" type="text" placeholder="Enter input or parameter..." class="flex-1 bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-sm text-gray-200 focus:outline-none focus:border-indigo-500" />
            <button onclick="runWidgetAction()" class="px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg text-sm font-medium transition shadow-sm">
                Run
            </button>
        </div>
    </div>

    <!-- Output Box -->
    <div id="widget-result-box" class="hidden">
        <div class="text-xs font-semibold text-gray-400 uppercase mb-1">Result</div>
        <pre id="widget-result" class="bg-black/70 p-3 rounded-lg text-xs font-mono text-emerald-400 overflow-x-auto max-h-80 whitespace-pre-wrap"></pre>
    </div>
</div>

<script>
function runWidgetAction() {{
    const input = document.getElementById('action-input').value.trim();
    const box = document.getElementById('widget-result-box');
    const out = document.getElementById('widget-result');
    box.classList.remove('hidden');
    out.textContent = 'Processing...';

    window.AgentBridge.runSkill('{name}', 'main.py', input ? ['--echo', input] : []).then(res => {{
        out.textContent = res.output || JSON.stringify(res, null, 2);
    }}).catch(err => {{
        out.textContent = 'Error: ' + err.message;
    }});
}}
</script>
"""

    def api_memory_list(self, query: str, limit: int):
        con = memory.db()
        if query:
            q = memory._fts_query(query)
            if q:
                sql = "SELECT f.* FROM facts_fts JOIN facts f ON f.id=facts_fts.rowid WHERE facts_fts MATCH ? ORDER BY rank LIMIT ?"
                rows = [memory._row(r) for r in con.execute(sql, (q, limit))]
            else:
                rows = []
        else:
            rows = [memory._row(r) for r in con.execute("SELECT * FROM facts ORDER BY pinned DESC, updated DESC LIMIT ?", (limit,))]
        self.send_json({"ok": True, "facts": rows})

    def api_memory_add(self, body: dict):
        import argparse
        ns = argparse.Namespace(
            subject=body.get("subject", "").strip(),
            content=body.get("content", "").strip(),
            kind=body.get("kind", "fact"),
            tags=body.get("tags", ""),
            source=body.get("source", "web-ui"),
            pin=bool(body.get("pin", False)),
        )
        if not ns.subject or not ns.content:
            self.send_json({"ok": False, "error": "Subject and content are required"}, 400)
            return
        con = memory.db()
        ts = core.utc_now_iso()
        cur = con.execute(
            "INSERT INTO facts(kind,subject,content,tags,source,pinned,created,updated) VALUES(?,?,?,?,?,?,?,?)",
            (ns.kind, ns.subject, ns.content, ns.tags, ns.source, int(ns.pin), ts, ts),
        )
        con.commit()
        if ns.pin:
            memory.render_profile()
        self.send_json({"ok": True, "id": cur.lastrowid})

    def api_memory_forget(self, body: dict):
        fid = int(body.get("id", 0))
        con = memory.db()
        r = con.execute("SELECT pinned FROM facts WHERE id=?", (fid,)).fetchone()
        if not r:
            self.send_json({"ok": False, "error": f"Fact {fid} not found"}, 404)
            return
        con.execute("DELETE FROM facts WHERE id=?", (fid,))
        con.commit()
        if r["pinned"]:
            memory.render_profile()
        self.send_json({"ok": True, "deleted": fid})

    def api_jobs_list(self):
        con = scheduler.db()
        rows = [dict(r) for r in con.execute("SELECT * FROM jobs ORDER BY next_run")]
        runs = [dict(r) for r in con.execute("SELECT * FROM job_runs ORDER BY id DESC LIMIT 20")]
        self.send_json({"ok": True, "jobs": rows, "recent_runs": runs, "heartbeat": scheduler.heartbeat_status()})

    def api_job_run(self, body: dict):
        jid = body.get("id")
        con = scheduler.db()
        r = con.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
        if not r:
            self.send_json({"ok": False, "error": f"Job {jid} not found"}, 404)
            return
        job = dict(r)
        status, output = scheduler.execute(job)
        con.execute("INSERT INTO job_runs(job_id, started, status, output) VALUES(?,?,?,?)",
                    (jid, core.iso(core.now_local()), status + " (ui manual)", output[-1000:]))
        con.commit()
        self.send_json({"ok": status.startswith("ok"), "status": status, "output": output})

    def api_job_toggle(self, body: dict):
        jid = body.get("id")
        con = scheduler.db()
        r = con.execute("SELECT enabled, sched_type, spec FROM jobs WHERE id=?", (jid,)).fetchone()
        if not r:
            self.send_json({"ok": False, "error": f"Job {jid} not found"}, 404)
            return
        new_val = 0 if r["enabled"] else 1
        nxt = core.iso(scheduler.compute_next(r["sched_type"], r["spec"], core.now_local())) if new_val else None
        con.execute("UPDATE jobs SET enabled=?, next_run=? WHERE id=?", (new_val, nxt, jid))
        con.commit()
        self.send_json({"ok": True, "id": jid, "enabled": bool(new_val), "next_run": nxt})

    def api_inbox_list(self, unread_only: bool):
        con = inbox.db()
        sql = "SELECT * FROM inbox" + (" WHERE read=0" if unread_only else "") + " ORDER BY id DESC LIMIT 50"
        rows = [dict(r) for r in con.execute(sql)]
        self.send_json({"ok": True, "items": rows})

    def api_inbox_read(self, body: dict):
        con = inbox.db()
        all_items = bool(body.get("all", False))
        iid = body.get("id")
        if all_items:
            con.execute("UPDATE inbox SET read=1 WHERE read=0")
        elif iid:
            con.execute("UPDATE inbox SET read=1 WHERE id=?", (int(iid),))
        con.commit()
        self.send_json({"ok": True})

    def api_backup_status(self):
        remote = backup.git("remote", "get-url", "origin").stdout.strip() or None
        last = backup.git("log", "-1", "--format=%h %ci %s").stdout.strip() or None
        dirty = bool(backup.git("status", "--porcelain").stdout.strip())
        self.send_json({"ok": True, "remote": remote, "last_commit": last, "uncommitted_changes": dirty})

    def api_backup_now(self, body: dict):
        msg = body.get("message", "Web UI backup trigger")
        res = backup.backup_now(msg)
        self.send_json(res)

    def api_notify(self, body: dict):
        title = body.get("title", "Test Notification")
        msg = body.get("message", "Triggered from Antigravity Agent Web UI")
        res = inbox.notify(title, msg, source="web-ui")
        self.send_json({"ok": True, **res})

    def api_profile(self):
        prof = core.RULES_DIR / "90-user-profile.md"
        content = prof.read_text(encoding="utf-8") if prof.exists() else ""
        self.send_json({"ok": True, "content": content})

    def api_chat_send(self, body: dict):
        text = body.get("message", "").strip()
        session_id = body.get("session_id", "default")
        if not text:
            self.send_json({"ok": False, "error": "Message text is required"}, 400)
            return
        res = llm_chat.send_chat_message(text, session_id=session_id)
        res["stats"] = llm_chat.get_chat_stats(session_id)
        self.send_json(res)

    def api_chat_save_config(self, body: dict):
        updates = {}
        if "provider" in body:
            updates["LLM_PROVIDER"] = body["provider"]
        if "gemini_key" in body and body["gemini_key"]:
            updates["GEMINI_API_KEY"] = body["gemini_key"]
        if "gemini_model" in body and body["gemini_model"]:
            updates["GEMINI_MODEL"] = body["gemini_model"]
        if "macmini_url" in body and body["macmini_url"]:
            updates["MACMINI_BASE_URL"] = body["macmini_url"]
        if "macmini_model" in body and body["macmini_model"]:
            updates["MACMINI_MODEL"] = body["macmini_model"]
        if updates:
            llm_chat.save_env(updates)
        self.send_json({"ok": True, "config": llm_chat.get_config(), "active": llm_chat.get_active_provider_info()})

    def api_chat_clear(self, body: dict):
        session_id = body.get("session_id", "default")
        llm_chat.clear_chat_history(session_id)
        self.send_json({"ok": True, "stats": llm_chat.get_chat_stats(session_id)})


def start_server(host: str = "0.0.0.0", port: int = 8765, open_browser: bool = False) -> None:
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    server_address = (host, port)
    try:
        httpd = ThreadingHTTPServer(server_address, AgentApiHandler)
    except OSError:
        port = port + 1
        server_address = (host, port)
        httpd = ThreadingHTTPServer(server_address, AgentApiHandler)

    display_host = "localhost" if host in ("0.0.0.0", "") else host
    url = f"http://{display_host}:{port}"
    print(json.dumps({"ok": True, "url": url, "host": host, "port": port}))

    if open_browser:
        import webbrowser
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.server_close()


if __name__ == "__main__":
    start_server(open_browser=False)


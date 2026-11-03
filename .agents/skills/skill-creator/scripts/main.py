"""Meta-skill for creating, verifying, and testing agent skills."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(os.environ.get("AGENT_ROOT", Path(__file__).resolve().parents[4]))
PYTHON = sys.executable


def run_agent_cli(*args: str) -> dict:
    cmd = [PYTHON, str(ROOT / "runtime" / "agent.py"), *args]
    r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8")
    lines = [line.strip() for line in r.stdout.splitlines() if line.strip()]
    if lines:
        try:
            return json.loads(lines[-1])
        except Exception:
            pass
    return {"ok": r.returncode == 0, "stdout": r.stdout, "stderr": r.stderr}


def main() -> None:
    p = argparse.ArgumentParser(description="Skill lifecycle assistant")
    sub = p.add_subparsers(dest="action", required=True)

    c = sub.add_parser("create")
    c.add_argument("name", help="lowercase-hyphenated skill name")
    c.add_argument("--description", "-d", required=True)
    c.add_argument("--packages", "-p", nargs="*", default=[])
    c.add_argument("--widget", action="store_true", help="scaffold custom interactive Web UI widget (widget.html)")
    c.add_argument("--widget-desc", default="", help="custom description or intended controls for the widget")

    sub.add_parser("list")

    t = sub.add_parser("test")
    t.add_argument("name")

    l = sub.add_parser("lint")
    l.add_argument("name", nargs="?")

    a = p.parse_args()

    if a.action == "create":
        res = run_agent_cli("skill", "new", a.name, "--description", a.description)
        if not res.get("ok"):
            print(json.dumps(res))
            sys.exit(1)
        if a.packages:
            pkg_res = run_agent_cli("env", "add", a.name, *a.packages)
            res["packages_added"] = pkg_res
        if a.widget:
            sdir = ROOT / ".agents" / "skills" / a.name
            wfile = sdir / "widget.html"
            title = a.name.replace("-", " ").title()
            hint = a.widget_desc or f"Interactive controls and telemetry for {title}."
            wcontent = f"""<div class="skill-widget p-4 space-y-4">
  <div class="border-b border-slate-800 pb-3">
    <h3 class="text-base font-semibold text-slate-100">{title} Control Widget</h3>
    <p class="text-xs text-slate-400 mt-0.5">{hint}</p>
  </div>
  <div class="bg-slate-950/80 p-4 rounded-xl border border-slate-800 space-y-3">
    <div class="text-xs font-semibold text-indigo-400 uppercase tracking-wider">Execute Action</div>
    <div class="flex gap-2">
      <input id="widget-input" type="text" placeholder="Parameter or flag..." class="flex-1 bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-xs text-slate-200 focus:outline-none focus:border-indigo-500" />
      <button onclick="runCustomWidget()" class="px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg text-xs font-medium transition shadow-sm">
        Run Action
      </button>
    </div>
  </div>
  <div id="widget-out-box" class="p-3 bg-black/70 rounded-xl border border-slate-900 text-xs font-mono text-emerald-400 max-h-60 overflow-y-auto whitespace-pre-wrap hidden"></div>
</div>

<script>
async function runCustomWidget() {{
  const val = document.getElementById('widget-input').value.trim();
  const out = document.getElementById('widget-out-box');
  out.classList.remove('hidden');
  out.textContent = 'Executing...';
  const args = val ? val.split(/\\s+/) : [];
  try {{
    const res = await window.AgentBridge.runSkill('{a.name}', 'main.py', args);
    out.textContent = res.output;
  }} catch (e) {{
    out.textContent = 'Error: ' + e.message;
  }}
}}
</script>
"""
            wfile.write_text(wcontent, encoding="utf-8")
            res["widget_created"] = True
        print(json.dumps(res, indent=2))

    elif a.action == "list":
        res = run_agent_cli("skill", "list")
        print(json.dumps(res, indent=2))

    elif a.action == "test":
        res = run_agent_cli("skill", "test", a.name)
        print(json.dumps(res, indent=2))
        if not res.get("ok"):
            sys.exit(1)

    elif a.action == "lint":
        args = ["skill", "lint"]
        if a.name:
            args.append(a.name)
        res = run_agent_cli(*args)
        print(json.dumps(res, indent=2))
        if not res.get("ok"):
            sys.exit(1)


if __name__ == "__main__":
    main()

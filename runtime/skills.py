"""Skill lifecycle: scaffold, lint, isolated per-skill virtual envs (uv), run, test."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tomllib
from pathlib import Path

from core import (ENVS_DIR, ROOT, RULES_DIR, SKILL_NAME_RE, SKILLS_DIR, fail, out, skill_data_dir, skill_dir)

SKILL_MD_MAX = 12_000      # hard cap (chars) - keep SKILL.md lean, move bulk to references/
SKILL_MD_WARN = 8_000
DESC_MAX = 1024
DESC_WARN = 600
RULE_MAX = 12_000          # per rule file (chars)
ALWAYS_ON_BUDGET = 16_000  # total chars of always_on rules (keeps per-turn cost low)
SECRET_PATTERNS = [
    r"sk-[A-Za-z0-9_-]{20,}", r"AKIA[0-9A-Z]{16}", r"gh[pousr]_[A-Za-z0-9]{30,}", r"github_pat_[A-Za-z0-9_]{30,}",
    r"xox[abpr]-[A-Za-z0-9-]{10,}", r"AIza[0-9A-Za-z_-]{35}", r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    r"eyJ[A-Za-z0-9_-]{20,}\.eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}",
]
_SECRET_RE = re.compile("|".join(SECRET_PATTERNS))

PYPROJECT_TMPL = """[project]
name = "skill-{name}"
version = "0.1.0"
description = "Dependencies for the {name} skill (isolated venv: envs/{name})"
requires-python = ">=3.11"
dependencies = []

[tool.uv]
package = false

[tool.agent]
# Secret names (see `agent.py secret list`) injected as env vars by `agent.py run`.
secrets = []
"""

SKILL_TMPL = """---
name: {name}
description: >-
  {desc}
---

# {title}

## When to use
- {desc}

## Commands
Run from the workspace root (venv + secrets handled by the runner):
```
python runtime/agent.py run {name} main.py --help
```

## Data
- Persistent data dir: env `AGENT_SKILL_DATA` (= `data/skills/{name}/`). Use SQLite there if storage is needed.

## Verify
- `python runtime/agent.py skill test {name}` must pass.

## Gotchas (append lessons learned here)
- (none yet)
"""

MAIN_TMPL = '''"""Entry point for the {name} skill."""
import argparse
import json
import os


def main() -> None:
    p = argparse.ArgumentParser(description="{name} skill")
    p.add_argument("--echo", default="ok")
    a = p.parse_args()
    print(json.dumps({{"ok": True, "echo": a.echo, "data_dir": os.environ.get("AGENT_SKILL_DATA")}}))


if __name__ == "__main__":
    main()
'''

TEST_TMPL = '''import json
import subprocess
import sys
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "main.py"


class SmokeTest(unittest.TestCase):
    def test_runs(self):
        r = subprocess.run([sys.executable, str(SCRIPT), "--echo", "hi"], capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(r.stdout)["echo"], "hi")


if __name__ == "__main__":
    unittest.main()
'''


# ---- helpers ---------------------------------------------------------------

def parse_frontmatter(text: str) -> dict:
    """Tiny YAML-subset parser for name/description/trigger/globs (supports '>-' folded blocks)."""
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    fm, data, key = text[3:end].strip("\n").splitlines(), {}, None
    for line in fm:
        m = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
        if m and not line.startswith(" "):
            key, val = m.group(1), m.group(2).strip()
            data[key] = "" if val in (">-", ">", "|", "|-") else val.strip("\"'")
        elif key and line.startswith(" "):
            data[key] = (data[key] + " " + line.strip()).strip()
    return data


def uv_exe() -> str:
    exe = shutil.which("uv")
    if not exe:
        fail("uv not found on PATH (install: pip install uv)")
    return exe


def _skill_env(name: str, extra: dict | None = None) -> dict:
    env = dict(os.environ)
    for k in ("VIRTUAL_ENV", "PYTHONHOME"):
        env.pop(k, None)
    env.update(
        UV_PROJECT_ENVIRONMENT=str(ENVS_DIR / name),
        AGENT_ROOT=str(ROOT),
        AGENT_SKILL=name,
        AGENT_SKILL_DATA=str(skill_data_dir(name)),
        PYTHONUTF8="1",
        PYTHONIOENCODING="utf-8",
    )
    if extra:
        env.update(extra)
    return env


def _secrets_for(sdir: Path) -> dict:
    pp = sdir / "pyproject.toml"
    if not pp.exists():
        return {}
    tool_agent = tomllib.loads(pp.read_text(encoding="utf-8")).get("tool", {}).get("agent", {})
    names = tool_agent.get("secrets", [])
    required = tool_agent.get("secrets_required", [])
    if not names and not required:
        return {}
    from secrets_store import get_secret

    env, missing = {}, []
    for n in set(names + required):
        v = get_secret(n)
        if v is not None:
            env[n] = v
        elif n in required:
            missing.append(n)
    if missing:
        fail(f"missing required secrets {missing}; ask the user to run: python runtime/agent.py secret set <NAME>")
    return env


def _resolve_script(sdir: Path, script: str) -> Path:
    scripts = (sdir / "scripts").resolve()
    if not script:
        script = "main.py"
    path = (scripts / script).resolve()
    if scripts not in path.parents or not path.is_file():
        # Fallback to main.py if available
        if (scripts / "main.py").is_file():
            return scripts / "main.py"
        fail(f"script must exist inside {scripts}")
    return path


def run_skill(name: str, script: str, args: list[str], capture: bool = False, timeout: int | None = None):
    """Run a skill script inside its own isolated venv. Returns (returncode, output)."""
    try:
        sdir = skill_dir(name)
        if not sdir.is_dir():
            return 1, f"skill '{name}' not found"
        path = _resolve_script(sdir, script)
        env = _skill_env(name, _secrets_for(sdir))
        if (sdir / "pyproject.toml").exists():
            cmd = [uv_exe(), "run", "--quiet", "--project", str(sdir)]
            if (sdir / "uv.lock").exists():
                cmd.append("--frozen")
            cmd += ["python", str(path), *args]
        else:
            import sys

            cmd = [sys.executable, str(path), *args]
        r = subprocess.run(cmd, env=env, cwd=str(ROOT), capture_output=capture, text=True, timeout=timeout,
                           encoding="utf-8" if capture else None, errors="replace" if capture else None)
        output = ((r.stdout or "") + (r.stderr or "")) if capture else ""
        return r.returncode, output
    except Exception as e:
        return 1, f"Skill execution error: {e}"


# ---- commands --------------------------------------------------------------

def _list(_a):
    items = []
    for d in sorted(p for p in SKILLS_DIR.iterdir() if p.is_dir()):
        md = d / "SKILL.md"
        fm = parse_frontmatter(md.read_text(encoding="utf-8")) if md.exists() else {}
        items.append({"name": d.name, "description": fm.get("description", "")[:160],
                      "venv": (ENVS_DIR / d.name).exists()})
    out({"ok": True, "skills": items})


def _new(a):
    sdir = skill_dir(a.name)
    if sdir.exists():
        fail(f"skill '{a.name}' already exists")
    if len(a.name) > 64:
        fail("name too long (max 64)")
    (sdir / "scripts").mkdir(parents=True)
    (sdir / "tests").mkdir()
    title = a.name.replace("-", " ").title()
    (sdir / "SKILL.md").write_text(SKILL_TMPL.format(name=a.name, desc=a.description, title=title), encoding="utf-8")
    (sdir / "pyproject.toml").write_text(PYPROJECT_TMPL.format(name=a.name), encoding="utf-8")
    (sdir / "scripts" / "main.py").write_text(MAIN_TMPL.format(name=a.name), encoding="utf-8")
    (sdir / "tests" / "test_main.py").write_text(TEST_TMPL, encoding="utf-8")
    out({"ok": True, "created": str(sdir.relative_to(ROOT)), "next": [
        f"edit .agents/skills/{a.name}/SKILL.md and scripts/",
        f"python runtime/agent.py env add {a.name} <pkg>==<ver>  (if deps needed)",
        f"python runtime/agent.py skill test {a.name}",
        f"python runtime/agent.py skill lint {a.name}"]})


def lint_skill(sdir: Path) -> tuple[list[str], list[str]]:
    errs, warns = [], []
    md = sdir / "SKILL.md"
    if not md.exists():
        return [f"{sdir.name}: SKILL.md missing"], warns
    text = md.read_text(encoding="utf-8")
    fm = parse_frontmatter(text)
    name, desc = fm.get("name", ""), fm.get("description", "")
    if name != sdir.name:
        errs.append(f"{sdir.name}: frontmatter name '{name}' must equal folder name")
    if not SKILL_NAME_RE.match(sdir.name) or len(sdir.name) > 64:
        errs.append(f"{sdir.name}: folder name must be lowercase-hyphenated, <=64 chars")
    if not desc:
        errs.append(f"{sdir.name}: description missing")
    elif len(desc) > DESC_MAX:
        errs.append(f"{sdir.name}: description {len(desc)} chars > {DESC_MAX}")
    elif len(desc) > DESC_WARN:
        warns.append(f"{sdir.name}: description {len(desc)} chars (aim < {DESC_WARN}; it is paid every turn)")
    if len(text) > SKILL_MD_MAX:
        errs.append(f"{sdir.name}: SKILL.md {len(text)} chars > {SKILL_MD_MAX}; move detail to references/")
    elif len(text) > SKILL_MD_WARN:
        warns.append(f"{sdir.name}: SKILL.md {len(text)} chars (aim < {SKILL_MD_WARN})")
    for link in re.findall(r"\]\((?!https?:|file:|#)([^)\s]+)\)", text):
        if not (sdir / link.split("#")[0]).exists():
            errs.append(f"{sdir.name}: broken relative link '{link}'")
    if any((sdir / "scripts").glob("*.py")) and not (sdir / "pyproject.toml").exists():
        errs.append(f"{sdir.name}: python scripts present but no pyproject.toml (needed for isolated venv)")
    widget = sdir / "widget.html"
    if widget.exists():
        wtext = widget.read_text(encoding="utf-8", errors="ignore")
        if len(wtext) > 30_000:
            errs.append(f"{sdir.name}: widget.html {len(wtext)} chars > 30000")
    for f in sdir.rglob("*"):
        if f.is_file() and f.suffix in (".md", ".py", ".toml", ".json", ".txt", ".yaml", ".yml", ".ps1", ".js", ".html"):
            if _SECRET_RE.search(f.read_text(encoding="utf-8", errors="ignore")):
                errs.append(f"{sdir.name}: possible secret in {f.relative_to(sdir)}")
    return errs, warns


def lint_rules() -> tuple[list[str], list[str], int]:
    errs, warns, always = [], [], 0
    for f in sorted(RULES_DIR.glob("*.md")):
        text = f.read_text(encoding="utf-8")
        fm = parse_frontmatter(text)
        trig = fm.get("trigger")
        if trig not in ("always_on", "model_decision", "glob", "manual"):
            errs.append(f"rule {f.name}: invalid/missing trigger '{trig}'")
        if trig == "model_decision" and not fm.get("description"):
            errs.append(f"rule {f.name}: model_decision requires description")
        if trig == "glob" and not (fm.get("globs") or fm.get("glob")):
            errs.append(f"rule {f.name}: glob trigger requires globs")
        if len(text) > RULE_MAX:
            errs.append(f"rule {f.name}: {len(text)} chars > {RULE_MAX}")
        if trig == "always_on":
            always += len(text)
    if always > ALWAYS_ON_BUDGET:
        errs.append(f"always_on rules total {always} chars > budget {ALWAYS_ON_BUDGET}")
    return errs, warns, always


def _lint(a):
    targets = [skill_dir(a.name)] if a.name else sorted(p for p in SKILLS_DIR.iterdir() if p.is_dir())
    errs, warns = [], []
    for d in targets:
        e, w = lint_skill(d)
        errs += e
        warns += w
    always = None
    if not a.name:
        e, w, always = lint_rules()
        errs += e
        warns += w
    out({"ok": not errs, "errors": errs, "warnings": warns, "checked": len(targets), "always_on_chars": always})
    if errs:
        raise SystemExit(1)


def _env_sync(a):
    sdir = skill_dir(a.name)
    if not (sdir / "pyproject.toml").exists():
        fail("no pyproject.toml in skill")
    env = _skill_env(a.name)
    for cmd in ([uv_exe(), "lock", "--project", str(sdir)], [uv_exe(), "sync", "--project", str(sdir)]):
        r = subprocess.run(cmd, env=env, cwd=str(ROOT), capture_output=True, text=True)
        if r.returncode:
            fail(f"{' '.join(cmd[1:2])} failed: {r.stderr[-1500:]}")
    out({"ok": True, "venv": str((ENVS_DIR / a.name).relative_to(ROOT))})


def _env_add(a):
    sdir = skill_dir(a.name)
    for p in a.packages:
        if not re.search(r"(==|~=|>=)", p):
            fail(f"pin a version for '{p}' (e.g. {p}==1.2.3) - reproducibility")
    r = subprocess.run([uv_exe(), "add", "--project", str(sdir), *a.packages], env=_skill_env(a.name),
                       cwd=str(ROOT), capture_output=True, text=True)
    if r.returncode:
        fail(f"uv add failed: {r.stderr[-1500:]}")
    out({"ok": True, "added": a.packages, "venv": f"envs/{a.name}"})


def _env_rm(a):
    shutil.rmtree(ENVS_DIR / a.name, ignore_errors=True)
    out({"ok": True, "removed_venv": a.name})


def _run(a):
    rc, _ = run_skill(a.name, a.script, a.args, timeout=a.timeout)
    raise SystemExit(rc)


def _test(a):
    sdir = skill_dir(a.name)
    tdir = sdir / "tests"
    if not tdir.is_dir():
        fail("skill has no tests/ directory")
    env = _skill_env(a.name, _secrets_for(sdir))
    unit = ["python", "-m", "unittest", "discover", "-s", ".", "-p", "test_*.py", "-v"]
    if (sdir / "pyproject.toml").exists():
        cmd = [uv_exe(), "run", "--quiet", "--project", str(sdir), *unit]
    else:
        import sys

        cmd = [sys.executable, *unit[1:]]
    r = subprocess.run(cmd, env=env, cwd=str(tdir), capture_output=True, text=True, encoding="utf-8", errors="replace")
    tail = (r.stdout + r.stderr)[-3000:]
    out({"ok": r.returncode == 0, "returncode": r.returncode, "output": tail})
    if r.returncode:
        raise SystemExit(1)


def register(sub) -> None:
    p = sub.add_parser("skill", help="skill lifecycle")
    s = p.add_subparsers(dest="action", required=True)
    s.add_parser("list").set_defaults(fn=_list)
    n = s.add_parser("new")
    n.add_argument("name")
    n.add_argument("--description", "-d", required=True)
    n.set_defaults(fn=_new)
    li = s.add_parser("lint")
    li.add_argument("name", nargs="?")
    li.set_defaults(fn=_lint)
    t = s.add_parser("test")
    t.add_argument("name")
    t.set_defaults(fn=_test)

    e = sub.add_parser("env", help="per-skill isolated virtual environments (uv)")
    es = e.add_subparsers(dest="action", required=True)
    sy = es.add_parser("sync")
    sy.add_argument("name")
    sy.set_defaults(fn=_env_sync)
    ad = es.add_parser("add")
    ad.add_argument("name")
    ad.add_argument("packages", nargs="+")
    ad.set_defaults(fn=_env_add)
    rm = es.add_parser("rm")
    rm.add_argument("name")
    rm.set_defaults(fn=_env_rm)

    r = sub.add_parser("run", help="run a skill script in its isolated venv")
    r.add_argument("name")
    r.add_argument("script")
    r.add_argument("--timeout", type=int, default=None)
    r.add_argument("args", nargs="...")
    r.set_defaults(fn=_run)


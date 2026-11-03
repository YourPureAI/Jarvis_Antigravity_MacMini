---
trigger: model_decision
description: Complete instructions for creating, isolating, testing, linting, and running modular agent skills.
---

# Skills Lifecycle & Development Standard

Every skill is a self-contained bundle under `.agents/skills/<skill-name>/` with an isolated virtual environment (`envs/<skill-name>/`) managed via `uv`.

## 1. Directory Anatomy
```text
.agents/skills/<skill-name>/
├── SKILL.md            # Frontmatter + concise user & agent instructions (< 12,000 chars)
├── pyproject.toml      # Isolated dependencies and required secrets declaration
├── uv.lock             # Deterministic dependency lockfile
├── scripts/            # Executable Python scripts
│   └── main.py         # Entrypoint invoked via runner
├── tests/              # Automated unit tests
│   └── test_main.py    # Unittest verification
├── widget.html         # (Optional) Interactive Web UI control widget
└── references/         # (Optional) Extended API docs, schemas, bulky manuals
```

## 2. Interactive Web UI Widgets (`widget.html`)
Skills can feature a custom interactive widget rendered directly in the Agent Web Dashboard:
- **Location**: `.agents/skills/<skill-name>/widget.html`.
- **User Prompt Handling**:
  - If the user specifies they want a widget (or describes desired buttons/inputs/gauges), author `widget.html` specifically matching their intent.
  - If the user asks for a widget but doesn't describe the layout, design an intuitive dashboard (e.g. status metrics, input forms, action buttons).
  - If no custom widget is created, the Web UI automatically provides a Universal Script Runner.
- **JavaScript Bridge**: Widgets communicate with the skill backend via:
  ```javascript
  const res = await window.AgentBridge.runSkill('<skill-name>', 'main.py', ['arg1', 'arg2']);
  console.log(res.output);
  ```
- **Styling**: Use standard Tailwind CSS utility classes matching the dark dashboard theme.

## 3. Standard Creation Flow

### Step A: Scaffold
```bash
python runtime/agent.py skill new <skill-name> --description "Concise action-oriented description"
```

### Step B: Add Dependencies (Pinned & Isolated)
Never install packages globally. Add them to the skill's isolated environment:
```bash
python runtime/agent.py env add <skill-name> httpx==0.28.1 pydantic==2.10.4
```

### Step C: Declare Required Secrets (if any)
In `.agents/skills/<skill-name>/pyproject.toml`:
```toml
[tool.agent]
secrets = ["HOME_ASSISTANT_TOKEN", "WEATHER_API_KEY"]
```
The user sets these securely once via:
```bash
python runtime/agent.py secret set HOME_ASSISTANT_TOKEN
```
When running the skill via `agent.py run`, the runner automatically unprotects the DPAPI secret and injects it into `os.environ` of the subprocess without printing it.

### Step D: Persistent Data Storage
If the skill stores persistent state (history, device caches, bookmarks):
- Use the path provided in `AGENT_SKILL_DATA` (resolves to `data/skills/<skill-name>/`).
- Store data in a SQLite database: `data/skills/<skill-name>/state.db`.

### Step E: Test & Lint
Before declaring a skill ready:
```bash
python runtime/agent.py skill test <skill-name>
python runtime/agent.py skill lint <skill-name>
```

### Step F: Execution
Run the skill in its isolated environment:
```bash
python runtime/agent.py run <skill-name> main.py -- [args]
```


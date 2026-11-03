---
name: crypto-tracker
description: >-
  Create skill which will triger the selected crypto currency price in USD and CZK. There should be widget for it too where user can select what crypto to check. The check will be performed only when the user access the skill or widget (so not scheduled checks). The skill must remember what crypt currencies the user selected to directly show him the prices when he open the widget.
---

# Crypto Tracker

## When to use
- Create skill which will triger the selected crypto currency price in USD and CZK. There should be widget for it too where user can select what crypto to check. The check will be performed only when the user access the skill or widget (so not scheduled checks). The skill must remember what crypt currencies the user selected to directly show him the prices when he open the widget.

## Commands
Run from the workspace root (venv + secrets handled by the runner):
```
python runtime/agent.py run crypto-tracker main.py --help
```

## Data
- Persistent data dir: env `AGENT_SKILL_DATA` (= `data/skills/crypto-tracker/`). Use SQLite there if storage is needed.

## Verify
- `python runtime/agent.py skill test crypto-tracker` must pass.

## Gotchas (append lessons learned here)
- (none yet)

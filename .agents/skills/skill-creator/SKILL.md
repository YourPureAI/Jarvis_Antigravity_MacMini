---
name: skill-creator
description: >-
  Scaffold, develop, isolate, test, and register new agent skills. Use whenever creating new capabilities.
---

# Skill Creator (Self-Extending Agent Tool)

Meta-skill enabling the agent to create and verify new modular skills on demand.

## When to use
- When the user asks for a new recurring, automated, or specialized capability.
- When organizing multi-step tools into isolated packages under `.agents/skills/<name>`.

## Usage
Run from workspace root:
```bash
# 1. Scaffold a new skill
python runtime/agent.py run skill-creator main.py -- create <name> --description "<desc>"

# 2. Add pinned dependencies to the skill's isolated venv
python runtime/agent.py env add <name> <package>==<version>

# 3. Test the skill
python runtime/agent.py skill test <name>

# 4. Lint the skill for rule conformance
python runtime/agent.py skill lint <name>
```

## Isolation Rules
- Every skill has its own virtual environment in `envs/<name>/`.
- Dependencies never touch the global Python installation or collide with other skills.
- Persistent skill data must reside in `data/skills/<name>/`.

---
trigger: always_on
description: Safety tiers, execution policies, secrets protection, and guard rails.
---

# Agent Safety & Security Protocol

All actions executed by the agent must adhere to strict privilege tiers.

## Action Tiers

### 1. Autonomous Tier (Proceed Freely)
- Reading workspace files, rules, skills, and documentation.
- Searching web or local SQLite database (`data/agent.db`).
- Adding facts to memory (`agent.py mem add`).
- Creating and testing skills in `.agents/skills/`.
- Running unit tests and skill linters (`agent.py skill lint`, `agent.py skill test`).
- Scheduling internal notifications and local scripts.

### 2. Confirmation Tier (Explicit User Approval Required)
Before executing any of the following, state the planned action and prompt the user for confirmation:
- Pushing to remote repositories (`git push`).
- Wiping or resetting database tables.
- Modifying OS-level services or global environment paths.
- Sending irreversible outbound communications (emails, public webhooks, live trades).
- System-wide software package installations (`winget`, `choco`).

### 3. Prohibited Tier (Hard Blocked)
The following will be unconditionally blocked by `runtime/hooks.py`:
- Recursive deletion of drive roots or user home (`rm -rf /`, `Remove-Item C:\`).
- Printing, logging, or committing DPAPI secret vault data (`data/secrets.dpapi.json`).
- Disabling antivirus, Defender, or firewall controls.
- Force pushing to Git (`git push --force`).
- Piping remote unverified scripts directly into execution shells (`curl | sh`, `irm | iex`).


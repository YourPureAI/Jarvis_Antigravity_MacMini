---
trigger: model_decision
description: Instructions for persistent SQLite database management, encrypted secrets store, and automated Git/GitHub backups.
---

# Backup, Storage & Secret Security

## 1. Secrets Management (Windows DPAPI)
- Hardware-backed / Windows DPAPI vault (`data/secrets.dpapi.json`).
- Encrypted using the current Windows user identity.
- Never exposed in plaintext logs, stdout, or Git commits.

### Setting a secret:
```bash
# Prompt interactively (input hidden)
python runtime/agent.py secret set OPENAI_API_KEY

# Pipe programmatically if needed
echo "my-token" | python runtime/agent.py secret set OPENAI_API_KEY --stdin
```

### Listing secret names:
```bash
python runtime/agent.py secret list
```

## 2. SQLite Database Architecture
- Primary agent DB: `data/agent.db` (Memory, Jobs, Inbox, Runs).
- Per-skill DBs: `data/skills/<skill-name>/*.db`.
- All connections use `PRAGMA journal_mode=WAL` and `PRAGMA foreign_keys=ON`.
- Live `.db` binary files are `.gitignore`d to prevent corruption or repository bloat.

## 3. GitHub Backups
Database contents are exported into clean, deterministic `.sql` text dumps in `backups/db/` before Git commits.
A pre-commit secret scanner checks for leaked tokens or keys before any commit is completed.

### Commands:
```bash
# Attach private GitHub repository
python runtime/agent.py backup remote https://github.com/YourUsername/agent-backup.git

# Execute manual backup (dumps DBs, commits changes, pushes to origin)
python runtime/agent.py backup now -m "Nightly backup"

# Check backup status
python runtime/agent.py backup status

# Disaster recovery: restore all DBs from SQL text dumps
python runtime/agent.py backup restore --force
```


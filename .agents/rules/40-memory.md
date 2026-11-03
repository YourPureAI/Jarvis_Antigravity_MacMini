---
trigger: model_decision
description: Instructions for extracting, persisting, searching, and managing long-term user memory and pinned facts.
---

# Long-Term Memory & User Profile Management

The personal agent maintains a persistent SQLite database with Full-Text Search (FTS5 BM25 with unicode diacritics stripping) to remember details across sessions.

## 1. Supported Fact Categories
- `preference`: Formatting styles, communication habits, preferred languages, UI modes.
- `person`: Family members, colleagues, contacts, birthdays.
- `place`: Home address, office locations, coordinates, cities.
- `device`: Smart home devices, local network hostnames, IP addresses, specs.
- `account`: Service names, usernames, license tiers (passwords belong in secrets).
- `project`: Codebase names, goals, repository locations, active deadlines.
- `fact`: General factual knowledge relevant to the user.

## 2. Memory Operations

```bash
# Add new fact
python runtime/agent.py mem add "Smart TV" "Samsung QLED in living room at 192.168.1.50" --kind device --tags iot,livingroom

# Add pinned fact (promoted to always-on context in 90-user-profile.md)
python runtime/agent.py mem add "Language" "Prefers code comments in English and explanations in Czech" --kind preference --pin

# Semantic / Keyword search (FTS5 diacritics-insensitive)
python runtime/agent.py mem search "living room tv"

# Update or pin/unpin existing fact
python runtime/agent.py mem update <id> --content "Updated IP 192.168.1.55" --pin 1

# Delete obsolete fact
python runtime/agent.py mem forget <id>
```

## 3. Pinned Profile Synchronization
When `--pin` is specified or updated, `runtime/memory.py` automatically regenerates `.agents/rules/90-user-profile.md`. This ensures that critical facts are unconditionally inlined in the model's prompt on every turn without manual file editing.


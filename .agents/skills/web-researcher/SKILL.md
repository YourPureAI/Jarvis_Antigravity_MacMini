---
name: web-researcher
description: >-
  Deep web research, article extraction, topic synthesis, and research notebook. Use for web digests and knowledge retrieval.
---

# Web Researcher & Topic Notebook

Hermes/Grok-style web content retrieval and research database.

## When to use
- Scraping and extracting clean readability text from web URLs.
- Storing research syntheses, paper summaries, or competitive digests.
- Full-text searching across previous research sessions.

## Commands
Run from workspace root:
```bash
# Fetch and clean webpage text
python runtime/agent.py run web-researcher main.py -- fetch https://example.com

# Save a research note into the skill database
python runtime/agent.py run web-researcher main.py -- save "Title" "Summary markdown..." --url "https://..." --tags "ai,research"

# Full-text search through saved research
python runtime/agent.py run web-researcher main.py -- search "local llm"

# List recent research entries
python runtime/agent.py run web-researcher main.py -- list --limit 10
```

## Data Storage
Research records are stored in SQLite with full-text search at `data/skills/web-researcher/research.db` and backed up as SQL dumps.

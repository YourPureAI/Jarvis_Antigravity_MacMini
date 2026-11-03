---
name: online-radio
description: >-
  Play live online radio stations directly in the Agent Dashboard or system browser. Includes preloaded Czech stations (Radio Krokodýl, Český Rozhlas Radiožurnál, Radio Beat, Radio 1) and 3 New Zealand stations (RNZ National, George FM, 95bFM), with complete custom station addition, deletion, persistence, and an interactive audio streaming widget.
---

# Online Radio Skill

Provides live audio streaming and station directory management for Czech, New Zealand, and global radio stations.

## Features
- **Built-in Presets**:
  - **Czech Republic**: Rádio Krokodýl, Český Rozhlas Radiožurnál, Radio Beat, Radio 1.
  - **New Zealand**: RNZ National, George FM, 95bFM Auckland.
- **Custom Station Management**: Add, update, delete, or reset custom radio streams (saved persistently in SQLite).
- **Online Directory Search**: Search thousands of global stations via Radio-Browser API and add them with one click.
- **Interactive Dashboard Widget**: Complete audio player with sound wave visualizer, volume/mute control, country filters, and quick play.

## CLI Usage

Run commands from the workspace root:

```bash
# List all radio stations
python runtime/agent.py run online-radio main.py -- list

# Filter stations by country (e.g., CZ or NZ)
python runtime/agent.py run online-radio main.py -- list --country CZ
python runtime/agent.py run online-radio main.py -- list --country NZ

# Play or get stream URL for a station
python runtime/agent.py run online-radio main.py -- play "Radio Krokodýl"
python runtime/agent.py run online-radio main.py -- play "Český Rozhlas Radiožurnál" --open

# Add a new custom radio station
python runtime/agent.py run online-radio main.py -- add --name "Fajn Radio" --url "http://icecast1.play.cz/fajn128.mp3" --country CZ --genre Pop

# Remove a radio station by ID or name
python runtime/agent.py run online-radio main.py -- remove --name "Fajn Radio"

# Search online radio directory
python runtime/agent.py run online-radio main.py -- search "Prague"
python runtime/agent.py run online-radio main.py -- search "Auckland"

# Reset database to factory default stations
python runtime/agent.py run online-radio main.py -- reset
```

## Persistent Storage
- State is stored in SQLite: `data/skills/online-radio/state.db`.

## Verification
- Run tests: `python runtime/agent.py skill test online-radio`
- Run linter: `python runtime/agent.py skill lint online-radio`

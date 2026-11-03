---
name: run-youtube-music
description: >-
  Start music on YouTube and YouTube Music with zero-confirmation autoplay, manage custom playlists, and control playback via interactive dashboard widget.
---

# YouTube & YouTube Music Player

Play songs, artists, moods, and manage custom playlists on YouTube and YouTube Music with immediate, zero-confirmation autoplay. Includes an interactive Web UI dashboard widget.

## Why Zero-Confirmation Works
Standard approaches often trigger confirmation dialogs, cookie consent banners, bot verifications, or require manual user clicks:
1. **Search Resolution vs Search Pages**: Queries are resolved headlessly to exact video IDs before launching, preventing users from landing on search result pages that require manual clicks.
2. **Explicit Autoplay Flags**: Links are generated with `&autoplay=1` (and `&enablejsapi=1`), instructing the player to begin stream immediately.
3. **Native Authenticated Browser Session**: When launching the default browser (`webbrowser.open` / `os.startfile`), it opens inside the user's primary signed-in profile where Media Engagement Index (MEI) is established and cookie consent is already satisfied.
4. **Dashboard Zero-Click IFrame**: The widget embeds a direct YouTube player (`youtube-nocookie.com/embed/<id>?autoplay=1`) right inside the Antigravity Agent Web Dashboard.

## Commands

Run from workspace root via the runner:

### 1. Play Music / Videos
```bash
# Play query or URL on YouTube (launches default browser with autoplay=1)
python runtime/agent.py run run-youtube-music main.py play "lofi beats to study"

# Play on YouTube Music
python runtime/agent.py run run-youtube-music main.py play "daft punk get lucky" --target music

# Resolve video without opening browser (outputs JSON with play_url and embed_url)
python runtime/agent.py run run-youtube-music main.py play "hans zimmer interstellar" --no-open
```

### 2. Search Videos
```bash
python runtime/agent.py run run-youtube-music main.py search "synthwave radio" --limit 5
```

### 3. Playlists Management
```bash
# Create a playlist
python runtime/agent.py run run-youtube-music main.py playlist create "Focus Flow" --description "Deep concentration tracks"

# List playlists
python runtime/agent.py run run-youtube-music main.py playlist list

# Add song or query to a playlist
python runtime/agent.py run run-youtube-music main.py playlist add "Focus Flow" "Brian Eno Music for Airports"

# Show tracks in a playlist
python runtime/agent.py run run-youtube-music main.py playlist show "Focus Flow"

# Play a playlist (starts from track 1 with autoplay)
python runtime/agent.py run run-youtube-music main.py playlist play "Focus Flow" --target youtube

# Remove track from playlist by item ID
python runtime/agent.py run run-youtube-music main.py playlist remove "Focus Flow" 1

# Delete playlist
python runtime/agent.py run run-youtube-music main.py playlist delete "Focus Flow"

# Seed 5 built-in focus/genre presets
python runtime/agent.py run run-youtube-music main.py presets --seed
```

## Interactive Web Widget
The skill features an interactive Web Dashboard widget (`widget.html`) rendered in the Agent Web UI (`http://localhost:8765`):
- Quick search bar with instant autocomplete pills (Lofi, Synthwave, Classical, Coding Ambient, Workout).
- Target switcher: YouTube vs YouTube Music.
- Dual execution: **Play Here** (in-widget embedded player) or **In Browser** (native browser window).
- Full Playlist Manager: create playlists, view tracks, add/delete songs, and play all.

## Data & Persistence
- SQLite database: `data/skills/run-youtube-music/playlists.db` (accessed via `AGENT_SKILL_DATA`).
- Tables: `playlists`, `playlist_items`.

## Verification
```bash
python runtime/agent.py skill test run-youtube-music
python runtime/agent.py skill lint run-youtube-music
```

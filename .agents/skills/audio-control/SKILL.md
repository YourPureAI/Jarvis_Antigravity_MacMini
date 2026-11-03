---
name: audio-control
description: >-
  Universal audio and volume controller for Apple Mac Mini and agent media skills (YouTube, Radio). Controls Mac Mini master volume and mute (natively on macOS Darwin or via SSH), and provides unified pause, stop, resume, and status commands across all audio sources.
---

# Universal Audio Controller

Provides centralized master volume and mute control for Apple Mac Mini (Apple Silicon M4) alongside universal media playback controls (`pause`, `stop`, `resume`, `status`) across all agent audio skills (`online-radio`, `run-youtube-music`, and macOS system media).

## Features
- **Dual-Mode Mac Mini Volume Engine**:
  - **Local Execution**: Runs directly on macOS Darwin using native `osascript` in-process.
  - **Remote Orchestration**: Controls Mac Mini transparently over SSH (`macmini`) from Windows/Linux dev hosts.
- **Precision Volume Controls**:
  - Get volume status (output volume 0-100%, input volume, alert volume, mute state).
  - Set volume to exact percentage (e.g. 50%, 75%).
  - Step volume up or down (+5%, +10%, -5%, -10%).
  - One-click / one-command mute, unmute, and toggle mute.
- **Universal Media Transport**:
  - **Pause All**: Simultaneously pauses running streams in `online-radio`, `run-youtube-music`, and Mac Mini system media.
  - **Stop All**: Instantly halts and resets streams across all active players.
  - **Targeted Control**: Target specific players via `--target radio`, `--target youtube`, or `--target macmini`.
  - **Real-Time Web Dashboard Integration**: Interactive widget (`widget.html`) with live volume slider, quick volume presets, and instant zero-latency broadcast to browser audio elements and iframe players.

## CLI Usage

Run from workspace root via the agent runner:

```bash
# Get complete audio status (Mac Mini volume + all skills playback)
python runtime/agent.py run audio-control main.py status

# Get current volume level
python runtime/agent.py run audio-control main.py volume

# Set volume to exact percentage (0-100)
python runtime/agent.py run audio-control main.py volume 60

# Step volume up or down
python runtime/agent.py run audio-control main.py up 10
python runtime/agent.py run audio-control main.py down 10

# Mute / Unmute / Toggle
python runtime/agent.py run audio-control main.py mute
python runtime/agent.py run audio-control main.py unmute
python runtime/agent.py run audio-control main.py toggle-mute

# Universal playback: pause all media
python runtime/agent.py run audio-control main.py pause

# Pause specific source
python runtime/agent.py run audio-control main.py pause --target radio
python runtime/agent.py run audio-control main.py pause --target youtube

# Universal playback: stop all media
python runtime/agent.py run audio-control main.py stop

# Universal playback: resume media
python runtime/agent.py run audio-control main.py resume

# List registered audio skills
python runtime/agent.py run audio-control main.py list-skills
```

## Interactive Web Widget
The skill provides a rich, responsive Web UI dashboard widget (`widget.html`):
- Master Volume Slider (0-100%) with live percentage readout.
- Quick Volume Preset Pills: 20%, 40%, 60%, 80%, 100%.
- Volume Step Buttons: -10%, -5%, +5%, +10%.
- Mute Toggle Button with visual muted indicator.
- Master Transport Bar: **⏸ Pause All**, **⏹ Stop All**, **▶ Resume All**.
- Individual Source Status Cards:
  - 📻 **Online Radio**: Status badge + quick pause/stop.
  - ▶ **YouTube & Music**: Status badge + quick pause/stop.
  - 🍎 **Mac Mini**: System output level & mute status.
- Dispatches universal `agent-audio-control` events across dashboard widget panes for immediate zero-latency in-browser playback interruption.

## Verification
```bash
python runtime/agent.py skill test audio-control
python runtime/agent.py skill lint audio-control
```

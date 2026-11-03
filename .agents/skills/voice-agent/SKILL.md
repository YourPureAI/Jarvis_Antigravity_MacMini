---
name: voice-agent
description: >-
  Conversational Voice AI Agent powered by Apple Silicon M4, mlx-whisper turbo speech-to-text, macOS native Zuzana (Premium) text-to-speech, real-time live preview, 4s silence timeout, instant microphone button cut-off submission, and multi-turn LLM reasoning.
---

# Voice Agent Skill

Provides bidirectional conversational voice interaction for the personal agent.

## Capabilities & Architecture
- **Speech Input**: Connect IT ClipMic Pro (USBAudio1.0) or default input device via `sounddevice` (16kHz float32 PCM).
- **Voice Activity & Timing Detection**:
  - `SPEECH_START_THRESHOLD = 0.010` (RMS to begin recording)
  - `SPEECH_CONTINUE_THRESHOLD = 0.0030` (RMS to keep speech alive)
  - `PEAK_MIN_THRESHOLD = 0.035` (rejects ambient noise)
  - `MIN_SPEECH_DURATION = 0.6s` (rejects brief spikes/clicks)
  - `SILENCE_TIMEOUT = 4.0s` continuous pause auto-finish with timer countdown.
- **Microphone Hardware Button Cut-off (Instant Submission)**:
  - Detects hardware digital zero output (`zero_ratio > 0.95 and rms < 0.0001`).
  - Upon 0.6s confirmed digital zero (`DEBOUNCE_ZERO_CHUNKS = 6`), immediately submits audio without waiting for the 4s silence timeout, trimming debounce zero chunks.
- **Hardware Button Instant Interruption**:
  - When the AI agent (Zuzana) is speaking, pressing the microphone hardware button instantly terminates the speech synthesizer in <20ms and immediately restarts live microphone listening with zero cooldown. Acoustic room audio/echo from speakers is ignored to prevent false interruptions.
- **Real-Time Streaming Transcription**:
  - Live non-blocking partial transcription every 0.7s via `mlx_whisper` (`whisper-large-v3-turbo` on Apple Silicon M4).
  - Hallucination rejection filter (`titulky`, `johnyx`, `sledování`, `překlad`, `časování`, `konec`, `odběr`, etc.).
- **Voice Output (TTS)**:
  - High-quality native macOS voice `say -v "Zuzana (Premium)"`.
  - Cleans markdown symbols, emojis, and code formatting for natural Czech speech.
  - Echo-loop prevention: microphone reading is muted while speaking + 0.6s acoustic room reverberation cooldown.
- **Bilingual Support (Czech & English)**:
  - Instant one-click toggle in the Web UI dashboard between 🇨🇿 Čeština and 🇬🇧 English.
  - Automatically switches Whisper ASR language (`cs` vs `en`), native TTS voice (`Zuzana (Premium)` vs `Samantha`), and conversational LLM system prompt.
- **Dual-Platform Execution**:
  - **Windows Dev Host**: Transparently orchestrates the Mac Mini M4 audio engine over SSH (`macmini`) and renders live streaming audio & chat transcript in the Web UI.
  - **Mac Mini Production**: Runs natively in-process on macOS Darwin.

## CLI Usage

```bash
# Check status of voice engine
python runtime/agent.py run voice-agent main.py -- status

# Start voice conversation loop (Czech default)
python runtime/agent.py run voice-agent main.py -- start

# Start voice conversation loop in English
python runtime/agent.py run voice-agent main.py -- start --language en --voice Samantha

# Stop voice conversation loop
python runtime/agent.py run voice-agent main.py -- stop

# Speak text using Zuzana (Premium)
python runtime/agent.py run voice-agent main.py -- speak "Ahoj, systém je připraven."
```


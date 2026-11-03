# Antigravity Personal AI Agent



A minimal, frugal, autonomous personal AI agent living natively inside **Google Antigravity IDE**.

Combines Home Assistant smart home control, Hermes-style self-extending modular skills, Grok-style web research, Dots long-term memory, and an interactive Web Dashboard.



---



## 🚀 Quick Start: Launch the Web UI



To open the interactive Web Dashboard in your browser:



```powershell

python runtime/agent.py web --open

```



- **Dashboard URL**: [http://localhost:8765](http://localhost:8765)

- **Features**: Live system vitals, **Agent Chat (LLM + live skills execution)**, personal memory explorer, scheduled automations manager, notification inbox, and an interactive **Skills Registry** with domain-specific widgets.



---



## 💬 Interactive Agent Chat (LLM + Live Skills)



The Web Dashboard includes an interactive conversational chat window connected to your choice of LLM:

1. **Local Mac Mini (LM Studio on LAN)**: Connects to `http://192.168.1.100:1234/v1` running `nvidia-nemotron-3.5-bolt-e4b-mlx` (0 cloud token costs, private).

2. **Google Gemini API**: Connects to `https://generativelanguage.googleapis.com/v1beta/openai` running `gemini-3.8-flash` (or `gemini-2.5-flash`).



### Autonomous Tool Execution in Chat:

When chatting with the assistant, it autonomously calls tools to execute skills during conversation:

- *"Check if my Mac Mini is online"* -> Calls `check_macmini` tool live.

- *"What is stored in my long-term memory about my car?"* -> Calls `search_memory` tool live.

- *"Send a desktop notification saying hello"* -> Calls `send_notification` tool.

- *"Ping 192.168.1.100"* -> Calls `execute_skill` with `home-device-control`.



Switch providers on the fly in the UI via the provider dropdown or configure `.env`.



---



## ⏱️ Background Heartbeat (1-Minute Ticker)



To execute scheduled jobs, reminders, and health checks in the background:



- **Option A (Terminal Daemon)**:

  ```powershell

  python runtime/agent.py daemon

  ```

- **Option B (Antigravity Project-Local Sidecar)**:

  Toggle **Personal Agent Heartbeat (Local)** in the Antigravity Automations Dashboard (`sidecar://dashboard`).

  Config location: `.agents/sidecars/agent-heartbeat/sidecar.json`.



---



## 🧩 Modular Skills & Interactive Widgets



Every skill lives under `.agents/skills/<name>/` with its own isolated virtual environment in `envs/<name>/` managed via `uv`.



| Skill | Description | Interactive Widget |

| :--- | :--- | :--- |

| **`voice-agent`** | Apple Silicon M4 Voice AI: live audio streaming, mlx-whisper turbo STT, Zuzana TTS, mic button cut-off | `widget.html` |

| **`macmini-monitor`** | Reachability, SSH vitals (CPU/RAM/Disk), Wake-on-LAN for local Apple M4 Mini | `widget.html` |

| **`home-device-control`** | Home Assistant REST API entity & service control, local host ping/SSH | `widget.html` |

| **`online-radio`** | Live online radio streaming (CZ & NZ stations) with audio player controls | `widget.html` |

| **`run-youtube-music`** | YouTube / YouTube Music zero-confirmation playback & custom playlists | `widget.html` |

| **`crypto-tracker`** | Crypto currency price checks in USD & CZK with persistent tracked coins | `widget.html` |

| **`web-researcher`** | Clean article readability scraper, FTS5 searchable research notes | `widget.html` |

| **`skill-creator`** | Autonomous meta-skill to scaffold and register new skills & widgets | `widget.html` |



### Creating a New Skill with a Widget:

You can ask the agent in Antigravity chat:

> *"Create a new skill for tracking Bitcoin price with an interactive widget"*



Or via CLI:

```powershell

python runtime/agent.py run skill-creator main.py -- create my-skill --description "My skill description" --widget

```



---



---



## 🍏 Mac Mini Deployment & Dual-Sync Workflow



The complete project is deployed natively to the **Apple Mac Mini M4** at:

`~/Google_Antigravity_Agent`



Both machines remain synchronized via a secure **dual-sync model**:



### Option 1: Instant Local Deploy from Windows (Recommended)

Pushes local commits directly over local SSH into the Mac Mini repository in under 2 seconds, updates the remote working tree (`receive.denyCurrentBranch updateInstead`), and verifies test health:



```powershell

# From Windows PowerShell:

.\scripts\deploy_macmini.ps1 -RunTests

```



### Option 2: Pull from GitHub on Mac Mini

The Mac Mini's dedicated SSH key is configured as a GitHub Deploy Key for `YourPureAI/Jarvis_Antigravity_MacMini`. To update from the Mac Mini directly:



```bash

# In Mac Mini Terminal:

cd ~/Google_Antigravity_Agent

./scripts/update.sh

# or simply:

git pull origin main

```



### Running on Mac Mini:

```bash

cd ~/Google_Antigravity_Agent



# Run health doctor

python runtime/agent.py doctor



# Run full unit test suite (23 tests)

python -m unittest discover -s tests -v



# Start Web UI Dashboard

python runtime/agent.py web --port 8765 --open



# Check Voice Agent (Native Local Mode)

python runtime/agent.py run voice-agent main.py -- status

```



## 🛠️ CLI Cheat Sheet



All operations can be managed directly via `python runtime/agent.py <subcommand>`:



### 🧠 Long-Term Memory (SQLite FTS5)

```powershell

# Add a fact

python runtime/agent.py mem add "Workstation" "Custom PC with Windows 11 and RTX 4080" --kind device



# Add a pinned fact (promoted to always-on prompt in 90-user-profile.md)

python runtime/agent.py mem add "Mac Mini" "Apple M4 10-core 16GB at 192.168.1.100, alias macmini" --kind device --pin



# Diacritics-insensitive search

python runtime/agent.py mem search "mac mini"

```



### ⏱️ Scheduled Automations (0 LLM Tokens)

```powershell

# Desktop toast reminder in 30 minutes

python runtime/agent.py job add tea-timer --kind notify --at "+30m" --title "Timer" --message "Tea is ready"



# Recurring Python script check every 15 minutes

python runtime/agent.py job add check-mac --kind script --every 15 --skill macmini-monitor --script main.py --args-json '["status","--fail-if-down"]'



# List active jobs

python runtime/agent.py job list

```



### 🔒 Hardware-Backed Secrets Vault (Windows DPAPI)

```powershell

# Store credential securely (never logged, never committed)

python runtime/agent.py secret set HASS_TOKEN

```



### ☁️ Automated GitHub Backup & Disaster Recovery

```powershell

# Backup now (exports SQLite to .sql dumps, screens for secrets, commits & pushes)

python runtime/agent.py backup now -m "Manual sync"



# Disaster recovery: restore all DBs from SQL text dumps

python runtime/agent.py backup restore --force

```



---



## 🧪 Verification & Health Check



Run the comprehensive test suite and linters:



```powershell

# System doctor check

python runtime/agent.py doctor



# Lint all skills and rules

python runtime/agent.py skill lint



# Run all 15 unit tests

python -m unittest discover -s tests -v

```



---



## 📂 Project Structure



```text

Google_Antigravity_Agent/

├── .agents/

│   ├── hooks.json              # Antigravity lifecycle pre-tool guards & session alerts

│   ├── rules/                  # Modular rules (00-core, 10-safety, 20-skills, etc.)

│   ├── sidecars/               # Project-local background heartbeat sidecar

│   └── skills/                 # Modular skills (with isolated envs and widgets)

├── backups/                    # Clean SQL text dumps (committed to GitHub)

├── data/                       # Private SQLite DBs & DPAPI secrets vault (git-ignored)

├── envs/                       # Isolated virtual environments (managed via uv)

├── runtime/                    # Minimalist Python kernel & Web UI server

│   ├── agent.py                # Unified CLI entrypoint

│   └── web/                    # Zero-dependency Web Dashboard & REST API

└── tests/                      # Automated unit test suite

```


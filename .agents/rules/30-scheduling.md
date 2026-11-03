---
trigger: model_decision
description: Instructions for managing recurring jobs, cron schedules, timers, desktop toast notifications, and unattended agent tasks.
---

# Scheduling & Automation Guide

The agent runtime features an internal deterministic scheduler driven by a 1-minute heartbeat sidecar.

## 1. Job Kinds & Cost Management

| Kind | Token Cost | Use Case | Example Payload |
| :--- | :--- | :--- | :--- |
| `notify` | 0 tokens | Reminders, alarms, desktop alerts | `agent.py job add tea-timer --kind notify --at +5m --title "Tea" --message "Steep complete"` |
| `script` | 0 tokens | Periodic data fetching, API syncs, backups, device checks | `agent.py job add weather-sync --kind script --cron "0 7 * * *" --skill weather --script main.py` |
| `core` | 0 tokens | Builtin runtime operations | `agent.py job add daily-backup --kind core --cron "30 3 * * *" --action backup` |
| `agent` | LLM tokens | Creative synthesis, multi-step problem solving | `agent.py job add news-digest --kind agent --cron "0 8 * * 1" --prompt "Synthesize weekly tech updates"` |

## 2. Schedule Specifications

- **One-off**: `--at "+15m"`, `--at "+2h"`, `--at "+1d"`, or ISO timestamp `--at "2026-11-03T09:00:00"`
- **Interval**: `--every 30` (runs every 30 minutes)
- **Cron**: Standard 5-field cron in local system time:
  - `"0 7 * * *"` (Daily at 7:00 AM)
  - `"0 9 * * 1-5"` (Weekdays at 9:00 AM)
  - `"*/15 * * * *"` (Every 15 minutes)

## 3. CLI Management Commands

```bash
# List all active scheduled jobs
python runtime/agent.py job list

# Inspect job details and past runs
python runtime/agent.py job show <job-id>

# Test-run a job immediately
python runtime/agent.py job run <job-id>

# Pause or resume
python runtime/agent.py job pause <job-id>
python runtime/agent.py job resume <job-id>

# Delete a job
python runtime/agent.py job rm <job-id>
```

## 4. Inbox & Notifications
- Results of background jobs and reminders are recorded in the persistent Inbox (`data/agent.db`).
- Review unread notifications: `python runtime/agent.py inbox list --unread`.
- Desktop toast notifications are automatically emitted via Windows notification manager.


---
name: macmini-monitor
description: >-
  Monitor Apple Mac Mini availability, uptime, ping, SSH reachability, system metrics, and alert on status changes.
---

# Mac Mini Availability & Health Monitor

Monitors the local Apple Mac Mini host (`192.168.1.100`, SSH alias `macmini`) for network reachability, SSH responsiveness, and system health.

## When to use
- Check whether the Mac Mini is online, asleep, or powered off.
- Retrieve system vitals (load average, memory usage %, disk capacity %, uptime).
- Track availability history and uptime percentage over 24 hours or custom timeframes.
- Automate periodic background health checks using the zero-token script scheduler.
- Detect state transitions (`offline` or `recovered`).

## Commands
Run from the workspace root (isolated execution handled by `runtime/agent.py run`):

```bash
# Check current reachability and vitals (records check to SQLite)
python runtime/agent.py run macmini-monitor main.py -- status

# Check status without saving to history
python runtime/agent.py run macmini-monitor main.py -- status --no-record

# Check status and return exit code 1 if down (ideal for scheduler alerts)
python runtime/agent.py run macmini-monitor main.py -- status --fail-if-down

# View recent check history (default: last 10 entries)
python runtime/agent.py run macmini-monitor main.py -- history --limit 20

# Calculate uptime percentage over a time window (e.g., past 24 hours or 72 hours)
python runtime/agent.py run macmini-monitor main.py -- uptime --hours 24

# Send Wake-on-LAN packet to wake host if sleeping
python runtime/agent.py run macmini-monitor main.py -- wake --mac 00:11:22:33:44:55
```

## Scheduling Background Monitoring (Zero LLM Tokens)
To automatically check the Mac Mini every 5 or 15 minutes without consuming LLM tokens:

```bash
python runtime/agent.py sched add --id macmini-check --kind script --every 10 --skill macmini-monitor --script main.py --args-json "[\"status\", \"--fail-if-down\"]" --notify on_error --description "Periodic Mac Mini reachability check"
```
When `notify: on_error` is set, a desktop toast notification and inbox entry are generated if the Mac Mini goes offline.

## Persistent Storage
- State database: `data/skills/macmini-monitor/state.db`
- Table `checks`: stores timestamp, ping latency, port 22 latency, SSH reachability, load averages, memory and disk percentages.

## Verification
```bash
python runtime/agent.py skill test macmini-monitor
python runtime/agent.py skill lint macmini-monitor
```

## Gotchas & Architecture Notes
- macOS sleeps aggressively if "Prevent computer from sleeping automatically when the display is off" is disabled in macOS Energy Saver settings. If unreachable, send a Wake-on-LAN magic packet or wake the machine.
- SSH connections use `-o ConnectTimeout=3` and `-o BatchMode=yes` so automated checks fail fast instead of hanging.

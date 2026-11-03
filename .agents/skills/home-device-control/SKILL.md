---
name: home-device-control
description: >-
  Manage smart home devices via Home Assistant REST API and control local network hosts (ping, ssh, Wake-on-LAN).
---

# Home & Local Device Control

Enables smart home automation and local device management from Antigravity.

## When to use
- Controlling lights, switches, scenes, or checking sensors via Home Assistant.
- Checking local network host availability (`ping`), waking hosts (`wol`), or executing remote tasks (`ssh`).
- Controlling the local Mac Mini environment (`macmini` host alias).

## Commands
Run from workspace root via the isolated runner:
```bash
# Check Home Assistant state
python runtime/agent.py run home-device-control main.py -- state light.living_room

# Call Home Assistant service
python runtime/agent.py run home-device-control main.py -- call light turn_on --data "{\"entity_id\": \"light.living_room\"}"

# Ping local host (e.g. Mac Mini)
python runtime/agent.py run home-device-control main.py -- ping 192.168.1.100

# Remote SSH command on Mac Mini
python runtime/agent.py run home-device-control main.py -- ssh macmini "sw_vers"

# Wake-on-LAN
python runtime/agent.py run home-device-control main.py -- wol 00:11:22:33:44:55
```

## Secrets Configuration
To enable Home Assistant, set credentials in the DPAPI store:
```bash
python runtime/agent.py secret set HASS_TOKEN
python runtime/agent.py secret set HASS_URL
```

#!/usr/bin/env bash
# Update script to run locally on the Mac Mini
set -euo pipefail

cd "$(dirname "$0")/.."

echo "==> Pulling latest changes..."
git pull origin main || echo "Tip: If pulling from GitHub fails, add Mac Mini's public key (~/.ssh/id_ed25519.pub) to GitHub Deploy Keys, or push from Windows via scripts/deploy_macmini.ps1"

echo "==> Verifying runtime health..."
python runtime/agent.py doctor

echo "==> Antigravity Agent is up to date!"

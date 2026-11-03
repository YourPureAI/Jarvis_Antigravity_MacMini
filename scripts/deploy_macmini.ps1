<#
.SYNOPSIS
Deploys and updates the Antigravity Agent project on the local Mac Mini.

.DESCRIPTION
Pushes the latest commits on branch 'main' to the Mac Mini repository via Git over SSH,
and verifies runtime health on the remote host.
#>

[CmdletBinding()]
param(
    [switch]$RunTests
)

$ErrorActionPreference = "Stop"

Write-Host "==> Checking local Git status..." -ForegroundColor Cyan
$status = git status --porcelain
if ($status) {
    Write-Warning "You have uncommitted changes in your local workspace:"
    Write-Host $status
    Write-Warning "Only committed changes will be deployed."
}

Write-Host "==> Preparing Mac Mini working tree..." -ForegroundColor Cyan
ssh macmini "cd ~/Google_Antigravity_Agent && git config core.filemode false && git checkout -q -- . 2>/dev/null || true"

Write-Host "==> Pushing commits to Mac Mini (macmini:~/Google_Antigravity_Agent)..." -ForegroundColor Cyan
git push macmini main

Write-Host "==> Verifying runtime health on Mac Mini..." -ForegroundColor Cyan
ssh macmini "cd ~/Google_Antigravity_Agent && python runtime/agent.py doctor"

if ($RunTests) {
    Write-Host "==> Running full test suite on Mac Mini..." -ForegroundColor Cyan
    ssh macmini "cd ~/Google_Antigravity_Agent && python -m unittest discover -s tests"
}

Write-Host "==> Mac Mini deployment complete and healthy!" -ForegroundColor Green

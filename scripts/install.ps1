# Creates .venv with the locked dependency set and builds the dashboard.
$ErrorActionPreference='Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
function Check-Exit { if ($LASTEXITCODE -ne 0) { throw "Command failed with exit code $LASTEXITCODE" } }
if (!(Test-Path '.venv\Scripts\python.exe')) { py -3.12 -m venv .venv; Check-Exit }
& '.venv\Scripts\python.exe' -m pip install -r requirements-lock.txt; Check-Exit
& '.venv\Scripts\python.exe' -m pip install --no-deps -e .; Check-Exit
if (Get-Command npm -ErrorAction SilentlyContinue) {
    Push-Location frontend
    try { npm ci; Check-Exit; npm run build; Check-Exit } finally { Pop-Location }
} else {
    Write-Host 'npm not found: using the included frontend\dist build.'
}
Write-Host 'Installed. Start with: .\scripts\start.ps1  (Demo: .\scripts\start.ps1 -Demo)'

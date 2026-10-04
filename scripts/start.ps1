param([switch]$Demo,[switch]$NoBrowser)
$ErrorActionPreference='Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
$agentArgs=@('-m','ac_agent')
if ($Demo) { $agentArgs+='--demo' } else { $agentArgs+='--ac' }
if ($NoBrowser) { $agentArgs+='--no-browser' }
& '.venv\Scripts\python.exe' @agentArgs
exit $LASTEXITCODE

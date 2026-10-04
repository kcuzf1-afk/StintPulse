# Builds the StintPulse release: tests, EXE folder, portable ZIP and installer.
# Output in dist\: StintPulse\ (EXE folder), StintPulse-<version>-Windows.zip,
# StintPulse-<version>-Setup.exe (needs Inno Setup 6) and SHA256SUMS.txt.
param([switch]$NoInstaller, [switch]$SkipTests)
$ErrorActionPreference='Stop'
$projectRoot=Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot
if ($env:OS -ne 'Windows_NT') { throw 'PyInstaller must build the Windows EXE on Windows.' }
function Check-Exit { if ($LASTEXITCODE -ne 0) { throw "Command failed with exit code $LASTEXITCODE" } }
if (!(Test-Path '.venv\Scripts\python.exe')) { & '.\scripts\install.ps1' }
$python='.venv\Scripts\python.exe'
$version=(& $python -c "import sys; sys.path.insert(0, 'backend'); import ac_agent; print(ac_agent.__version__)").Trim()
Check-Exit
Write-Host "StintPulse $version"
Push-Location frontend
try {
    if (!(Test-Path 'node_modules')) { npm ci; Check-Exit }  # fresh checkout (CI)
    if (!$SkipTests) { npm test; Check-Exit }
    npm run build; Check-Exit
} finally { Pop-Location }
if (!$SkipTests) { & $python -m pytest -q; Check-Exit }
& $python scripts\collect_licenses.py
Check-Exit
& $python -m PyInstaller --noconfirm --clean packaging\stintpulse.spec
Check-Exit
$executable=Join-Path $projectRoot 'dist\StintPulse\StintPulse.exe'
& $python scripts\smoke_executable.py $executable
Check-Exit
$zip="dist\StintPulse-$version-Windows.zip"
Compress-Archive -Path 'dist\StintPulse' -DestinationPath $zip -Force
$artifacts=@($zip)
if (!$NoInstaller) {
    $compiler=(Get-Command ISCC.exe -ErrorAction SilentlyContinue).Source
    if (!$compiler) {
        $compiler=@("$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe", "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe") |
            Where-Object { Test-Path $_ } | Select-Object -First 1
    }
    if (!$compiler) { throw 'Inno Setup 6 not found (winget install JRSoftware.InnoSetup), or build with -NoInstaller.' }
    & $compiler "/DAppVersion=$version" 'packaging\installer.iss'
    Check-Exit
    $artifacts+="dist\StintPulse-$version-Setup.exe"
}
# Checksums for the download page / GitHub release.
$artifacts | ForEach-Object { "{0}  {1}" -f (Get-FileHash $_ -Algorithm SHA256).Hash.ToLower(), (Split-Path $_ -Leaf) } |
    Set-Content -Encoding ascii 'dist\SHA256SUMS.txt'
Write-Host 'Release files in dist:'
Get-ChildItem dist -File | ForEach-Object { Write-Host ("  {0} ({1:N1} MB)" -f $_.Name, ($_.Length / 1MB)) }

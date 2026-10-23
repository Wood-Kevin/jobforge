# Release build: frozen agent (PyInstaller onedir, console) -> layout check -> Tauri NSIS installer.
# Usage (from repo root): powershell -NoProfile -ExecutionPolicy Bypass -File scripts\build.ps1
# Output: dist-installers\JobForge_<version>_setup.exe
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$env:Path = [Environment]::GetEnvironmentVariable('Path', 'User') + ';' + [Environment]::GetEnvironmentVariable('Path', 'Machine')

# 0. One version for agent and host.
$agentVersion = (Select-String -Path "$root\agent\src\jobforge_agent\__init__.py" -Pattern '__version__ = "([^"]+)"').Matches[0].Groups[1].Value
$hostVersion = (Get-Content "$root\desktop\src-tauri\tauri.conf.json" -Raw | ConvertFrom-Json).version
if ($agentVersion -ne $hostVersion) { throw "version mismatch: agent $agentVersion, host $hostVersion" }
Write-Output "building JobForge $hostVersion"

# 1. Frozen agent. --console on purpose: the windowed bootloader reports startup failures in a
#    modal dialog that hangs a background agent (spike P1.0d defect 1).
Push-Location "$root\agent"
try {
  if (Test-Path dist) { Remove-Item -Recurse -Force dist }
  uv run pyinstaller --noconfirm --clean --onedir --console --name jobforge-agent --collect-data jobforge_agent `
    --distpath dist --workpath build\pyi --specpath build packaging\entry.py
  if ($LASTEXITCODE -ne 0) { throw "pyinstaller failed" }
} finally { Pop-Location }

# 2. Layout check (spike P1.0d defect 2: a resource glob flattened _internal and the agent could not start).
$agentDir = "$root\agent\dist\jobforge-agent"
$required = @("jobforge-agent.exe", "_internal\jobforge_agent\db\migrations\0001_init.sql",
              "_internal\jobforge_agent\evaluation\verdict_rules\v1.yaml")  # P1.7: E5/E6 thresholds are package data
foreach ($r in $required) { if (-not (Test-Path (Join-Path $agentDir $r))) { throw "agent layout check failed: missing $r" } }
if (-not (Get-ChildItem "$agentDir\_internal" -Filter 'python3*.dll')) { throw "agent layout check failed: no python3*.dll in _internal" }
$v = & "$agentDir\jobforge-agent.exe" --version
if ($v -ne $agentVersion) { throw "frozen agent reports version '$v', expected $agentVersion" }
Write-Output "agent layout ok ($((Get-ChildItem $agentDir -Recurse -File).Count) files)"

# 3. Host + installer.
Push-Location "$root\desktop"
try {
  if (-not (Test-Path node_modules)) { npm ci }
  npx tauri build
  if ($LASTEXITCODE -ne 0) { throw "tauri build failed" }
} finally { Pop-Location }

$installer = Get-ChildItem "$root\desktop\src-tauri\target\release\bundle\nsis\*.exe" | Sort-Object LastWriteTime | Select-Object -Last 1
New-Item -ItemType Directory -Force "$root\dist-installers" | Out-Null
$dest = "$root\dist-installers\JobForge_${hostVersion}_setup.exe"
Copy-Item $installer.FullName $dest -Force
Get-Item $dest | Select-Object Name, @{n = 'MB'; e = { [math]::Round($_.Length / 1MB, 1) } }

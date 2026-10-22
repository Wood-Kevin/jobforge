# P1.0d packaging spike build: agent (PyInstaller onedir) -> Tauri NSIS installer.
# Usage: powershell -File build.ps1 -Version 0.1.0 -Schema 1
param(
  [Parameter(Mandatory = $true)][string]$Version,
  [Parameter(Mandatory = $true)][int]$Schema
)
$ErrorActionPreference = 'Stop'
$here = $PSScriptRoot
Set-Location $here

# 1. Python build env, isolated from the user's global packages
if (-not (Test-Path "$here\.venv\Scripts\python.exe")) {
  python -m venv "$here\.venv"
  & "$here\.venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
  & "$here\.venv\Scripts\python.exe" -m pip install --quiet pyinstaller==6.22.3 fastapi==0.159.1 uvicorn==0.54.0
}

# Windows PowerShell 5.1's "-Encoding utf8" writes a BOM, which Tauri's JSON parser rejects.
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)

# 2. Agent with version baked in
[IO.File]::WriteAllText("$here\agent\build_info.py", "VERSION = '$Version'`nSCHEMA_VERSION = $Schema`n", $utf8NoBom)
if (Test-Path "$here\agent-dist") { Remove-Item -Recurse -Force "$here\agent-dist" }
# --console, not --noconsole: the windowed bootloader reports startup failures in a modal
# message box, which hangs a detached agent forever. The host hides the console instead.
& "$here\.venv\Scripts\pyinstaller.exe" --noconfirm --clean --onedir --console --name jobforge-agent `
  --distpath "$here\agent-dist" --workpath "$here\build\pyi" --specpath "$here\build" `
  --paths "$here\agent" "$here\agent\agent.py"
if ($LASTEXITCODE -ne 0) { throw "pyinstaller failed" }

# 3. Host version follows the agent version
$conf = "$here\host\src-tauri\tauri.conf.json"
[IO.File]::WriteAllText($conf, ((Get-Content $conf -Raw) -replace '"version": "[^"]+"', "`"version`": `"$Version`""), $utf8NoBom)

# 4. Tauri build (NSIS)
Set-Location "$here\host"
if (-not (Test-Path node_modules)) { npm install --silent }
npx tauri build
if ($LASTEXITCODE -ne 0) { throw "tauri build failed" }
$out = Get-ChildItem "$here\host\src-tauri\target\release\bundle\nsis\*.exe" | Sort-Object LastWriteTime | Select-Object -Last 1
New-Item -ItemType Directory -Force "$here\installers" | Out-Null
Copy-Item $out.FullName "$here\installers\JobForgeSpike_${Version}_setup.exe" -Force
Get-Item "$here\installers\JobForgeSpike_${Version}_setup.exe" | Select-Object Name, Length

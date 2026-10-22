# P1.2 acceptance against an installed JobForge (ARCHITECTURE 9, P1.2 row). Prints raw evidence, no verdicts.
# Processes under test start UNELEVATED through explorer.exe; this shell may be elevated (Kevin's is).
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File scripts\test_desktop.ps1 -Installer dist-installers\JobForge_0.1.0_setup.exe
param([Parameter(Mandatory = $true)][string]$Installer)
$ErrorActionPreference = 'Continue'
$data = Join-Path $env:LOCALAPPDATA 'dev.kevin.jobforge'
$runKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
$runKeyLM = 'HKLM:\Software\Microsoft\Windows\CurrentVersion\Run'
$sqlite = Join-Path (Split-Path $PSScriptRoot -Parent) 'agent\.venv\Scripts\python.exe'

Add-Type -ReferencedAssemblies System.Drawing -TypeDefinition @'
using System; using System.Drawing; using System.Drawing.Imaging; using System.Runtime.InteropServices;
public static class Shot {
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L, T, R, B; }
  [DllImport("user32.dll")] static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] static extern bool PrintWindow(IntPtr h, IntPtr hdc, uint flags);
  public static string Save(IntPtr h, string path) {
    RECT r; GetWindowRect(h, out r);
    int w = r.R - r.L, ht = r.B - r.T;
    using (var bmp = new Bitmap(w, ht)) using (var g = Graphics.FromImage(bmp)) {
      IntPtr hdc = g.GetHdc(); bool ok = PrintWindow(h, hdc, 2); g.ReleaseHdc(hdc);  // 2 = PW_RENDERFULLCONTENT (WebView2)
      bmp.Save(path, ImageFormat.Png); return ok + " " + w + "x" + ht;
    }
  }
}
'@
function Say($m) { Write-Output ("[{0:HH:mm:ss}] {1}" -f (Get-Date), $m) }
function Unelev([string]$exe, [string]$argline = '', [switch]$Wait, [int]$TimeoutSec = 180) {
  $id = [guid]::NewGuid().ToString('N').Substring(0, 8)
  $marker = Join-Path $env:TEMP "jf_t_$id.txt"; $cmd = Join-Path $env:TEMP "jf_t_$id.cmd"
  if ($Wait) { Set-Content $cmd "@`"$exe`" $argline`r`n@echo %ERRORLEVEL% > `"$marker`"" -Encoding ascii }
  else { Set-Content $cmd "@start `"`" `"$exe`" $argline" -Encoding ascii }
  explorer.exe $cmd
  if (-not $Wait) { return }
  $d = (Get-Date).AddSeconds($TimeoutSec)
  while ((Get-Date) -lt $d -and -not (Test-Path $marker)) { Start-Sleep -Milliseconds 300 }
  if (Test-Path $marker) { (Get-Content $marker -Raw).Trim() } else { 'timeout' }
}
function InstallDir {
  $k = Get-ChildItem HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall -ErrorAction SilentlyContinue |
    Get-ItemProperty | Where-Object { $_.DisplayName -eq 'JobForge' } | Select-Object -First 1
  if ($k) { Split-Path ($k.UninstallString -replace '"', '') -Parent }
}
function HostExe { Join-Path (InstallDir) 'JobForge.exe' }
function Procs {
  Get-Process -Name JobForge, jobforge-agent -ErrorAction SilentlyContinue |
    Select-Object Name, Id, @{n = 'Started'; e = { $_.StartTime.ToString('HH:mm:ss') } }, MainWindowTitle | Format-Table -AutoSize | Out-String -Width 200
}
function Ipc { try { Get-Content "$data\run\ipc.json" -Raw -ErrorAction Stop | ConvertFrom-Json } catch { $null } }
function Call($method, $path) {
  $i = Ipc; if (-not $i) { return 'no ipc.json' }
  try { (Invoke-RestMethod -Method $method -Uri "http://127.0.0.1:$($i.port)$path" -Headers @{ Authorization = "Bearer $($i.token)" } -TimeoutSec 3) | ConvertTo-Json -Compress }
  catch { "failed: $($_.Exception.Message)" }
}
function Health { Call GET '/v1/health' }
function WaitFor([scriptblock]$cond, [int]$sec) { $d = (Get-Date).AddSeconds($sec); while ((Get-Date) -lt $d) { if (& $cond) { return $true }; Start-Sleep -Milliseconds 500 }; $false }
function Sql($q) { & $sqlite -I -c "import sqlite3,json;c=sqlite3.connect(r'file:$data\data\jobforge.db?mode=ro',uri=True);[print(tuple(r)) for r in c.execute('''$q''')]" }
function HostLog($n = 6) { Get-Content "$data\logs\host.log" -Tail $n -ErrorAction SilentlyContinue }
function AgentLog($pattern) { Select-String -Path "$data\logs\agent.jsonl" -Pattern $pattern -ErrorAction SilentlyContinue | Select-Object -Last 3 | ForEach-Object { $_.Line } }
function RunEntries {
  foreach ($k in $runKey, $runKeyLM) {
    $hit = (Get-ItemProperty $k -ErrorAction SilentlyContinue).PSObject.Properties | Where-Object { "$($_.Value)" -like '*JobForge*' }
    if ($hit) { $hit | ForEach-Object { Say "  $k :: $($_.Name) = $($_.Value)" } } else { Say "  $k :: (no JobForge entry)" }
  }
}

# ---- 0. Preconditions: never touch real user data
$elevatedShell = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
Say "test shell elevated: $elevatedShell"
$createdByTest = -not (Test-Path $data)
if (-not $createdByTest -and (Test-Path "$data\data\jobforge.db")) {
  $rows = & $sqlite -I -c "import sqlite3;c=sqlite3.connect(r'file:$data\data\jobforge.db?mode=ro',uri=True);print(sum(c.execute(f'select count(*) from {t}').fetchone()[0] for t in ('experience_items','applications','resumes','search_profiles')))"
  if ([int]$rows -gt 0) { throw "data root holds user data ($rows rows); refusing to run lifecycle tests against it" }
}
Say "data root existed before test: $(-not $createdByTest)"
if (Get-Process JobForge, jobforge-agent -ErrorAction SilentlyContinue) { throw "JobForge is running; exit it first" }

# ---- 1. Install
$t0 = Get-Date
Say "T1 silent per-user install, unelevated: $Installer"
Say ("installer exit {0} in {1:N1} s" -f (Unelev (Resolve-Path $Installer).Path '/S' -Wait), ((Get-Date) - $t0).TotalSeconds)
$dir = InstallDir; Say "install dir: $dir"
Get-ChildItem $dir | Select-Object Name, Length | Format-Table | Out-String | Write-Output
Say ("install MB {0:N1}; agent files {1}" -f ((Get-ChildItem $dir -Recurse -File | Measure-Object Length -Sum).Sum / 1MB), (Get-ChildItem "$dir\agent" -Recurse -File).Count)
$installedAt = (Get-Item "$dir\uninstall.exe").LastWriteTime
Say "autostart before any action:"; RunEntries

# ---- 2. Launch (window), attach-or-spawn
Say "T2 launch JobForge unelevated (window)"
Unelev (HostExe)
WaitFor { (Health) -like '*"pid"*' } 40 | Out-Null; Start-Sleep 3
Procs; Say ("health: " + (Health)); HostLog 4
AgentLog 'agent_start'

# ---- 3. Close window: tray stays, agent unaffected
Say "T3 close the window"
$agentPid = (Ipc).pid
$h = Get-Process JobForge | Where-Object { $_.MainWindowTitle -eq 'JobForge' } | Select-Object -First 1
Say ("window before: '{0}' pid {1}" -f $h.MainWindowTitle, $h.Id)
$h.CloseMainWindow() | Out-Null; Start-Sleep 8
Procs; Say "agent pid before close: $agentPid"; Say ("health after close: " + (Health))

# ---- 4. Pause persists across an agent crash; watchdog respawns
Say "T4 pause (same route as the tray item), then kill the agent"
Say (Call POST '/v1/discovery/pause')
$t0 = Get-Date
Stop-Process -Id $agentPid -Force
$ok = WaitFor { $hh = Health; ($hh -like '*"pid"*') -and ($hh -notlike "*`"pid`":$agentPid,*") } 60
Say ("respawned: {0} after {1:N1} s" -f $ok, ((Get-Date) - $t0).TotalSeconds)
Say ("health after respawn (expect discovery_paused true): " + (Health))
Say (Call POST '/v1/discovery/resume')
Sql "select id, pid, stopped_at_ms is not null, stop_reason from agent_runs order by id desc limit 3"
HostLog 5

# ---- 5. Second launch focuses the existing instance
Say "T5 second launch (no args) -> one host process, window shown"
Unelev (HostExe); Start-Sleep 6
Procs
Say "T5b live event in the open window: pause, wait, capture the window"
Say (Call POST '/v1/discovery/pause'); Start-Sleep 4
$w = Get-Process JobForge | Where-Object { $_.MainWindowTitle -eq 'JobForge' } | Select-Object -First 1
$shot = Join-Path (Split-Path $Installer -Parent) 'window_p1_2.png'
if ($w) { Say ("screenshot " + [Shot]::Save($w.MainWindowHandle, $shot) + " -> $shot") } else { Say "no JobForge window found" }
Say (Call POST '/v1/discovery/resume')

# ---- 6. Host crash: agent survives; relaunch attaches
Say "T6 kill the host"
$agentPid = (Ipc).pid
Stop-Process -Name JobForge -Force; Start-Sleep 6
Procs; Say ("health after host kill: " + (Health))
Unelev (HostExe) '--background'; Start-Sleep 8
Procs; HostLog 3
Say "agent pid unchanged: $agentPid -> $((Ipc).pid)"

# ---- 7. Autostart: HKCU only, and the registered command starts in the background
Say "T7 autostart on (forwarded to the running host)"
Say ("exit: " + (Unelev (HostExe) '--autostart on' -Wait -TimeoutSec 30)); Start-Sleep 2
RunEntries
$cmdline = ((Get-ItemProperty $runKey).PSObject.Properties | Where-Object Name -eq 'JobForge').Value
Say "simulate sign-in: exit JobForge, then run the registered command unelevated"
Unelev (HostExe) '--exit'; WaitFor { -not (Get-Process JobForge, jobforge-agent -ErrorAction SilentlyContinue) } 30 | Out-Null
if ($cmdline) {
  $exe = ($cmdline -split '" ')[0].Trim('"'); $rest = $cmdline.Substring($cmdline.IndexOf('" ') + 2)
  Unelev $exe $rest
  WaitFor { (Health) -like '*"pid"*' } 40 | Out-Null; Start-Sleep 2
  Procs; Say ("health: " + (Health))
}

# ---- 8. Elevated launch: relaunch unelevated (D16)
if ($elevatedShell) {
  Say "T8 start JobForge.exe directly from this ELEVATED shell"
  Unelev (HostExe) '--exit'; WaitFor { -not (Get-Process JobForge, jobforge-agent -ErrorAction SilentlyContinue) } 30 | Out-Null
  Start-Process (HostExe); Start-Sleep 12
  Procs; HostLog 4
  Say ("health (expect elevated false): " + (Health))
} else { Say "T8 skipped: test shell is not elevated" }

# ---- 9. Database newer than the agent: watchdog stops, does not churn
Say "T9 database newer than this version"
Unelev (HostExe) '--exit'; WaitFor { -not (Get-Process JobForge, jobforge-agent -ErrorAction SilentlyContinue) } 30 | Out-Null
# Copy through SQLite (backup API), never as a raw file: a raw copy misses rows still in the WAL.
& $sqlite -I -c "import sqlite3;s=sqlite3.connect(r'$data\data\jobforge.db');d=sqlite3.connect(r'$env:TEMP\jf_t_db_backup.db');s.backup(d);d.close();s.close()"
& $sqlite -I -c "import sqlite3;c=sqlite3.connect(r'$data\data\jobforge.db');c.execute('PRAGMA user_version=99');c.close()"
$spawnsBefore = (Select-String -Path "$data\logs\host.log" -Pattern 'spawned agent' -ErrorAction SilentlyContinue).Count
Unelev (HostExe) '--background'; Start-Sleep 30
$spawnsAfter = (Select-String -Path "$data\logs\host.log" -Pattern 'spawned agent').Count
Procs; HostLog 5
Say "agent spawns during 30 s: $($spawnsAfter - $spawnsBefore) (one expected, no churn)"
AgentLog 'db_newer_than_agent'
Unelev (HostExe) '--exit'; WaitFor { -not (Get-Process JobForge -ErrorAction SilentlyContinue) } 30 | Out-Null
Remove-Item "$data\data\jobforge.db-wal", "$data\data\jobforge.db-shm" -ErrorAction SilentlyContinue
& $sqlite -I -c "import sqlite3;s=sqlite3.connect(r'$env:TEMP\jf_t_db_backup.db');d=sqlite3.connect(r'$data\data\jobforge.db');s.backup(d);d.close();s.close()"
Remove-Item "$env:TEMP\jf_t_db_backup.db"

# ---- 10. Exit
Say "T10 start, then --exit (same function as tray Exit)"
Unelev (HostExe) '--background'; WaitFor { (Health) -like '*"pid"*' } 40 | Out-Null
$t0 = Get-Date
Unelev (HostExe) '--exit'
$gone = WaitFor { -not (Get-Process JobForge, jobforge-agent -ErrorAction SilentlyContinue) } 30
Say ("both gone: {0} after {1:N1} s" -f $gone, ((Get-Date) - $t0).TotalSeconds)
# Checked before any probe: a read-only open of a WAL database leaves an empty -wal file itself.
Say ("WAL file left after clean exit: " + (Test-Path "$data\data\jobforge.db-wal"))
Sql "select id, stop_reason, elevated from agent_runs order by id desc limit 3"

# ---- 11. Install dir untouched at runtime
Say "T11 files under the install dir changed after install (expect none):"
Get-ChildItem $dir -Recurse -File | Where-Object { $_.LastWriteTime -gt $installedAt.AddSeconds(5) } | Select-Object FullName | Format-Table | Out-String -Width 200 | Write-Output

# ---- 12. Uninstall with the agent running and autostart on
Say "T12 uninstall while running"
Unelev (HostExe) '--background'; WaitFor { (Health) -like '*"pid"*' } 40 | Out-Null
RunEntries
Say ("uninstaller exit " + (Unelev (Join-Path $dir 'uninstall.exe') '/S' -Wait -TimeoutSec 120)); Start-Sleep 5
Procs
Say ("install dir exists: " + (Test-Path $dir) + "; data root exists: " + (Test-Path $data))
RunEntries
AgentLog '"agent_stop"'

if ($createdByTest) { Remove-Item $data -Recurse -Force -ErrorAction SilentlyContinue; Say "removed test-created data root" }
Remove-Item "$env:TEMP\jf_t_*" -ErrorAction SilentlyContinue

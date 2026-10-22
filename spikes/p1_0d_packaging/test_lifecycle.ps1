# P1.0d lifecycle tests. Prints raw evidence (PIDs, rows, log lines, registry), no verdicts.
# Processes under test are started UNELEVATED via explorer.exe (this shell may be elevated);
# inspection and kills run from this shell.
# Usage: powershell -File test_lifecycle.ps1 -Phase <install|lifecycle|update|rollback|uninstall> [-Installer path] [-Restore path]
param(
  [Parameter(Mandatory = $true)][ValidateSet('install', 'lifecycle', 'update', 'exit', 'rollback', 'uninstall')][string]$Phase,
  [string]$Installer,
  [string]$Restore
)
$ErrorActionPreference = 'Continue'
$data = Join-Path $env:LOCALAPPDATA 'JobForgeSpikeData'
$py = 'C:\Users\kevin\Desktop\claude-projects\job-search-app\spikes\p1_0d_packaging\.venv\Scripts\python.exe'
$runKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
$runKeyLM = 'HKLM:\Software\Microsoft\Windows\CurrentVersion\Run'

function Say($m) { Write-Output ("[{0:HH:mm:ss}] {1}" -f (Get-Date), $m) }

# Start a process at medium integrity through explorer.exe. With -Wait, block until it exits and return its exit code.
function Unelev([string]$exe, [string]$argline = '', [switch]$Wait, [int]$TimeoutSec = 120) {
  $id = [guid]::NewGuid().ToString('N').Substring(0, 8)
  $marker = Join-Path $env:TEMP "jf_unelev_$id.txt"
  $cmd = Join-Path $env:TEMP "jf_unelev_$id.cmd"
  if ($Wait) {
    Set-Content $cmd "@`"$exe`" $argline`r`n@echo %ERRORLEVEL% > `"$marker`"" -Encoding ascii
  } else {
    Set-Content $cmd "@start `"`" `"$exe`" $argline" -Encoding ascii
  }
  explorer.exe $cmd
  if (-not $Wait) { return }
  $d = (Get-Date).AddSeconds($TimeoutSec)
  while ((Get-Date) -lt $d -and -not (Test-Path $marker)) { Start-Sleep -Milliseconds 300 }
  if (Test-Path $marker) { return (Get-Content $marker -Raw).Trim() } else { return 'timeout' }
}
function InstallDir {
  $k = Get-ChildItem HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall -ErrorAction SilentlyContinue |
    Get-ItemProperty | Where-Object { $_.DisplayName -like 'JobForgeSpike*' } | Select-Object -First 1
  if ($k) { return Split-Path ($k.UninstallString -replace '"', '') -Parent }
}
function HostExe { Join-Path (InstallDir) 'jobforge-spike-host.exe' }
function AgentExe { Join-Path (InstallDir) 'agent\jobforge-agent.exe' }
function Procs {
  Get-Process -Name jobforge-spike-host, jobforge-agent -ErrorAction SilentlyContinue |
    Select-Object Name, Id, @{n = 'Started'; e = { $_.StartTime.ToString('HH:mm:ss') } }, MainWindowTitle | Format-Table -AutoSize | Out-String -Width 220
}
function Ipc { try { Get-Content "$data\run\ipc.json" -Raw -ErrorAction Stop | ConvertFrom-Json } catch { $null } }
function Health {
  $i = Ipc; if (-not $i) { return 'health failed: no ipc.json' }
  try { (Invoke-RestMethod -Uri "http://127.0.0.1:$($i.port)/v1/health" -Headers @{ Authorization = "Bearer $($i.token)" } -TimeoutSec 3) | ConvertTo-Json -Compress }
  catch { "health failed: $($_.Exception.Message)" }
}
function Post($path) {
  $i = Ipc
  try { (Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$($i.port)$path" -Headers @{ Authorization = "Bearer $($i.token)" } -TimeoutSec 3) | ConvertTo-Json -Compress }
  catch { "post failed: $($_.Exception.Message)" }
}
function Runs {
  & $py -I -c "import sqlite3;c=sqlite3.connect(r'file:$data\data\jobforge.db?mode=ro',uri=True);print('user_version',c.execute('PRAGMA user_version').fetchone()[0]);[print(r) for r in c.execute('select id,pid,version,started_at_ms,stopped_at_ms,stop_reason from agent_runs order by id desc limit 8')];print('ticks per run',c.execute('select run_id,count(*) from ticks group by run_id order by run_id desc limit 8').fetchall())"
}
function AgentLog($n = 12) { Get-Content "$data\logs\agent.jsonl" -Tail $n -ErrorAction SilentlyContinue }
function HostLog($n = 8) { Get-Content "$data\logs\host.log" -Tail $n -ErrorAction SilentlyContinue }
function WaitFor([scriptblock]$cond, [int]$sec) { $d = (Get-Date).AddSeconds($sec); while ((Get-Date) -lt $d) { if (& $cond) { return $true }; Start-Sleep -Milliseconds 500 }; return $false }
function RunEntries {
  foreach ($k in $runKey, $runKeyLM) {
    $p = Get-ItemProperty $k -ErrorAction SilentlyContinue
    $hits = $p.PSObject.Properties | Where-Object { "$($_.Value)" -like '*jobforge*' }
    if ($hits) { $hits | ForEach-Object { Say "  $k :: $($_.Name) = $($_.Value)" } } else { Say "  $k :: (no JobForge entry)" }
  }
}

switch ($Phase) {
  'install' {
    $t0 = Get-Date
    Say "silent per-user install, unelevated: $Installer"
    $code = Unelev (Resolve-Path $Installer).Path '/S' -Wait
    Say ("installer exit {0} in {1:N1} s" -f $code, ((Get-Date) - $t0).TotalSeconds)
    $dir = InstallDir
    Say "install dir: $dir"
    Get-ChildItem $dir | Select-Object Name, Length | Format-Table | Out-String | Write-Output
    Say ("install dir size MB: {0:N1}; agent files: {1}" -f ((Get-ChildItem $dir -Recurse -File | Measure-Object Length -Sum).Sum / 1MB), (Get-ChildItem "$dir\agent" -Recurse -File).Count)
    Say ("python dll: " + ((Get-ChildItem "$dir\agent" -Recurse -Filter python3*.dll | Select-Object -First 1).FullName))
    Say "autostart entries before any action:"; RunEntries
  }
  'lifecycle' {
    $installedAt = (Get-Item (Join-Path (InstallDir) 'uninstall.exe')).LastWriteTime
    Say "T1 launch host unelevated (window mode)"
    Unelev (HostExe)
    WaitFor { (Health) -notlike 'health failed*' } 30 | Out-Null
    Start-Sleep 4
    Procs; Say ("health: " + (Health)); HostLog 4
    Say "agent elevation (from its own log):"; AgentLog 3 | Select-String agent_start

    Say "T2 close the window; host stays in tray, agent unaffected"
    $agentPid = (Get-Process jobforge-agent).Id
    $hostProc = Get-Process jobforge-spike-host
    Say ("host main window: '{0}' handle {1}" -f $hostProc.MainWindowTitle, $hostProc.MainWindowHandle)
    $hostProc.CloseMainWindow() | Out-Null
    Start-Sleep 12
    $hostProc.Refresh()
    Say ("host still running: {0}; main window now: '{1}' handle {2}" -f (-not $hostProc.HasExited), $hostProc.MainWindowTitle, $hostProc.MainWindowHandle)
    Procs; Say "agent pid before close: $agentPid"; Say ("health: " + (Health))

    Say "T3 pause via IPC (same route as the tray item); ticks must stop"
    Say (Post '/v1/pause')
    $a = Health; Start-Sleep 12; $b = Health
    Say "health 12 s apart while paused:"; $a; $b

    Say "T4 IPC rejects missing token and foreign Host; loopback only; run dir ACL"
    $i = Ipc
    try { Invoke-WebRequest -Uri "http://127.0.0.1:$($i.port)/v1/health" -UseBasicParsing -TimeoutSec 3 | Out-Null } catch { Say ("no token -> " + $_.Exception.Response.StatusCode.value__) }
    try { Invoke-WebRequest -Uri "http://127.0.0.1:$($i.port)/v1/health" -Headers @{ Authorization = "Bearer $($i.token)"; Host = 'evil.example' } -UseBasicParsing -TimeoutSec 3 | Out-Null } catch { Say ("foreign Host -> " + $_.Exception.Response.StatusCode.value__) }
    Get-NetTCPConnection -OwningProcess $agentPid -State Listen | Select-Object LocalAddress, LocalPort | Format-Table | Out-String | Write-Output
    icacls "$data\run"

    Say "T5 second agent instance must refuse (exit 3)"
    Say ("second agent exit code: " + (Unelev (AgentExe) '' -Wait -TimeoutSec 30))

    Say "T6 host crash: kill host; agent must keep running; relaunch must attach, not spawn"
    Stop-Process -Name jobforge-spike-host -Force
    Start-Sleep 8
    Procs; Say ("health after host kill: " + (Health))
    Unelev (HostExe) '--background'
    Start-Sleep 10
    Procs; HostLog 4

    Say "T7 agent crash while paused: watchdog respawns; pause persists; previous run marked crash_recovered"
    $old = (Get-Process jobforge-agent).Id
    Stop-Process -Name jobforge-agent -Force
    $t0 = Get-Date
    $ok = WaitFor { ((Health) -notlike 'health failed*') -and ((Get-Process jobforge-agent -ErrorAction SilentlyContinue).Id -ne $old) } 60
    Say ("agent answered again: {0} after {1:N1} s" -f $ok, ((Get-Date) - $t0).TotalSeconds)
    Say ("health after respawn (expect paused true): " + (Health))
    Say (Post '/v1/resume')
    Start-Sleep 11
    Say ("health after resume: " + (Health))
    Procs; Runs; HostLog 6

    Say "T8 autostart through the plugin, unelevated (host must not be running for the CLI hook)"
    Stop-Process -Name jobforge-spike-host -Force; Start-Sleep 2
    Say ("enable exit: " + (Unelev (HostExe) '--autostart on' -Wait -TimeoutSec 30))
    RunEntries
    $cmdline = ((Get-ItemProperty $runKey -ErrorAction SilentlyContinue).PSObject.Properties | Where-Object { "$($_.Value)" -like '*jobforge*' } | Select-Object -First 1).Value
    if ($cmdline) {
      Say "simulate sign-in by running the registered command line unelevated: $cmdline"
      $exe = ($cmdline -split ' --')[0].Trim('"'); $rest = $cmdline.Substring($cmdline.IndexOf(' --') + 1)
      Unelev $exe $rest
      Start-Sleep 10
      Procs; HostLog 2
      Stop-Process -Name jobforge-spike-host -Force; Start-Sleep 2
    }
    Say ("disable exit: " + (Unelev (HostExe) '--autostart off' -Wait -TimeoutSec 30))
    RunEntries

    Say "T9 relaunch host so the update test starts with host + agent running"
    Unelev (HostExe) '--background'; Start-Sleep 8
    Procs; Say ("health: " + (Health))

    Say "T10 files under the install dir modified after install (expect none):"
    Get-ChildItem (InstallDir) -Recurse -File | Where-Object { $_.LastWriteTime -gt $installedAt.AddSeconds(5) } | Select-Object FullName, LastWriteTime | Format-Table | Out-String -Width 220 | Write-Output
    Say "data dir tree:"; Get-ChildItem $data -Recurse -File | Select-Object FullName, Length | Format-Table | Out-String -Width 220 | Write-Output
  }
  'update' {
    Say "before update:"; Procs; Say ("health: " + (Health))
    $t0 = Get-Date
    Say "run the new installer silently, unelevated, while host + agent run: $Installer"
    $code = Unelev (Resolve-Path $Installer).Path '/S' -Wait -TimeoutSec 180
    Say ("installer exit {0} in {1:N1} s" -f $code, ((Get-Date) - $t0).TotalSeconds)
    Procs
    Say "agent log around the update:"; AgentLog 6
    Say "launch host after update"
    Unelev (HostExe) '--background'
    WaitFor { (Health) -like '*0.2.0*' } 40 | Out-Null
    Start-Sleep 4
    Procs; Say ("health: " + (Health)); Runs
    Say "backups:"; Get-ChildItem "$data\backups" -ErrorAction SilentlyContinue | Select-Object Name, Length | Format-Table | Out-String | Write-Output
    AgentLog 5
  }
  'exit' {
    Say "before exit:"; Procs; Say ("health: " + (Health))
    $t0 = Get-Date
    Say "second launch with --exit (same code path as tray 'Exit JobForge')"
    Unelev (HostExe) '--exit'
    $gone = WaitFor { -not (Get-Process jobforge-spike-host, jobforge-agent -ErrorAction SilentlyContinue) } 30
    Say ("both processes gone: {0} after {1:N1} s" -f $gone, ((Get-Date) - $t0).TotalSeconds)
    Procs; Runs; HostLog 3; AgentLog 2
    Say "lock released (a fresh agent can take it):"
    Say ("agent --status exit (no agent running, expect 1): " + (Unelev (AgentExe) '--status' -Wait -TimeoutSec 30))
  }
  'rollback' {
    Say "install the OLD installer over the new one, unelevated: $Installer"
    Say ("installer exit " + (Unelev (Resolve-Path $Installer).Path '/S' -Wait -TimeoutSec 180))
    Unelev (HostExe) '--background'
    Start-Sleep 30
    Procs; Say ("health: " + (Health)); Say "agent log (expect db_newer_than_agent):"; AgentLog 5; HostLog 8
    if ($Restore) {
      Say "restore pre-migration backup: $Restore"
      Stop-Process -Name jobforge-spike-host -Force -ErrorAction SilentlyContinue; Start-Sleep 2
      Say ("restore exit " + (Unelev (AgentExe) "--restore `"$Restore`"" -Wait -TimeoutSec 60))
      Unelev (HostExe) '--background'
      WaitFor { (Health) -notlike 'health failed*' } 30 | Out-Null
      Start-Sleep 4
      Procs; Say ("health: " + (Health)); Runs
    }
  }
  'uninstall' {
    $dir = InstallDir
    Say "before uninstall:"; Procs
    Say ("uninstaller exit " + (Unelev (Join-Path $dir 'uninstall.exe') '/S' -Wait -TimeoutSec 120))
    Start-Sleep 5
    Procs
    Say ("install dir exists: " + (Test-Path $dir)); Say ("data dir exists: " + (Test-Path $data))
    Say "autostart entries:"; RunEntries
    AgentLog 4
  }
}

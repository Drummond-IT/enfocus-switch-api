# Sets up the Drummond One PitStop helper on the PitStop Server machine (switch03).
# Run in an elevated PowerShell on switch03, from the folder holding this file and PitStopHelper.ps1.
#
#   Check only, changes nothing:   powershell -ExecutionPolicy Bypass -File .\Install-PitStopHelper.ps1 -Check
#   Install:                       powershell -ExecutionPolicy Bypass -File .\Install-PitStopHelper.ps1 -Install
#   Remove:                        powershell -ExecutionPolicy Bypass -File .\Install-PitStopHelper.ps1 -Remove
#
# -Check prints the PitStop CLI version, the standard profiles and action lists on this machine, and runs one test
#   file through the CLI into a temp folder (deleted after). Paste its output back into the thread.
# -Install copies the helper to C:\ProgramData\DrummondPitStopHelper, asks for the portal secret (typed, not shown),
#   stores it encrypted for this machine, and adds a scheduled task "Drummond PitStop helper" that runs as SYSTEM at
#   startup and restarts on failure. It only makes outbound HTTPS calls to the portal.
# -Remove deletes the task and the folder.

param(
  [switch]$Check,
  [switch]$Install,
  [switch]$Remove,
  [string]$PortalUrl = 'https://client-portal.drummondit.workers.dev',
  [string]$Profile = '',
  [string]$ActionList = '',
  [int]$PollSeconds = 20
)

$ErrorActionPreference = 'Stop'
$dest = "$env:ProgramData\DrummondPitStopHelper"
$task = 'Drummond PitStop helper'

function Find-Cli {
  $c = Get-ChildItem 'C:\Program Files\Enfocus' -Recurse -Filter 'PitStopServerCLI.exe' -ErrorAction SilentlyContinue |
    Sort-Object FullName -Descending | Select-Object -First 1
  if (-not $c) { throw 'PitStopServerCLI.exe not found under C:\Program Files\Enfocus' }
  $c.FullName
}

if ($Check) {
  $cli = Find-Cli
  "CLI: $cli"
  & $cli -version 2>&1
  $res = Join-Path (Split-Path $cli) 'Resources'
  "`nStandard preflight profiles:"
  Get-ChildItem $res -Recurse -Filter *.ppp -ErrorAction SilentlyContinue | ForEach-Object { '  ' + $_.FullName.Substring($res.Length + 1) }
  "`nStandard action lists (Fix, Convert, Remove only):"
  Get-ChildItem $res -Recurse -Filter *.eal -ErrorAction SilentlyContinue | Where-Object { $_.FullName -match '\\(Fix|Convert|Remove)' } |
    ForEach-Object { '  ' + $_.FullName.Substring($res.Length + 1) }
  "`nLocal profiles and action lists (PitStop Server databases):"
  Get-ChildItem "$env:ProgramData\Enfocus", "$env:APPDATA\Enfocus" -Recurse -Include *.ppp, *.eal -ErrorAction SilentlyContinue | ForEach-Object { '  ' + $_.FullName }
  $tmp = Join-Path $env:TEMP ('pshelper-' + [guid]::NewGuid())
  New-Item -ItemType Directory $tmp | Out-Null
  try {
    $help = Get-ChildItem (Split-Path $cli) -Recurse -Filter '*CLI*.pdf' -ErrorAction SilentlyContinue | Select-Object -First 1
    $ppp = Get-ChildItem $res -Recurse -Filter *.ppp -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($help -and $ppp) {
      & $cli -input $help.FullName -mutator $ppp.FullName -reportJSON (Join-Path $tmp 'r.json') 2>&1
      "`nTest run exit code: $LASTEXITCODE; JSON report written: $(Test-Path (Join-Path $tmp 'r.json'))"
    } else { "`nTest run skipped (no sample PDF or profile found)" }
  } finally { Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue }
  "`nOutbound HTTPS to the portal:"
  try { $r = Invoke-WebRequest -UseBasicParsing -Method Head "$PortalUrl/" -TimeoutSec 15; "  reachable ($($r.StatusCode))" } catch { "  NOT reachable: $($_.Exception.Message)" }
  return
}

if ($Remove) {
  schtasks /Delete /TN $task /F 2>$null | Out-Null
  Remove-Item -Recurse -Force $dest -ErrorAction SilentlyContinue
  'Removed.'
  return
}

if ($Install) {
  if (-not $Profile) { throw 'Pass -Profile with the full path of the .ppp to use (see -Check output).' }
  if (-not (Test-Path $Profile)) { throw "Profile not found: $Profile" }
  if ($ActionList -and -not (Test-Path $ActionList)) { throw "Action list not found: $ActionList" }
  $cli = Find-Cli
  New-Item -ItemType Directory -Force $dest | Out-Null
  Copy-Item -Force (Join-Path $PSScriptRoot 'PitStopHelper.ps1') $dest
  [ordered]@{ portalUrl = $PortalUrl; cli = $cli; profile = $Profile; actionList = $ActionList; pollSeconds = $PollSeconds; maxPreviews = 4; keepWork = $false } |
    ConvertTo-Json | Set-Content -Encoding UTF8 (Join-Path $dest 'config.json')

  $ss = Read-Host -AsSecureString 'Portal secret for the PitStop helper'
  $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR([Runtime.InteropServices.Marshal]::SecureStringToBSTR($ss))
  Add-Type -AssemblyName System.Security
  $enc = [Security.Cryptography.ProtectedData]::Protect([Text.Encoding]::UTF8.GetBytes($plain), $null, 'LocalMachine')
  [IO.File]::WriteAllBytes((Join-Path $dest 'secret.bin'), $enc)
  $plain = $null
  # Only SYSTEM and Administrators may read the helper folder.
  icacls $dest /inheritance:r /grant:r 'SYSTEM:(OI)(CI)F' 'Administrators:(OI)(CI)F' | Out-Null

  $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$dest\PitStopHelper.ps1`""
  $trigger = New-ScheduledTaskTrigger -AtStartup
  $settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable
  Register-ScheduledTask -TaskName $task -Action $action -Trigger $trigger -Settings $settings -User 'SYSTEM' -RunLevel Highest -Force | Out-Null
  Start-ScheduledTask -TaskName $task
  Start-Sleep 5
  "Installed. Task state: $((Get-ScheduledTask -TaskName $task).State). Log: $dest\helper.log"
  Get-Content (Join-Path $dest 'helper.log') -ErrorAction SilentlyContinue | Select-Object -Last 5
  return
}

'Pass -Check, -Install or -Remove.'

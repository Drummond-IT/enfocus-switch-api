<#
.SYNOPSIS
  Installs the Enfocus Switch automation service as a Windows service using NSSM
  (https://nssm.cc, a small, widely used service wrapper), plus the weekday digest task.

.DESCRIPTION
  Run in an elevated PowerShell on the server that will host the service, after:
    1. uv tool install "<repo>\switch-mcp[pace]"   (as the service account, or system-wide)
    2. The config file exists and `enfocus-switch-mcp check --env-file <ConfigFile>` passes.
  The service runs as a dedicated low-privilege account (-ServiceUser). Give that account
  read access to the config folder and write access to the data folder only.

.EXAMPLE
  .\install-service.ps1 -Exe "C:\Users\svc-switchmcp\.local\bin\enfocus-switch-mcp.exe" `
      -ConfigFile "C:\ProgramData\EnfocusSwitchMCP\config.env" -ServiceUser ".\svc-switchmcp"
#>
param(
  [Parameter(Mandatory = $true)] [string] $Exe,
  [Parameter(Mandatory = $true)] [string] $ConfigFile,
  [Parameter(Mandatory = $true)] [string] $ServiceUser,
  [string] $Nssm = "nssm.exe",
  [string] $Name = "EnfocusSwitchAutomation",
  [string] $DataDir = "C:\ProgramData\EnfocusSwitchMCP\data",
  [switch] $NoDigest
)
$ErrorActionPreference = "Stop"

if (-not (Test-Path $Exe)) { throw "Not found: $Exe" }
if (-not (Test-Path $ConfigFile)) { throw "Not found: $ConfigFile" }
New-Item -ItemType Directory -Force -Path $DataDir | Out-Null

& $Exe check --env-file $ConfigFile
if ($LASTEXITCODE -ne 0) { throw "The configuration check failed; fix it before installing the service." }

$cred = Get-Credential -UserName $ServiceUser -Message "Password for the service account"
& $Nssm install $Name $Exe service --env-file $ConfigFile
& $Nssm set $Name AppDirectory $DataDir
& $Nssm set $Name DisplayName "Enfocus Switch automation service (Drummond)"
& $Nssm set $Name Start SERVICE_AUTO_START
& $Nssm set $Name ObjectName $ServiceUser $cred.GetNetworkCredential().Password
& $Nssm set $Name AppStdout (Join-Path $DataDir "service.log")
& $Nssm set $Name AppStderr (Join-Path $DataDir "service.log")
& $Nssm set $Name AppRotateFiles 1
& $Nssm set $Name AppRotateBytes 10485760
& $Nssm start $Name
Write-Host "Service '$Name' installed and started. Test: Invoke-RestMethod http://127.0.0.1:8765/health"

if (-not $NoDigest) {
  $action = New-ScheduledTaskAction -Execute $Exe -Argument "digest --post --hours 16 --env-file `"$ConfigFile`""
  $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 6:30am
  Register-ScheduledTask -TaskName "$Name Digest" -Action $action -Trigger $trigger `
    -User $ServiceUser -Password $cred.GetNetworkCredential().Password -RunLevel Limited -Force | Out-Null
  Write-Host "Scheduled task '$Name Digest' registered (weekdays 6:30)."
}

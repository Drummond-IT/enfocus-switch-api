<#
.SYNOPSIS
  Installs the Enfocus Switch automation service as a Windows service using NSSM
  (https://nssm.cc, a small, widely used service wrapper), plus the weekday digest task.

.DESCRIPTION
  Run in an elevated PowerShell on the server that will host the service, after:
    1. Installing the tool somewhere only administrators can write, e.g.
         $env:UV_TOOL_DIR = "C:\Program Files\EnfocusSwitchMCP\tools"
         $env:UV_TOOL_BIN_DIR = "C:\Program Files\EnfocusSwitchMCP\bin"
         uv tool install "<repo>\switch-mcp[pace]"
       (The script runs the exe elevated, so it must not be writable by the service account.)
    2. The config file exists and `enfocus-switch-mcp check --env-file <ConfigFile>` passes.
  The service runs as a dedicated low-privilege account (-ServiceUser). The script locks the data
  folder down to SYSTEM, Administrators and that account, and makes the config file read-only for it.

.EXAMPLE
  .\install-service.ps1 -Exe "C:\Users\svc-switchmcp\.local\bin\enfocus-switch-mcp.exe" `
      -ConfigFile "C:\ProgramData\EnfocusSwitchMCP\config.env" -ServiceUser ".\svc-switchmcp"
  (Exe: "C:\Program Files\EnfocusSwitchMCP\bin\enfocus-switch-mcp.exe" with the install above.)
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

# Owner-only access: the data folder holds the audit log and analytics (customer names, job numbers);
# the config folder holds passwords. The service account may change data but only read config.
$ConfigDir = Split-Path -Parent (Resolve-Path $ConfigFile)
icacls $DataDir /inheritance:r /grant:r "SYSTEM:(OI)(CI)F" "Administrators:(OI)(CI)F" "${ServiceUser}:(OI)(CI)M" | Out-Null
icacls $ConfigDir /inheritance:r /grant:r "SYSTEM:(OI)(CI)F" "Administrators:(OI)(CI)F" "${ServiceUser}:(OI)(CI)RX" | Out-Null

& $Exe check --env-file $ConfigFile
if ($LASTEXITCODE -ne 0) { throw "The configuration check failed; fix it before installing the service." }

$cred = Get-Credential -UserName $ServiceUser -Message "Password for the service account"
& $Nssm install $Name $Exe service --env-file $ConfigFile
& $Nssm set $Name AppDirectory $DataDir
& $Nssm set $Name DisplayName "Enfocus Switch automation service (Drummond)"
& $Nssm set $Name Start SERVICE_AUTO_START
# Set the logon account in-process (CIM), so the password never appears on a process command line.
$svc = Get-CimInstance Win32_Service -Filter "Name='$Name'"
$res = Invoke-CimMethod -InputObject $svc -MethodName Change -Arguments @{
  StartName = $ServiceUser; StartPassword = $cred.GetNetworkCredential().Password }
if ($res.ReturnValue -ne 0) { throw "Could not set the service account (Win32_Service.Change returned $($res.ReturnValue))." }
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

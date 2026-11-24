# PitStop helper for Drummond One.
# Runs on the PitStop Server machine (switch03). It only makes outbound HTTPS calls to the portal, so no port,
# share or firewall change is needed. It asks the portal for the next file, runs PitStop Server's command-line tool
# (PitStopServerCLI.exe) on it, and posts back the JSON report, the fixed PDF (fix jobs) and page previews.
#
# Portal side (to build in wavelength-dashboard):
#   GET  {portal}/api/pitstop/next            -> 204 when nothing waits, else JSON
#        { "id": "...", "name": "flyer.pdf", "mode": "check" | "fix", "file": "https://.../download" }
#   GET  {file}                                -> the file bytes (same bearer secret)
#   POST {portal}/api/pitstop/result?id=...    multipart: status (ok|error), message, report (report.json),
#                                              fixed (fixed.pdf, fix jobs only), preview1..previewN (PNG, first pages)
#   Every call sends Authorization: Bearer <secret>. The report is PitStop's JSON report, which the portal already
#   reads with summariseReport() in src/switch.js.
#
# Run:  powershell -ExecutionPolicy Bypass -File PitStopHelper.ps1 [-Once] [-ConfigPath <path>]
# Config: C:\ProgramData\DrummondPitStopHelper\config.json (written by Install-PitStopHelper.ps1).

param(
  [string]$ConfigPath = "$env:ProgramData\DrummondPitStopHelper\config.json",
  [switch]$Once
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Net.Http
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$cfg = Get-Content -Raw $ConfigPath | ConvertFrom-Json
$root = Split-Path $ConfigPath
$work = Join-Path $root 'work'
$log = Join-Path $root 'helper.log'
New-Item -ItemType Directory -Force -Path $work | Out-Null

function Write-Log($msg) {
  $line = "{0:u} {1}" -f (Get-Date).ToUniversalTime(), $msg
  Add-Content -Path $log -Value $line
  if ((Get-Item $log).Length -gt 5MB) { Move-Item -Force $log "$log.1" }
}

# The secret is stored encrypted with machine-scope DPAPI in secret.bin, readable only by SYSTEM and Administrators.
function Get-Secret {
  Add-Type -AssemblyName System.Security
  $enc = [IO.File]::ReadAllBytes((Join-Path $root 'secret.bin'))
  [Text.Encoding]::UTF8.GetString([Security.Cryptography.ProtectedData]::Unprotect($enc, $null, 'LocalMachine'))
}

$client = New-Object System.Net.Http.HttpClient
$client.Timeout = [TimeSpan]::FromMinutes(10)
$client.DefaultRequestHeaders.Authorization = New-Object System.Net.Http.Headers.AuthenticationHeaderValue('Bearer', (Get-Secret))
$client.DefaultRequestHeaders.UserAgent.ParseAdd('DrummondPitStopHelper/1')
$portal = $cfg.portalUrl.TrimEnd('/')

function Esc([string]$s) { [Security.SecurityElement]::Escape($s) }

# One CLI run: a config XML with the mutators in order (action list first, then the preflight profile), the JSON
# report, and the output PDF for fix jobs. Returns the CLI exit code; preflight findings never make it nonzero.
function Invoke-PitStop($in, $out, $report, [string[]]$mutators, $taskReport) {
  $m = ($mutators | ForEach-Object {
    if ($_ -match '\.eal$') { "<cf:ActionList>$(Esc $_)</cf:ActionList>" } else { "<cf:PreflightProfile>$(Esc $_)</cf:PreflightProfile>" }
  }) -join "`n    "
  $outXml = if ($out) { "<cf:OutputPDF><cf:OutputPath>$(Esc $out)</cf:OutputPath></cf:OutputPDF>" } else { '' }
  $xml = @"
<?xml version="1.0" encoding="utf-8"?>
<cf:Configuration xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:cf="http://www.enfocus.com/PitStop/25.03/PitStopServerCLI_Configuration.xsd">
  <cf:Versioning><cf:Version>15</cf:Version><cf:VersioningStrategy>BestEffort</cf:VersioningStrategy></cf:Versioning>
  <cf:Initialize><cf:ProcessingMethod>EnforceServer</cf:ProcessingMethod></cf:Initialize>
  <cf:TaskReport><cf:TaskReportPath>$(Esc $taskReport)</cf:TaskReportPath></cf:TaskReport>
  <cf:Process>
    <cf:InputPDF><cf:InputPath>$(Esc $in)</cf:InputPath></cf:InputPDF>
    $outXml
    <cf:Reports><cf:ReportJSON><cf:ReportPath>$(Esc $report)</cf:ReportPath><cf:MaxReportedNbItemsPerCategory>50</cf:MaxReportedNbItemsPerCategory><cf:MaxReportedNbOccurrencesPerItem>20</cf:MaxReportedNbOccurrencesPerItem></cf:ReportJSON></cf:Reports>
    <cf:Mutators>
    $m
    </cf:Mutators>
  </cf:Process>
</cf:Configuration>
"@
  $cfgFile = Join-Path (Split-Path $in) 'config.xml'
  [IO.File]::WriteAllText($cfgFile, $xml, (New-Object Text.UTF8Encoding $false))
  $p = Start-Process -FilePath $cfg.cli -ArgumentList @('-config', "`"$cfgFile`"") -Wait -PassThru -NoNewWindow `
    -RedirectStandardError (Join-Path (Split-Path $in) 'stderr.txt') -RedirectStandardOutput (Join-Path (Split-Path $in) 'stdout.txt')
  return $p.ExitCode
}

# Page previews of the first pages, PNG, via the CLI's PDF-to-image option.
function Get-Previews($pdf, $dir) {
  $pattern = Join-Path $dir 'preview_%1.png'
  $p = Start-Process -FilePath $cfg.cli -ArgumentList @('-input', "`"$pdf`"", '-outputImage', "`"$pattern`"", '-imageType', 'PNG') -Wait -PassThru -NoNewWindow `
    -RedirectStandardError (Join-Path $dir 'preview-stderr.txt') -RedirectStandardOutput (Join-Path $dir 'preview-stdout.txt')
  if ($p.ExitCode -ne 0) { return @() }
  Get-ChildItem $dir -Filter 'preview_*.png' | Sort-Object { [int]($_.BaseName -replace '\D', '') } | Select-Object -First ([int]$cfg.maxPreviews)
}

function Send-Result($id, $status, $message, $files) {
  $form = New-Object System.Net.Http.MultipartFormDataContent
  $form.Add((New-Object System.Net.Http.StringContent $status), 'status')
  $form.Add((New-Object System.Net.Http.StringContent ([string]$message)), 'message')
  foreach ($k in $files.Keys) {
    $path = $files[$k]
    if (-not $path -or -not (Test-Path $path)) { continue }
    $c = New-Object System.Net.Http.ByteArrayContent (,[IO.File]::ReadAllBytes($path))
    $form.Add($c, $k, (Split-Path $path -Leaf))
  }
  $r = $client.PostAsync("$portal/api/pitstop/result?id=$([Uri]::EscapeDataString($id))", $form).Result
  if (-not $r.IsSuccessStatusCode) { throw "result for $id not accepted ($([int]$r.StatusCode))" }
}

function Invoke-Job($job) {
  $dir = Join-Path $work ($job.id -replace '[^\w.-]', '_')
  New-Item -ItemType Directory -Force -Path $dir | Out-Null
  try {
    $in = Join-Path $dir 'input.pdf'
    $bytes = $client.GetByteArrayAsync($job.file).Result
    [IO.File]::WriteAllBytes($in, $bytes)
    $report = Join-Path $dir 'report.json'
    $task = Join-Path $dir 'task.xml'
    $fix = $job.mode -eq 'fix'
    $out = if ($fix) { Join-Path $dir 'fixed.pdf' } else { $null }
    $mutators = @()
    if ($fix) { $mutators += $cfg.actionList }
    $mutators += $cfg.profile
    $code = Invoke-PitStop $in $out $report $mutators $task
    if ($code -ne 0) {
      $err = (Get-Content (Join-Path $dir 'stderr.txt') -ErrorAction SilentlyContinue | Where-Object { $_ } | Select-Object -Last 1)
      Send-Result $job.id 'error' "PitStop could not process the file ($code): $err" @{}
      Write-Log "job $($job.id) failed: $code $err"
      return
    }
    $files = @{ report = $report }
    if ($fix) { $files.fixed = $out }
    $i = 0
    foreach ($p in (Get-Previews $(if ($fix) { $out } else { $in }) $dir)) { $i++; $files["preview$i"] = $p.FullName }
    Send-Result $job.id 'ok' '' $files
    Write-Log "job $($job.id) $($job.mode) done, $i previews"
  } catch {
    Write-Log "job $($job.id) error: $($_.Exception.Message)"
    try { Send-Result $job.id 'error' 'The preflight helper hit a problem with this file.' @{} } catch {}
  } finally {
    if (-not $cfg.keepWork) { Remove-Item -Recurse -Force $dir -ErrorAction SilentlyContinue }
  }
}

Write-Log "helper started, portal $portal"
do {
  try {
    $r = $client.GetAsync("$portal/api/pitstop/next").Result
    if ([int]$r.StatusCode -eq 200) {
      $job = $r.Content.ReadAsStringAsync().Result | ConvertFrom-Json
      Invoke-Job $job
      continue   # ask again right away while work is waiting
    } elseif ([int]$r.StatusCode -ne 204) {
      Write-Log "portal answered $([int]$r.StatusCode)"
    }
  } catch { Write-Log "poll error: $($_.Exception.Message)" }
  if (-not $Once) { Start-Sleep -Seconds ([int]$cfg.pollSeconds) }
} while (-not $Once)

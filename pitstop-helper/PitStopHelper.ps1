# PitStop helper for Drummond One.
# Runs on the PitStop Server machine (switch03). It only makes outbound HTTPS calls to the portal, so no port,
# share or firewall change is needed. It asks the portal for the next file, runs PitStop Server's command-line tool
# (PitStopServerCLI.exe) on it, and posts back the JSON report, the fixed PDF (fix jobs) and page previews.
#
# Portal side (to build in wavelength-dashboard):
#   GET  {portal}/api/pitstop/next            -> 204 when nothing waits, else JSON
#        { "id": "...", "name": "flyer.pdf", "mode": "check" | "fix" | "tool", "tool": "printready", "file": "https://.../download" }
#        A tool job (client file tools, F-1494 and F-1499) runs the action lists under config "tools" for that tool name.
#   GET  {file}                                -> the file bytes (same bearer secret)
#   POST {portal}/api/pitstop/result?id=...    multipart: status (ok|error), message, report (report.json),
#                                              fixed (fixed.pdf, fix and tool jobs), preview1..previewN (PNG, first pages),
#                                              pages (one letter a page: c color, k black only, w empty; F-1498)
#   Every call sends Authorization: Bearer <secret>. The report is PitStop's JSON report, which the portal already
#   reads with summariseReport() in src/switch.js.
#
# Run:  powershell -ExecutionPolicy Bypass -File PitStopHelper.ps1 [-Once] [-ConfigPath <path>]
# Update: powershell -ExecutionPolicy Bypass -File C:\ProgramData\DrummondPitStopHelper\PitStopHelper.ps1 -SelfUpdate
#   fetches this script from the portal (GET /api/pitstop/helper-script, same secret), keeps the old copy as
#   PitStopHelper.ps1.before-update and restarts the task. Undo: copy that file back and restart the task.
# Config: C:\ProgramData\DrummondPitStopHelper\config.json (written by Install-PitStopHelper.ps1).

param(
  [string]$ConfigPath = "$env:ProgramData\DrummondPitStopHelper\config.json",
  [switch]$Once,
  [switch]$SelfUpdate
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
$client.DefaultRequestHeaders.UserAgent.ParseAdd('DrummondPitStopHelper/2')
# The PitStop tools set up here (config "tools"), named on every poll so the portal offers only those (F-1494, F-1499).
$toolNames = @(if ($cfg.tools) { $cfg.tools.PSObject.Properties | ForEach-Object { $_.Name } })
# F-1505: EPS and PostScript to PDF with Ghostscript (config "ghostscript", the path to gswin64c.exe), as tool "topdf".
if ($cfg.ghostscript -and (Test-Path $cfg.ghostscript)) { $toolNames += 'topdf' }
$toolNames = $toolNames -join ','
if ($toolNames) { $client.DefaultRequestHeaders.Add('X-Helper-Tools', $toolNames) }
$portal = $cfg.portalUrl.TrimEnd('/')

if ($SelfUpdate) {
  $new = $client.GetStringAsync("$portal/api/pitstop/helper-script").Result
  if ($new.Length -lt 2000 -or $new -notmatch '# PitStop helper for Drummond One') { throw 'The portal did not send the helper script.' }
  $me = $PSCommandPath
  Copy-Item -Force $me "$me.before-update"
  [IO.File]::WriteAllText($me, $new, (New-Object Text.UTF8Encoding $false))
  Write-Log "helper updated from the portal ($($new.Length) characters); restarting the task"
  Stop-ScheduledTask -TaskName 'Drummond PitStop helper' -ErrorAction SilentlyContinue
  Start-ScheduledTask -TaskName 'Drummond PitStop helper'
  "Updated. The old copy is $me.before-update"
  return
}

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

# Page previews, PNG, 800 px wide, showing the bleed box, via the CLI's PDF-to-image task. Checking and rendering can't
# share one CLI run (error 103), so this is a second run. The CLI has no page range, so every page renders (small and
# fast) and the first few are sent.
function Get-Previews($pdf, $dir) {
  $pattern = Join-Path $dir 'preview_%1.png'
  $xml = @"
<?xml version="1.0" encoding="utf-8"?>
<cf:Configuration xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:cf="http://www.enfocus.com/PitStop/25.03/PitStopServerCLI_Configuration.xsd">
  <cf:Versioning><cf:Version>15</cf:Version><cf:VersioningStrategy>BestEffort</cf:VersioningStrategy></cf:Versioning>
  <cf:Initialize><cf:ProcessingMethod>EnforceInternal</cf:ProcessingMethod></cf:Initialize>
  <cf:ExportImage>
    <cf:InputPDF><cf:InputPath>$(Esc $pdf)</cf:InputPath></cf:InputPDF>
    <cf:OutputImages><cf:OutputPath>$(Esc $pattern)</cf:OutputPath><cf:SaveOptions FileType="PNG"><cf:Interlaced>false</cf:Interlaced></cf:SaveOptions></cf:OutputImages>
    <cf:RenderOptions>
      <cf:RenderType>Composite</cf:RenderType>
      <cf:RenderArea>BleedBox</cf:RenderArea>
      <cf:ImageDimensions><cf:Width>800</cf:Width></cf:ImageDimensions>
      <cf:IncludeEmptyPages>true</cf:IncludeEmptyPages>
      <cf:AntiAliasing>true</cf:AntiAliasing>
    </cf:RenderOptions>
  </cf:ExportImage>
</cf:Configuration>
"@
  $cfgFile = Join-Path $dir 'preview.xml'
  [IO.File]::WriteAllText($cfgFile, $xml, (New-Object Text.UTF8Encoding $false))
  $p = Start-Process -FilePath $cfg.cli -ArgumentList @('-config', "`"$cfgFile`"") -Wait -PassThru -NoNewWindow `
    -RedirectStandardError (Join-Path $dir 'preview-stderr.txt') -RedirectStandardOutput (Join-Path $dir 'preview-stdout.txt')
  if ($p.ExitCode -ne 0) { return @() }
  Get-ChildItem $dir -Filter 'preview_*.png' | Sort-Object { [int]($_.BaseName -replace '\D', '') }
}

# F-1498: what each rendered page holds, one letter a page: c (some color), k (black and grays only) or w (empty).
# Every 6th pixel each way is read; a pixel is color when its channels differ by more than 40, ink when its darkest
# channel is under 245. A page needs 0.1% color samples to count as color and 0.05% ink samples not to be empty, so
# anti-aliasing and a stray speck don't count.
function Get-PageColors($pngs) {
  Add-Type -AssemblyName System.Drawing
  $out = ''
  foreach ($png in $pngs) {
    $bmp = [System.Drawing.Bitmap]::FromFile($png.FullName)
    try {
      $n = 0; $color = 0; $ink = 0
      for ($y = 0; $y -lt $bmp.Height; $y += 6) {
        for ($x = 0; $x -lt $bmp.Width; $x += 6) {
          $p = $bmp.GetPixel($x, $y); $n++
          $hi = [Math]::Max($p.R, [Math]::Max($p.G, $p.B)); $lo = [Math]::Min($p.R, [Math]::Min($p.G, $p.B))
          if ($hi - $lo -gt 40) { $color++ }
          if ($lo -lt 245) { $ink++ }
        }
      }
      $ch = if ($n -and $color / $n -ge 0.001) { 'c' } elseif ($n -and $ink / $n -ge 0.0005) { 'k' } else { 'w' }
      $out += $ch
    } finally { $bmp.Dispose() }
  }
  $out
}

function Send-Result($id, $status, $message, $files, $fields = @{}) {
  $form = New-Object System.Net.Http.MultipartFormDataContent
  $form.Add((New-Object System.Net.Http.StringContent $status), 'status')
  $form.Add((New-Object System.Net.Http.StringContent ([string]$message)), 'message')
  foreach ($k in $fields.Keys) { $form.Add((New-Object System.Net.Http.StringContent ([string]$fields[$k])), $k) }
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
    $tool = $job.mode -eq 'tool'
    $out = if ($fix -or $tool) { Join-Path $dir 'fixed.pdf' } else { $null }
    $mutators = @()
    if ($fix) { $mutators += $cfg.actionList }
    if ($tool -and $job.tool -eq 'topdf') {
      # F-1505: the file is EPS or PostScript: Ghostscript makes the PDF, which is then checked like any PDF.
      $src = Join-Path $dir 'input.src'
      Move-Item -Force $in $src
      $gs = Start-Process -FilePath $cfg.ghostscript -ArgumentList @('-dSAFER', '-dBATCH', '-dNOPAUSE', '-dQUIET', '-sDEVICE=pdfwrite', '-dEPSCrop', '-dPDFSETTINGS=/prepress', "-sOutputFile=`"$in`"", "`"$src`"") -NoNewWindow -Wait -PassThru -RedirectStandardError (Join-Path $dir 'gs-stderr.txt')
      if ($gs.ExitCode -ne 0 -or -not (Test-Path $in)) {
        Send-Result $job.id 'error' "We couldn't turn this file into a PDF ($($gs.ExitCode))." @{}
        Write-Log "job $($job.id) ghostscript failed: $($gs.ExitCode)"
        return
      }
    } elseif ($tool) {
      # The tool's action lists (config "tools", one array per tool name). A tool that isn't set up here is an error,
      # so the customer is told rather than given an unchanged copy.
      $lists = if ($cfg.tools) { $cfg.tools.($job.tool) } else { $null }
      if (-not $lists) {
        Send-Result $job.id 'error' "The $($job.tool) tool has no action lists on the PitStop server yet." @{}
        Write-Log "job $($job.id) tool $($job.tool) has no action lists"
        return
      }
      $mutators += $lists
    }
    $mutators += $cfg.profile
    $code = Invoke-PitStop $in $out $report $mutators $task
    if ($code -ne 0) {
      $err = (Get-Content (Join-Path $dir 'stderr.txt') -ErrorAction SilentlyContinue | Where-Object { $_ } | Select-Object -Last 1)
      Send-Result $job.id 'error' "PitStop could not process the file ($code): $err" @{}
      Write-Log "job $($job.id) failed: $code $err"
      return
    }
    if ($out -and -not (Test-Path $out)) { Copy-Item $in $out }
    $files = @{ report = $report }
    if ($fix -or $tool) { $files.fixed = $out }
    $pngs = @(Get-Previews $(if ($fix -or $tool) { $out } else { $in }) $dir)
    $i = 0
    foreach ($p in ($pngs | Select-Object -First ([int]$cfg.maxPreviews))) { $i++; $files["preview$i"] = $p.FullName }
    $fields = @{}
    try { if ($pngs.Count) { $fields.pages = Get-PageColors $pngs } } catch { Write-Log "job $($job.id) page read: $($_.Exception.Message)" }
    Send-Result $job.id 'ok' '' $files $fields
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

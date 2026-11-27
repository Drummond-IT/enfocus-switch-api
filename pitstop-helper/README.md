# PitStop helper

Runs customer preflight and auto-fix for Drummond One straight on PitStop Server, with no Switch flow. Chris chose this
on 2026-11-24, because Switch allows only one Designer connection at a time.

## How it works

The helper runs on switch03 (PitStop Server 26.03). Every 20 seconds it asks the portal for the next file, over outbound
HTTPS only. For each file it:

1. Downloads it.
2. Runs `PitStopServerCLI.exe` on it with the preflight profile. A fix job runs the action list first, then the profile.
3. Posts back three things: PitStop's JSON report, the fixed PDF (fix jobs only) and PNG previews of the first pages.

The portal already reads the JSON report with `summariseReport()` in `src/switch.js`, so the customer screens stay the
same. No port, share, firewall or Switch change is needed.

## Portal API (to build in wavelength-dashboard)

| Call | What it does |
|---|---|
| `GET /api/pitstop/next` | `204` when nothing waits. Otherwise `{ id, name, mode: "check" \| "fix" \| "tool", tool?, file }`, where `file` is a download URL. Each poll sends `X-Helper-Tools` (the tools set up in `config.json`) |
| `GET /api/pitstop/helper-script` | The current helper script, for `-SelfUpdate` |
| `GET <file>` | The file's bytes |
| `POST /api/pitstop/result?id=…` | Multipart: `status` (`ok` or `error`), `message`, `report` (JSON), `fixed` (PDF), `preview1..4` (PNG), `pages` (one letter per page: `c` color, `k` black only, `w` empty) |

Every call sends `Authorization: Bearer <PITSTOP_HELPER_SECRET>`.

## Setup on switch03 (one time, elevated PowerShell)

Steps 1 and 2 change nothing. Steps 3 and 4 need Chris's go.

1. Copy this folder to switch03.
2. Run the check, and paste its output back into the thread:
   `powershell -ExecutionPolicy Bypass -File .\Install-PitStopHelper.ps1 -Check`
   It shows the CLI version, the standard profiles and action lists, a test run, and whether the portal is reachable.
3. Install: `powershell -ExecutionPolicy Bypass -File .\Install-PitStopHelper.ps1 -Install`
   With no `-Profile` or `-ActionList`, it uses Enfocus's Generic Press profile and the Convert all RGB to CMYK action
   list. Both ship with PitStop Server.
   It asks for the portal secret (the person installing types it), stores it encrypted for that machine only, and adds
   the scheduled task "Drummond PitStop helper". The task runs as SYSTEM at startup.
4. Later, when the custom "Drummond Portal Preflight" profile and "Drummond Portal Auto-fix" action list are built in
   PitStop Pro on switch03 (specs in `../switch-packages/`), run `-Install` again with their paths.

To undo everything: `-Remove`.

## Client file tools (F-1494, F-1499)

The portal's file tools that need PitStop (print-ready PDF/X-4, grayscale, one color) run action lists named in
`config.json` under `tools`, for example:

```json
"tools": {
  "printready": ["C:\\ProgramData\\DrummondPitStopHelper\\tools\\printready.eal"],
  "grayscale": ["C:\\ProgramData\\DrummondPitStopHelper\\tools\\grayscale.eal"],
  "onecolor": ["C:\\ProgramData\\DrummondPitStopHelper\\tools\\onecolor.eal"]
}
```

EPS and PostScript files become PDFs with Ghostscript (F-1505) when `config.json` has `"ghostscript"`, the path to
`gswin64c.exe` (already on switch03). Ghostscript runs with `-dSAFER`. The helper then reports tool `topdf`.

The portal offers a tool only after the helper reports it. The lists are PitStop Server's stock lists, named by path
(read only), or new lists kept in the helper's own `tools` folder; existing PitStop resources are never changed.

## InDesign exporter (D320 c, F-1504)

A raw InDesign file with no PDF is made into PDF/X-4 by desktop InDesign on switch03 (Drummond allocates a dedicated
Creative Cloud license to each server). InDesign can't run as SYSTEM, so the same script runs a second time with
`-InDesign` in the signed-in session, as the scheduled task "Drummond InDesign export" (at logon, that user, only when
signed in). It takes only InDesign jobs, opens the InDesign file from the package (links and fonts from its Links and
Document fonts folders), stops with a message when any link or font is missing, exports with the preset in
`"indesignPreset"` (default `[PDF/X-4:2008]`), then checks the PDF with the PitStop profile. Its log is `indesign.log`.

The task runs at the normal (not elevated) level, so it can't block or be blocked by an InDesign someone starts. It
never shares an open InDesign: while InDesign is open on the machine it asks the portal for no work, and it quits the
InDesign it started once no document is open. Work it doesn't take goes to prepress after two hours.

## Updating the helper

`powershell -ExecutionPolicy Bypass -File .\PitStopHelper.ps1 -SelfUpdate` downloads the script from the portal,
keeps the old copy as `PitStopHelper.ps1.before-update` and restarts the task.

## Files on switch03

Everything lives in `C:\ProgramData\DrummondPitStopHelper` (SYSTEM and Administrators only):

| File | Contents |
|---|---|
| `config.json` | Settings |
| `secret.bin` | The portal secret, encrypted with machine-scope DPAPI |
| `helper.log` | The log, rolled over at 5 MB |
| `work\` | One folder per job, deleted when the job is done |

## Checked on switch03 (2026-11-24)

- The CLI is `C:\Program Files\Enfocus\Enfocus PitStop Server 26\PitStopServerCLI.exe`, 26.03 Build 1664209.
- The JSON report has `preflightReport.errors.preflightReportItem[].message`, which `summariseReport()` reads as it is.
- Checking and rendering images in one CLI run fails with error 103, so previews are a second run.
- switch03 reaches the test portal over HTTPS.

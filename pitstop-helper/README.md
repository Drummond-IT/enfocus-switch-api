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
| `GET /api/pitstop/next` | `204` when nothing waits. Otherwise `{ id, name, mode: "check" \| "fix", file }`, where `file` is a download URL |
| `GET <file>` | The file's bytes |
| `POST /api/pitstop/result?id=…` | Multipart: `status` (`ok` or `error`), `message`, `report` (JSON), `fixed` (PDF), `preview1..4` (PNG) |

Every call sends `Authorization: Bearer <PITSTOP_HELPER_SECRET>`.

## Setup on switch03 (one time, elevated PowerShell)

Steps 1 and 2 change nothing. Steps 3 and 4 need Chris's go.

1. Copy this folder to switch03.
2. Run the check, and paste its output back into the thread:
   `powershell -ExecutionPolicy Bypass -File .\Install-PitStopHelper.ps1 -Check`
   It shows the CLI version, the standard profiles and action lists, a test run, and whether the portal is reachable.
3. Install, with the profile chosen from the check:
   `powershell -ExecutionPolicy Bypass -File .\Install-PitStopHelper.ps1 -Install -Profile "<path>.ppp" -ActionList "<path>.eal"`
   It asks for the portal secret (the person installing types it), stores it encrypted for that machine only, and adds
   the scheduled task "Drummond PitStop helper". The task runs as SYSTEM at startup.
4. Later, when the custom "Drummond Portal Preflight" profile and "Drummond Portal Auto-fix" action list are built in
   PitStop Pro on switch03 (specs in `../switch-packages/`), run `-Install` again with their paths.

To undo everything: `-Remove`.

## Files on switch03

Everything lives in `C:\ProgramData\DrummondPitStopHelper` (SYSTEM and Administrators only):

| File | Contents |
|---|---|
| `config.json` | Settings |
| `secret.bin` | The portal secret, encrypted with machine-scope DPAPI |
| `helper.log` | The log, rolled over at 5 MB |
| `work\` | One folder per job, deleted when the job is done |

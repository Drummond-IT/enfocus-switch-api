# Runbook

For whoever supports the connector and the automation service day to day. Keep the "Site details"
table current.

## Site details (fill in at deployment)

| Item | Value |
|---|---|
| Service host / URL | |
| Service account | |
| Config folder | |
| Data folder (audit log, analytics DB) | |
| Switch user for staff / for the service | `ai-connector` / `automation-service` |
| API keys issued (names and scopes only) | e.g. `portal` (preflight), `switch` (switch_events, match_job) |
| Switch flows that call the service | |
| Pace statuses automation may set | |
| Owner (IT) / Switch admin / Pace admin | |
| Current version (git commit / image tag) | |

## Emergency off switches

Change the setting in `config.env`, then restart the service (or, for Claude, have the person restart
Claude).

| Problem | Setting |
|---|---|
| Wrong things are changing in Pace | `PACE_ALLOW_WRITE=false` |
| Wrong jobs are being routed automatically | `SERVICE_AUTO_ROUTE=false` |
| Anything the service changes is wrong | `SERVICE_DRY_RUN=true` |
| A caller is misbehaving, or its key leaked | Remove its entry from `service_keys.json` |
| A Claude user routed something wrongly | Their `SWITCH_ALLOW_WRITE=false`; undo the route in Switch Designer |

Restart the service:
- **Windows:** `Restart-Service EnfocusSwitchAutomation`
- **Linux:** `sudo systemctl restart enfocus-switch-service`
- **Docker:** `docker restart enfocus-switch-service`

## Health and monitoring

- **Liveness:** `GET /health` returns `{"ok": true, "version": "..."}`. It needs no key. Alert if it fails
  twice in a row.
- **End-to-end check** (daily, or after changes):
  `enfocus-switch-mcp check --env-file <config.env>`. It logs in to Switch, lists flows, reads the message
  log, and runs a Pace query if Pace is configured. A non-zero exit code means something is wrong, and the
  output says what.
- **Service logs:**
  - Windows: `<data folder>\service.log` (NSSM, rotates at 10 MB).
  - Linux: `journalctl -u enfocus-switch-service`.
  - Docker: `docker logs enfocus-switch-service`.
- **Audit trail:** `AUDIT_LOG` has one JSON line per request and per Pace write attempt. Each line has
  time, caller key name, endpoint, job, result and HTTP status. It never contains keys, passwords or file
  contents. Search it with any JSON-lines tool, for example:

  ```bash
  grep '"status": 5' audit.jsonl | tail                      # server-side errors
  grep '"operation": "update_job_status"' audit.jsonl | tail  # Pace status writes
  ```

  The file is not rotated by the service: add it to logrotate or a scheduled task, and keep it at least
  as long as your audit policy requires.

## Common problems

| Symptom | Likely cause | Fix |
|---|---|---|
| `check`: "Login failed" | Wrong Switch password, or the user is disabled | Reset it in Switch Designer; update the password file |
| `check`: can't reach Switch | Web Services stopped, wrong host/port, firewall | Start the Web Services; test from the host with `curl <SWITCH_URL>/api/v1/...` |
| Service won't start: "SERVICE_KEYS_FILE ..." | Missing or invalid keys file | Recreate it with `service-key`; check the JSON |
| Service won't start: "PACE_ALLOW_WRITE is on but PACE_ALLOWED_STATUSES is empty" | Incomplete Pace write config | Set the allow-list, or turn writes off |
| Callers get 401 | Wrong key, or a new key without a restart | Check the caller's secret; restart after adding keys |
| Callers get 403 | The key lacks the scope | Re-issue the key with the right scopes |
| Callers get 429 | Over `SERVICE_RATE_LIMIT_PER_MIN` | Find out why (a loop in a flow?), or raise the limit |
| Callers get 502 "Switch ..." | Switch down, or the service user lacks access to that job | Check Switch; check the service user's permissions |
| Events: step `skipped` "No Pace job number" | Job name doesn't match `PACE_JOB_NUMBER_PATTERNS` and no number was sent | Send `pace_job_number` from the flow, or fix the patterns |
| Events: step `manual` "Pace writes are turned off" | Expected while `PACE_ALLOW_WRITE=false` | Someone does the step in Pace, or turn writes on |
| Events: step `failed` "Pace API refused ... HTTP 4xx" | Wrong endpoint/body in `pace_api.json`, a status unknown to Pace, or an expired Pace password | Check the Pace API response in the log; fix the config |
| Portal: "The file could not be read as a PDF" | Damaged or encrypted file | Expected; the customer re-exports |
| Claude says a tool doesn't exist | Write tools are off for that user (expected), or Claude wasn't restarted after a config change | Check their config; restart Claude |
| Wrong wording or owner in a preflight write-up | House policy differs | Edit `knowledge.py` (category text and fix owner), then deploy |

## Routine tasks

| Task | How | When |
|---|---|---|
| Rotate the Switch passwords | Change in Switch Designer, update the password files, restart the service, and tell staff to update theirs | Per policy (e.g. every 90 days) |
| Rotate API keys | `service-key --name <same name>` replaces the hash; update the caller; restart | Yearly, or when staff leave |
| Rotate the Pace API password | Update `PACE_API_PASSWORD`, restart | Per policy |
| Update the software | `git pull`, run the tests, reinstall or rebuild, restart, run `check` | Monthly, or for fixes |
| Review the audit log | Look for failed Pace writes, 401 bursts and unexpected callers | Weekly during rollout, then monthly |
| Analytics report | `enfocus-switch-mcp report --days 30`, shared with the CSR lead | Monthly |
| Back up | `analytics.sqlite`, `audit.jsonl`, the config folder (without passwords, or encrypted) | With server backups |
| Review customer rules | CSR lead checks the agreements are still valid | Quarterly |

## Changing behaviour safely

- **House wording and fix owners:** `switch-mcp/src/switch_mcp/knowledge.py`.
- **RFQ required fields:** `automation/rfq.py` (`REQUIRED`, `QUESTIONS`).
- **Standard products:** `automation/estimate_draft.py` (`STANDARD_PRODUCTS`).
- **Size tolerance:** `automation/ticket_check.py` (`SIZE_TOLERANCE_IN`).
- After any change: run the tests, deploy to the test service first, and re-run the affected UAT script.

# Administrator training

For IT, Switch and Pace admins who install, configure and support the connector and the automation
service. Work through these hands-on in the staging environment; each should take under 15 minutes
unaided.

| # | Exercise | Reference |
|---|---|---|
| 1 | Install the connector on a clean machine; `check` passes; Claude Desktop shows the tools | switch-mcp/README.md §2–5 |
| 2 | Turn write access on for one user and off again; confirm the tools appear and disappear | README §7 |
| 3 | Create an API key for a new caller with one scope; call an endpoint with it; get 403 on another endpoint | deployment.md §3.2, §4 |
| 4 | Revoke that key; confirm 401 | runbook.md |
| 5 | Switch the service between dry run and live; show the difference in `/switch/events` step statuses | deployment.md §3.1 |
| 6 | Read the audit log: find the last 5 Pace writes and any 4xx/5xx responses | runbook.md "Health and monitoring" |
| 7 | Add a new Switch event to `service_status_map.json`; fire it from the test flow | deployment.md §4.1 |
| 8 | Add a customer rule; show its effect in `explain_preflight_report` | README "Customer rules" |
| 9 | Change a preflight category's wording in `knowledge.py`; run the tests; deploy to test | runbook.md "Changing behaviour safely" |
| 10 | Rotate the Switch password for the service user without downtime beyond a restart | runbook.md "Routine tasks" |
| 11 | Use each emergency off switch and confirm the effect | runbook.md "Emergency off switches" |
| 12 | Update to a new version; roll back to the previous one | rollout.md "Rollback" |

**Settings that change risk.** Review these before changing them:
- `SWITCH_ALLOW_WRITE`
- `SWITCH_ALLOW_FLOW_CONTROL`
- `SERVICE_DRY_RUN`
- `SERVICE_AUTO_ROUTE`
- `PACE_ALLOW_WRITE`
- `PACE_ALLOWED_STATUSES`
- `SERVICE_HOST`

Record each change in the change log or Asana, with who approved it.

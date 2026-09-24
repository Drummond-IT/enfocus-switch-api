# Rollout plan

Roll out in phases. Each phase:
- turns on components that depend only on earlier phases;
- starts with a **pilot group**;
- goes wider only after its **go/no-go** criteria are met.

Every phase can be undone in minutes (see "Rollback").

Rough durations assume one IT owner part-time, plus the Switch and Pace admins for their parts. Adjust
to real availability in Asana.

| Phase | What goes live | Pilot | Go wider when | Rough duration |
|---|---|---|---|---|
| **0. Setup** | Switch users, test flow, sample files, test machines, test service | IT only | Staging checklist in test-plan.md §2 complete | 1 week |
| **1. Read-only Claude** | Connector (read-only), A1, B1 with typed ticket details, B3 digest, C1 drafts, C2 RFQ check | 2 prepress, 2 CSRs, 1 estimator | UAT for core, A1, B1, B3 signed off; 2 weeks of use with no wrong verdicts left unexplained | 2–3 weeks |
| **2. Pace read-only** | `pace_queries.sql`: A4, B1 by job number, B4 lookups, C3; job number patterns; D3 rules; D2 analytics | Same pilot + 2 more CSRs | UAT A4, B4, C3, D2/D3 signed off; queries under 1 s | 2 weeks |
| **3. Writes in Switch** | `SWITCH_ALLOW_WRITE` for trained prepress users; `approve_proof` with Pace steps still manual | 2 prepress leads | 2 weeks with no wrong routing; confirmations always shown | 1–2 weeks |
| **4. Automation service (dry run)** | Service deployed; Switch events and match-job wired in the **test** flow, then production flows in dry run | IT + Switch admin | A week of production events whose planned steps are all correct (audit log reviewed) | 1–2 weeks |
| **5. Pace writes** | `pace_api.json` on the Pace test system, then production; `PACE_ALLOW_WRITE=true` with a short allow-list; `SERVICE_DRY_RUN=false` for status sync | CSR lead watches every change for a week | UAT A3/A5/D1 signed off; no unexpected Pace changes for 1 week | 2 weeks |
| **6. Auto-fix routing** | Auto-fix branch in Switch; `SERVICE_AUTO_ROUTE=true` for the chosen categories | Prepress lead reviews every auto-routed job for 2 weeks | UAT B2 signed off; no customer-needed job ever auto-routed | 2 weeks |
| **7. Client-facing** | Portal self-serve preflight (A2), portal proof approval (A5) | 5–10 friendly customers | UAT A2 signed off; customer feedback reviewed; support load acceptable | 3–4 weeks |
| **8. Everyone + training** | All CSRs, prepress and estimators; monthly analytics review | — | Training done ([training/](training/)); runbook owner named | ongoing |

## Go/no-go checklist (every phase)

- [ ] UAT for the phase's components is signed off, with the tester's name in Asana.
- [ ] Automated tests pass on the deployed version (commit recorded).
- [ ] The runbook is updated: new settings, keys issued, who to call.
- [ ] Rollback for this phase has been tried once in staging.
- [ ] The pilot users have had the relevant training guide.
- [ ] The department lead agrees.

## Rollback

| To undo | Do this | Takes |
|---|---|---|
| Claude write access | Set `SWITCH_ALLOW_WRITE=false` in that person's config; they restart Claude | minutes |
| Everything the service changes | `SERVICE_DRY_RUN=true`, restart the service. Callers get plans, not changes | 1 minute |
| Pace writes only | `PACE_ALLOW_WRITE=false`, restart. Pace steps become "manual" with instructions | 1 minute |
| Auto-routing only | `SERVICE_AUTO_ROUTE=false`, restart | 1 minute |
| One caller (e.g. the portal) | Remove its key from `service_keys.json`, restart | 1 minute |
| The whole service | Stop the service. Switch HTTP elements fail; set their failure path to continue, so flows keep running | 1 minute |
| A bad release | Reinstall the previous tag/commit (`uv tool install --reinstall` or the previous image tag) | 5 minutes |

**Design flows to fail open.** Configure Switch HTTP request elements so that a failed call sends the job
down the normal manual path, never into an error that stops the job.

## Communication

- **Before each phase:** a short note to the affected department covering:
  - what changes
  - who's in the pilot
  - how to report problems (Asana form or Teams channel)
- **During pilots:** a 15-minute weekly check-in with pilot users. Log wording fixes as Asana tasks.
- **Customers (phase 7):** the portal message makes clear that the check is automatic and that prepress
  still reviews every file.

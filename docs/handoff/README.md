# Switch + Pace automation: IT handoff

This folder is the handoff for the IT and automation team. It covers the Enfocus Switch connector for
Claude (MCP server) and the automation web service in [`switch-mcp/`](../../switch-mcp/). It has what you
need to finish, validate, launch and support each component, and to train staff on it.

| Document | For | What's in it |
|---|---|---|
| **This page** | Everyone | What exists, how ready each piece is, who owns what |
| [deployment.md](deployment.md) | IT / Switch admin | Install the connector for staff; deploy the automation service (Windows, Linux, Docker); wire Switch and the portal to it |
| [test-plan.md](test-plan.md) | IT / QA / key users | Automated tests, staging setup, UAT scripts with pass criteria for every component |
| [rollout.md](rollout.md) | IT lead / managers | Phases, pilot groups, go/no-go criteria, rollback |
| [runbook.md](runbook.md) | IT on-call | Health checks, logs, audit trail, key and password rotation, common failures, emergency off switches |
| [security.md](security.md) | IT / security | Data flows, controls, what was reviewed, remaining risks and decisions |
| [backlog.md](backlog.md) | IT lead | Every remaining task with owner role and acceptance criteria, ordered by phase (ready to load into Asana) |
| [training/](training/) | Trainers, staff | One short guide each for CSRs, prepress, estimating and administrators |

Background and the design of each automation: [`../ai-connector-research.md`](../ai-connector-research.md),
section 3. Install and settings reference for the connector: [`../../switch-mcp/README.md`](../../switch-mcp/README.md).

## What was built

Two ways to run the same code:

1. **MCP server** (`enfocus-switch-mcp`). It runs on each staff member's machine and is started by
   Claude Desktop or Claude Code. People ask Claude in plain English, and Claude calls the tools.
   It is read-only unless write access is turned on for that person.
2. **Automation web service** (`enfocus-switch-mcp service`). It runs on one server. Switch flows and
   the web-to-print portal backend call it over HTTP with API keys. There is no LLM in its path: every
   answer comes from deterministic code. It is dry-run by default.

```
Staff ── Claude Desktop/Code ──MCP──► enfocus-switch-mcp ──REST──► Switch + PitStop Server
                                             │  └──read-only SQL──► Pace DB (replica, read-only role)
Switch flows ─┐                              │  └──Pace API (allow-listed statuses, notes)──► Pace
Portal backend┴──HTTPS + API key──► enfocus-switch-mcp service (same code)
Scheduled task ── enfocus-switch-mcp digest --post ──► Teams / Slack webhook
```

## Component readiness

Status meanings:

| Status | Meaning |
|---|---|
| **Ready** | Code and automated tests are done. What's left is live validation with real Switch/PitStop data. |
| **Needs config** | Code and tests are done. A house-specific file (SQL, API endpoints or a map) has to be written before it runs. |
| **Needs integration** | Code is done. Another system (the portal, a Switch flow) has to call it. |
| **Not built** | Designed only; it's in the backlog. |

"Owner" is the role that finishes it: **SW** = Switch admin, **PACE** = Pace admin/DBA,
**WEB** = portal developer, **IT** = infrastructure, **OPS** = department lead (prepress, CSR, estimating).

| # | Component | Status | Left to do | Owner | Settings / files |
|---|---|---|---|---|---|
| — | Connector core: jobs, flows, checkpoints, messages, thumbnails, downloads | Ready | Create the Switch user; run `check` against production Switch | SW, IT | `SWITCH_URL`, `SWITCH_USERNAME`, `SWITCH_PASSWORD` |
| — | Write tools: submit, route, replace, lock, rush | Ready | Validate on a test flow; decide who gets write access | SW, OPS | `SWITCH_ALLOW_WRITE` |
| A1 | Plain-language preflight explanations | Ready | Validate against 20+ real PitStop reports; tune wording and fix owners in `knowledge.py` | OPS (prepress + CSR), IT | — |
| A2 | Client self-serve preflight | Needs integration | Portal backend calls `POST /preflight`; optional full-PitStop submission | WEB, SW | Service `preflight` key |
| A3 | Explanations saved as Pace notes | Needs config | Fill in `add_job_note` in `pace_api.json`; test on Pace test system | PACE | `PACE_API_*`, `PACE_ALLOW_WRITE` |
| A4 | "Where's my job?" | Ready (Switch) / Needs config (Pace) | `job_status` SQL | PACE | `PACE_DB_DSN`, `PACE_QUERIES_FILE` |
| A5 | Proof approval (Switch route + Pace status + note) | Needs config | Pace API operations; exact status name; portal "Approve" button | PACE, WEB, OPS | `SWITCH_APPROVE_CONNECTION`, `PACE_PROOF_APPROVED_STATUS` |
| B1 | File vs. job ticket check | Ready / Needs config for one-call Pace lookup | `job_spec` SQL; tune the size tolerance | PACE, OPS | `PACE_QUERIES_FILE` |
| B2 | Auto-fix routing plan, and automatic routing | Needs config | Build the Switch auto-fix branch; write the map; enable auto-route after UAT | SW, OPS | `SWITCH_AUTOFIX_MAP`, `SERVICE_AUTO_ROUTE` |
| B3 | Morning digest | Ready | Schedule it; create the Teams webhook | IT | `DIGEST_WEBHOOK_URL` |
| B4 | Match files/emails to jobs | Ready | Set Drummond's job number patterns; intake flow calls `/switch/match-job` | SW, OPS | `PACE_JOB_NUMBER_PATTERNS` |
| C1 | Estimate draft from a PDF | Needs config | Real Pace item-template field names; house products | PACE, OPS (estimating) | `PACE_ITEM_TEMPLATE_MAP` |
| C2 | RFQ completeness check | Ready | Tune required fields per product; connect the estimating mailbox (M365 connector) | OPS (estimating), IT | — |
| C3 | Similar-job lookup | Needs config | `similar_jobs` SQL | PACE | `PACE_QUERIES_FILE` |
| D1 | Switch → Pace status sync | Needs config + integration | Event map; Pace API operations; HTTP request elements in Switch flows | SW, PACE | `SERVICE_STATUS_MAP` |
| D1b | Pace → Switch (hold/cancel locks the job) | Not built | Needs a Pace-side trigger | PACE, SW | — |
| D2 | Preflight analytics | Ready | Choose retention; share a monthly report | OPS, IT | `ANALYTICS_DB` |
| D2b | Checkpoint time and proof-round analytics | Not built | Switch Reporting module queries | SW | — |
| D3 | Customer-specific rules | Ready | CSRs collect the standing agreements into the rules file | OPS (CSR) | `CUSTOMER_RULES` |
| — | Automation web service (auth, limits, audit, dry run) | Ready | Deploy; reverse proxy with TLS; create keys | IT | `SERVICE_*`, `AUDIT_LOG` |

## Safety defaults (what's on and off out of the box)

| Capability | Default | Turned on by |
|---|---|---|
| Change anything in Switch from Claude | **Off** | `SWITCH_ALLOW_WRITE=true` (per person) |
| Start/stop flows from Claude | **Off** | `SWITCH_ALLOW_FLOW_CONTROL=true` |
| Any change made by the service (route, Pace status) | **Planned only** (dry run) | `SERVICE_DRY_RUN=false` |
| Automatic routing to the auto-fix branch | **Off** | `SERVICE_AUTO_ROUTE=true` + event `auto_route: true` |
| Writes to Pace | **Off** (reported as manual steps) | `PACE_API_CONFIG` + `PACE_ALLOW_WRITE=true` + `PACE_ALLOWED_STATUSES` |
| Pace database | Read-only role, read-only transaction, SELECT only | — (never writable) |
| Customer-facing messages | Drafted, never sent automatically | — |
| Pricing | Never | — |

## Tracking

The backlog in [backlog.md](backlog.md) is organised as Asana-ready sections and tasks. Each task has
an owner role and acceptance criteria. When the team is ready to start, load it into an Asana project,
one section per phase. From then on, track progress there rather than in these files. Keep these
documents as the reference; update them when the design changes.

## Verification done before handoff

- **Automated tests:** 150+ unit and end-to-end tests, run in CI on Linux and macOS (Python 3.10 and
  3.12). Windows runs as an informational job. The end-to-end tests run the real command over stdio
  against a fake Switch that implements the real login encryption.
- **Container build:** the service image is built and smoke-tested in CI (health, authentication, one
  endpoint).
- **Real Claude session:** a real Claude Code session used the tools end to end against the fake Switch.
- **Security reviews:** independent reviews of the connector and of the automation service. Findings were
  fixed and are covered by tests (see [security.md](security.md)).
- **Not yet done:** anything against the **real** Switch, PitStop, Pace or portal. That is phase 1 of
  [rollout.md](rollout.md).

# AI connector for Enfocus Switch: research notes and roadmap

This document explains what an AI assistant can do safely with Enfocus Switch, why it's
built as an MCP server, and where it could go next. The first version is in
[`switch-mcp/`](../switch-mcp).

## 1. What Switch exposes

**Switch Web Services REST API** (port 51088 by default; reference:
[v24 API docs](https://www.enfocus.com/manuals/DeveloperGuide/WebServices/24/index.html)).

| Area | Endpoints | Useful for |
|---|---|---|
| Auth | `POST /login` (RSA-encrypted password, `!@$` + base64), `GET /logout`, `GET /api/v1/ping` | Session management |
| Flows | `GET /api/v1/flows`, `PUT /flows/:id?action=start\|stop`, flow annotations CRUD | Status, start/stop, documentation |
| Submit points | `GET /api/v1/submitpoints[/:flow-object]` including metadata field definitions (enum, regex, dependencies) | Structured job intake |
| Jobs | `POST /api/v1/job` (multipart submit), `GET /api/v1/jobs` (rich JSON filter, sort, `lastUpdated` polling), `GET /job/:id` (download), `GET /job/report/:id`, `GET /job/metadata`, `PUT /job/:id?action=route\|replace\|lock\|unlock` | Tracking, approvals, proof replacement |
| Processing | `PUT /api/v1/processingjob/:processingId?action=rush\|unrush` | Priority |
| Messages | `GET /api/v1/messages` (period, type, flow, job filters), saved message filters | Troubleshooting |
| Reporting | `POST /api/v1/graphql` (processing jobs; statistics with Reporting module) | Dashboards, SLA analysis |
| Other | `GET /api/v1/thumbnails`, job filters CRUD, user groups | Previews, saved views |

Practical notes:

- Production use needs the **Web Services module**. Without it Switch limits API calls
  ("API limit reached").
- Links returned for downloads and reports embed Switch's own host name (often `127.0.0.1`).
  The connector rewrites them to the configured URL.
- The **checkpoint** is where AI help pays off most. A job waiting there has a status of
  `alert`. The job carries `outConnections` (e.g. Approve/Reject), optional checkpoint
  metadata, and optionally a viewable report.

**Other integration points (not used yet):**

- *Switch Scripter / scripting API (Node.js, TypeScript).* A custom flow element could call an
  LLM inside the flow, e.g. classify incoming files or write the customer message directly
  into job metadata. This runs inside Switch, not from the assistant.
- *PitStop Server CLI / PitStop Library Container.* These run preflight directly and give XML
  or JSON reports. This path suits a stand-alone "preflight as a service".
- *Switch HTTP Request / Webhook elements.* Switch can push events (job arrived in checkpoint)
  to a small service. That service can pre-compute the plain-language report so it's ready
  before anyone asks.

## 2. PitStop report formats (for plain-language explanations)

| Format | Shape | Notes |
|---|---|---|
| XML v2 (PitStop 10.1+) | `EnfocusReport/Report/PreflightResult/PreflightResultEntry[@level]` | Counts are attributes on `PreflightResult`. The message is in `PreflightResultEntryMessage/Message` |
| XML v3 (PitStop 2018+) | `EnfocusReport/PreflightReport/{Errors,Warnings,Fixes,Signoffs,...}/PreflightReportItem` | Items have `Message`, `StringContext/BaseString`, and `Location@page` (0-based) |
| JSON (PitStop 2023+) | `preflightReport.{errors,warnings,fixes}.preflightReportItem[]` plus `processInfo`, `generalDocInfo`, `pageBoxInfo`, ... | Richest metadata |
| PDF | Human-readable report | Handled by text extraction. Less precise |

In every format, messages end with an occurrence suffix such as `(3x on pages 1, 2)`. The
parser splits that suffix into counts and pages. Enfocus does not publish an XSD for the
report. The parser was written against real fragments and matches element names without
namespaces. **Next step: validate it against real reports from our PitStop Server** and add
them as test fixtures.

**Recommendation for the flows:** have each PitStop Server element write an **XML v3 (or
JSON) report** alongside the PDF report, and attach it to the job when it reaches the
checkpoint.

## 3. Automation opportunities (ranked by value ÷ risk)

1. **Plain-language preflight explanations**. *(built)*
   - A deterministic explanation for each audience: customer, CSR and prepress.
   - An overall verdict: `ready`, `prepress_can_fix` or `needs_customer`.
   - For each issue, who owns the fix and whether it can be fixed automatically.
   - The assistant turns this into an email. Nothing is sent automatically.
2. **CSR self-service job status**, e.g. "where is ORD1001, and what do I tell the customer?" *(built)*
3. **Morning prepress triage**. *(built as a prompt)*
   - What is waiting in checkpoints, oldest first.
   - The recurring errors from the message log, grouped by flow and element.
4. **Intake quick-check** at order entry. *(built)*
   - Checks trim size against the order, bleed, fonts, RGB images and security.
   - Takes seconds, so problems are caught before the job enters Switch.
5. **Guided approvals**. *(built, opt-in)*
   - Route a checkpoint job by connection name.
   - Metadata is validated and optimistic concurrency uses `updated`.
   - Replace a job with the customer's corrected file.
6. **Structured submission**. *(built, opt-in)*
   - Submit to a submit point.
   - Metadata is validated against the field definitions (required fields, enum values, regex).
7. **Auto-fix recommendation → action list mapping**. *(next)*
   - Map each `auto_fixable` category to our actual PitStop Action Lists or Switch routes.
   - The assistant can then say "route to 'Auto-fix bleed'" instead of "prepress can fix".
8. **Webhook-driven pre-computation**. *(next)*
   - When a job hits a checkpoint, Switch calls a small service.
   - The service stores the customer-ready text in job metadata, so the web portal and email
     templates can use it without any AI in the loop.
9. **SLA and bottleneck analytics via GraphQL / Reporting**. *(next)*
   - Time-in-checkpoint by CSR or customer.
   - Error-rate trends per customer, to target training ("send them our PDF export guide").
10. **Customer-specific rules**. *(later)*
    - Per-customer tolerances, e.g. a customer who always approves low-res at their own risk.
    - These come from the MIS and feed into the verdict.

## 4. Why MCP

- One connector works with Claude Desktop, Claude Code and any MCP-capable client. Later it
  can also run behind a web app or a Teams/Slack bot.
- Tools carry read-only / destructive annotations, so clients can require confirmation
  before an approval.
- Write tools are hidden unless explicitly enabled. By default the connector is read-only.

## 5. Safety

- Use a dedicated Switch user for the connector, with least privilege. Its actions show up
  in Switch under that name.
- Routing always passes the job's `updated` timestamp, so Switch rejects stale decisions.
- Local file access is limited to the allow-listed folders.
- Customer-facing text is drafted, never sent automatically. A human sends it.
- Credentials come from environment variables and are never logged. The password is
  RSA-encrypted before it goes over the wire, as Switch requires. Put the Switch API behind
  HTTPS when it is reached across the network.

## 6. Open questions for the shop

- Which Switch version and modules are licensed (Web Services, Reporting, PitStop Server)?
- What do our checkpoint connections and metadata fields look like? (Needed to tune the
  route and metadata helpers and the prompts.)
- Which fixes do we do in-house for free, and which do we charge for? (This sets
  `fix_owner` and the wording in `knowledge.py`.)
- Where should customer-facing text go: email, the web-to-print portal, or MIS notes?

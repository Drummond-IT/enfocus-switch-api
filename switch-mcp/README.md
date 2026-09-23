# Enfocus Switch MCP connector

An [MCP](https://modelcontextprotocol.io) server that lets an AI assistant (Claude Desktop,
Claude Code, or any MCP client) work with an **Enfocus Switch** server through the
Switch Web Services REST API. It also includes an offline preflight explainer and a quick
PDF intake check that work without Switch.

What people can ask it:

- *CSR:* "Where is order ORD1001? What do I tell the customer?"
- *CSR:* "Explain the preflight report on job 604f4d59… in plain English for the customer."
- *Prepress:* "What's waiting in checkpoints, oldest first? Which ones can we fix without the customer?"
- *Prepress:* "What broke overnight?" (groups the Switch message log by flow/element)
- *Anyone:* "Is `~/Uploads/ACME_flyer.pdf` OK for a 5.5 x 8.5 flyer?" (local PDF check)
- *With write access:* "Approve the proof for ORD1001, approved by Sam" / "Submit this PDF to the
  'Upload PDF' submit point with order number ORD1005, paper Matte."

## Tools

| Tool | What it does | Needs |
|---|---|---|
| `switch_status` | Connectivity, logged-in user's Switch permissions, connector safety settings | |
| `production_snapshot` | Job counts per status/flow, what's waiting in which checkpoint, oldest waits | |
| `list_flows` | Flows with status, groups, stages | |
| `list_submit_points` | Submit points and the metadata fields they require (with enum options) | |
| `find_jobs` | Search by name/order no., status, flow, checkpoint, user, recency | |
| `jobs_needing_attention` | Checkpoint jobs (status `alert`), oldest first, with routing options | |
| `get_job` | Full job detail including checkpoint metadata fields | |
| `get_job_thumbnail` | First-page preview image | |
| `download_job` | Save job file (or zipped folder) to the download folder | |
| `explain_job_report` | Fetch a checkpoint job's preflight report, verdict + plain-language write-up | |
| `explain_preflight_report` | Same, for pasted report text or a local XML/TXT/PDF report | |
| `quick_check_pdf` | Local check: trim size, bleed, unembedded fonts, RGB images, encryption, annotations | |
| `recent_messages` | Switch message log with filters | |
| `problem_summary` | Recurring errors/warnings grouped by flow + element | |
| `graphql_query` | Read-only Switch GraphQL (processing jobs, Reporting stats) | |
| `submit_job` | Submit a local file to a submit point, validating metadata | `SWITCH_ALLOW_WRITE` |
| `route_job` | Send a checkpoint job along a named connection (approve/reject) | `SWITCH_ALLOW_WRITE` |
| `replace_job` | Replace a checkpoint job's file (e.g. customer's corrected PDF) | `SWITCH_ALLOW_WRITE` |
| `set_job_lock` | Lock/unlock a checkpoint job | `SWITCH_ALLOW_WRITE` |
| `rush_job` | Rush/unrush a job | `SWITCH_ALLOW_WRITE` |
| `set_flow_running` | Start/stop a flow | `SWITCH_ALLOW_FLOW_CONTROL` |

Prompts: `customer_preflight_email`, `csr_order_status`, `prepress_triage`.

### Plain-language preflight

`explain_*` tools parse PitStop reports (XML v2 and v3, JSON from PitStop 2023+, or the text of a PDF report;
namespaces ignored) or pasted text, group findings into ~18 print-shop categories
(low resolution, RGB, missing fonts, bleed, trim size, safety margin, spot colors, TAC,
hairlines, small text, overprint, registration black, transparency, PDF/X, security,
page count, annotations/layers, compression) and return:

- a **verdict**: `ready`, `prepress_can_fix` or `needs_customer`;
- per issue: pages, occurrences, **who owns the fix** and whether it's typically
  **auto-fixable** by a PitStop Action List;
- a deterministic **write-up** for `customer`, `csr` or `prepress`, which the assistant can
  then adapt to tone.

Wording and ownership live in [`knowledge.py`](src/switch_mcp/knowledge.py); edit them to
match your shop's policies (e.g. whether you charge for bleed generation).

For best results, configure the PitStop Server element in your flow to output an **XML v3 (or JSON)**
report alongside the PDF report and attach it to the checkpoint. PDF reports are
supported by text extraction but are less precise.

## Setup

Requirements: Python 3.10+, a Switch server with the **Switch Web Services** (and for
production use the **Web Services module**; without it Switch caps API usage with
"API limit reached"). Create a dedicated Switch user for the connector and give it only the
permissions it needs (Switch Users pane).

```bash
cd switch-mcp
pip install -e .            # or: uv pip install -e .
```

### Configuration (environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `SWITCH_URL` | `http://127.0.0.1:51088` | Switch Web Services address |
| `SWITCH_USERNAME` / `SWITCH_PASSWORD` | | Switch user (password is RSA-encrypted before sending, as Switch requires) |
| `SWITCH_ALLOW_WRITE` | `false` | Enable submit/route/replace/lock/rush tools |
| `SWITCH_ALLOW_FLOW_CONTROL` | `false` | Enable starting/stopping flows |
| `SWITCH_UPLOAD_DIRS` | *(none)* | `:`-separated folders the connector may read files from (`;` on Windows) |
| `SWITCH_DOWNLOAD_DIR` | `./switch-downloads` | Where jobs and reports are saved |
| `SWITCH_LANG` | `enUS` | Language of Switch error messages |
| `SWITCH_VERIFY_TLS` | `true` | Set `false` only for self-signed test servers |
| `SWITCH_PUBLIC_KEY_PATH` | bundled | Override the Enfocus RSA public key |
| `SWITCH_TIMEOUT` | `60` | Seconds |

### Claude Desktop / Claude Code

```json
{
  "mcpServers": {
    "enfocus-switch": {
      "command": "enfocus-switch-mcp",
      "env": {
        "SWITCH_URL": "http://switch.local:51088",
        "SWITCH_USERNAME": "ai-connector",
        "SWITCH_PASSWORD": "…",
        "SWITCH_UPLOAD_DIRS": "/Volumes/Jobs/Incoming",
        "SWITCH_DOWNLOAD_DIR": "/Volumes/Jobs/AI-downloads"
      }
    }
  }
}
```

Claude Code: `claude mcp add enfocus-switch -e SWITCH_URL=… -e SWITCH_USERNAME=… -e SWITCH_PASSWORD=… -- enfocus-switch-mcp`

## Safety model

- Read-only by default; write tools aren't even listed to the model until enabled.
- Routing uses the job's `updated` timestamp so Switch refuses the route if someone else
  changed the job in the meantime.
- Metadata is validated against the submit point / checkpoint definition (required fields,
  enum options, regex format) before anything is sent.
- File access is limited to `SWITCH_UPLOAD_DIRS` (+ the download folder for reading reports).
- Tool annotations mark read-only vs destructive tools so MCP clients can ask for confirmation.

## Development

```bash
pip install -e ".[dev]"
pytest          # uses an in-memory fake Switch server; no real Switch needed
ruff check src tests
```

API reference used: [Switch Web Services REST API](https://www.enfocus.com/manuals/DeveloperGuide/WebServices/24/index.html).

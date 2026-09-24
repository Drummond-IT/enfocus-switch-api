# Security summary

## Data flows

| Flow | Data | Protection |
|---|---|---|
| Staff machine → Switch Web Services | Job names, metadata, reports, files the user chooses | Switch login per user; password RSA-encrypted as Switch requires; https recommended; write tools off by default |
| Staff machine → Claude (Anthropic) | What the user asks, and the tool results Claude needs: job details, report text, PDF facts | Covered by Drummond's Claude agreement. The connector sends only what a tool returns, never whole files. Local file access is limited to allow-listed folders |
| Service ← portal / Switch | Uploaded PDFs, job IDs, job numbers, customer names | Scoped API keys (stored hashed), rate limits, size limits, internal network + TLS via reverse proxy. **No LLM in the service path**: nothing goes to Anthropic |
| Connector/service → Pace DB | SQL results (job specs, statuses) | Read-only role, read-only transaction, SELECT/WITH only, statement timeout |
| Connector/service → Pace API | Status changes and notes | Off by default; only configured operations; only allow-listed statuses; values escaped for JSON/XML and URL-encoded in paths; no redirects followed; every attempt audited |
| Service → disk | Audit log, analytics DB, downloads | Files created as owner-only (0600) where the OS supports it. **Analytics and audit logs hold customer names and job numbers, but no file contents** |

## Controls

**Authentication and authorisation**
- **Switch:** each deployment uses its own Switch user, with only the flows and checkpoints it needs.
  Everything it does is attributed to that user in Switch.
- **Service:**
  - API keys are random (256-bit), shown once, and stored as SHA-256 hashes.
  - Keys are compared in constant time.
  - Each key has scopes. The portal key can't call Switch or proof endpoints unless given those scopes.
- **Customer isolation:** `/preflight` only compares against a Pace job when that job's customer matches the
  customer the portal names. The portal's own login decides the customer.

**Safe defaults**
- Claude: read-only; write and flow-control tools don't exist until enabled.
- Service: dry run; auto-routing off; Pace writes off; listens on 127.0.0.1.
- Customer-facing text is always drafted for a person to send, and nothing is ever priced.

**Input handling**
- IDs are strictly validated before they reach a URL or path.
- Uploads are capped by size and checked for PDF magic.
- Parsing runs in worker threads with a timeout and limited concurrency.
- XML is parsed with `defusedxml` (no entities or DTDs).
- Regexes and page ranges are bounded.
- Network (UNC) paths are refused before any filesystem access, so Windows doesn't leak NTLM hashes.
- Local files are opened without following symlinks and must be regular files inside allowed folders.
- Downloads are written atomically.

**Secrets**
- Secrets live in config files or password files readable only by the owner. `check` warns if the config
  file is readable by others.
- Passwords are never printed or logged. The httpx request logging (which could include Switch download
  links with session keys) is silenced.
- Keys and passwords never go into the audit log.

**Hardening**
- **systemd unit:** no new privileges, read-only system, private tmp, no capabilities, one writable data
  folder.
- **Container:** runs as a non-root user.
- **Service:** no server or date headers, keep-alive limits, a concurrency limit.

## Reviews done

1. **Connector review (PR #1).** Found and fixed:
   - regex backtracking and page-range expansion (DoS)
   - UNC path NTLM leak
   - symlink swaps
   - missing size caps

   Each has a regression test.
2. **Automation service review (this PR).** An independent review of `service.py`, the Pace writer, the
   audit log, the CLI and the deployment files. Findings and fixes are listed in the pull request
   description and covered by `test_service.py` / `test_pace_writer.py`.

## Remaining risks and decisions for Drummond

| Risk | Current mitigation | Decision needed |
|---|---|---|
| Claude sees customer job data | Drummond's Claude agreement; tools return summaries, not files | Confirm with the data owner that this matches the agreement and customer contracts |
| The service's rate limit and key store are per process | One service instance; keys reloaded on restart | If you run several instances behind a load balancer, move rate limiting to the proxy |
| The audit log is local and not tamper-evident | Owner-only file, server backups | Ship it to central logging/SIEM if required |
| The Pace API account can probably do more than status and notes | Only configured operations are ever called; allow-list | Restrict the Pace account's rights if Pace allows it |
| Switch HTTP request elements may not support custom headers in your version | Documented alternatives (script element, proxy rule) | Choose one during phase 4 and record it in the runbook |
| Portal integration security depends on the portal | Deployment guide: server-to-server only, key in the portal's secret store | Portal developer confirms in UAT A2 |
| Windows test coverage is informational in CI | The tests pass on Linux/macOS; Windows paths are handled in code | Run the suite on the Windows hosts before go-live (test plan §1) |
| Example dependencies in the older PHP sample (`composer.lock`: Guzzle 6.3.3, psr7 1.5.2) have known CVEs | The PHP sample isn't part of this deployment | Upgrade it or delete it (backlog) |

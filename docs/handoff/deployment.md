# Deployment guide

There are three things to deploy, in this order:

1. **Prerequisites** in Switch (and Pace, when you get to Pace).
2. **The connector for staff**: the MCP server on each person's machine.
3. **The automation service**: one server, for Switch flows and the portal.

Commands are for PowerShell on Windows and bash on macOS/Linux. Paths in `<angle brackets>` are yours to choose.

---

## 1. Prerequisites

### Switch (Switch admin)

1. **Switch Web Services** must be licensed and running. Default port: 51088.
2. Create **one Switch user per purpose** in Switch Designer > Users, so actions are traceable in Switch:
   - `ai-connector`: shared by staff using Claude. It can be one user, or one per department if you
     want Switch's own logs to separate them.
   - `automation-service`: used only by the automation service.
3. Grant each user only the flows and checkpoints it needs. For write actions, the user needs access to
   the relevant checkpoints and submit points.
4. If staff machines reach Switch across the network, put the Web Services behind **https** (for example
   with a reverse proxy). Plain http is acceptable on the Switch server itself (localhost) only.
5. Firewall: allow staff machines and the service host to reach the Web Services port.

### Pace (Pace admin/DBA). Needed from phase 2 onward.

1. **Read access:** a PostgreSQL **read-only role** on a replica if you have one, able to SELECT only the
   tables that the three queries in `pace_queries.example.sql` need.
2. **Write access (phase 3):** a Pace API account that can update job status and add job notes, and nothing
   more if Pace allows that.
3. A **Pace test system** for validating writes before they go to production.

---

## 2. The connector for staff (MCP server)

Follow [`switch-mcp/README.md`](../../switch-mcp/README.md), sections 1–5. It takes about 10 minutes per
machine once the Switch user exists. In short:

```bash
# once per machine
uv tool install "<path-to-repo>/switch-mcp"          # add [pace] for Pace: "<path>/switch-mcp[pace]"
# config: copy switch-mcp/config.env.example to ~/.config/enfocus-switch-mcp/config.env, fill it in
enfocus-switch-mcp check                              # must end with "All checks passed"
# then add it to Claude Desktop or Claude Code (README section 5)
```

**Rolling out to many machines:**
- Keep one reviewed `config.env` per role, e.g. `csr.env` and `prepress.env`. They differ in
  `SWITCH_ALLOW_WRITE` and `SWITCH_UPLOAD_DIRS`.
- Distribute them with your usual tooling (Intune, GPO, a script). On macOS/Linux, `chmod 600` the file.
- Prefer `SWITCH_PASSWORD_FILE` pointing at a file only that user can read over putting the password in
  `config.env`.
- Write access (`SWITCH_ALLOW_WRITE=true`) goes only to people whose job includes routing or submitting,
  after they've completed the prepress training ([training/prepress.md](training/prepress.md)).

**Updating:** `git pull`, then `uv tool install --reinstall "<path>/switch-mcp"`. Staff restart Claude.

---

## 3. The automation service

Run it on one always-on machine that can reach Switch (and Pace). The Switch server itself is fine.
It needs Python 3.10+ (or Docker) and very little CPU/RAM. Budget 1 GB of RAM if the portal uploads large PDFs.

### 3.1 Configuration files

Put these in one folder readable only by the service account: `/etc/enfocus-switch-mcp/` on Linux,
`C:\ProgramData\EnfocusSwitchMCP\` on Windows.

| File | From | Notes |
|---|---|---|
| `config.env` | `config.env.example` | Switch connection (the `automation-service` user), Pace, `SERVICE_*`, `AUDIT_LOG`, `ANALYTICS_DB` |
| `service_keys.json` | created by `service-key` | Hashes of the API keys, not the keys themselves |
| `service_status_map.json` | `service_status_map.example.json` | What each Switch event does |
| `autofix_map.json` | `autofix_map.example.json` | Only if using auto-fix routing |
| `customer_rules.json` | `customer_rules.example.json` | Optional |
| `pace_queries.sql`, `pace_api.json` | the `.example` files | Pace phases only |

Minimum `config.env` for the service:

```ini
SWITCH_URL=https://switch.drummond.local:51088
SWITCH_USERNAME=automation-service
SWITCH_PASSWORD_FILE=/etc/enfocus-switch-mcp/switch-password.txt
SWITCH_DOWNLOAD_DIR=/var/lib/enfocus-switch-mcp/downloads
SERVICE_KEYS_FILE=/etc/enfocus-switch-mcp/service_keys.json
SERVICE_STATUS_MAP=/etc/enfocus-switch-mcp/service_status_map.json
SERVICE_DRY_RUN=true
AUDIT_LOG=/var/lib/enfocus-switch-mcp/audit.jsonl
ANALYTICS_DB=/var/lib/enfocus-switch-mcp/analytics.sqlite
```

Keep `SERVICE_DRY_RUN=true` until UAT for that component is signed off ([test-plan.md](test-plan.md)).

### 3.2 API keys

Create one key per caller, with only the scopes it needs:

| Caller | Name | Scopes |
|---|---|---|
| Portal backend | `portal` | `preflight` (add `proof_approve` when the portal's Approve button is wired) |
| Switch flows | `switch` | `switch_events`, `match_job` |
| Quote request form | `rfq-form` | `rfq` |

```bash
enfocus-switch-mcp service-key --env-file /etc/enfocus-switch-mcp/config.env --name portal --scopes preflight
```

This prints the key **once** and saves only its hash in `SERVICE_KEYS_FILE`. Put the key in the caller's
secret store; never commit it or email it. Restart the service to load new keys. To revoke a key, delete
its entry from the file and restart.

### 3.3 Install: Windows (NSSM service)

1. Create a local or domain account for the service, e.g. `svc-switchmcp`. Give it read access to the
   config folder and modify access to the data folder only.
2. Install the tool, then check the configuration:
   ```powershell
   uv tool install "<repo>\switch-mcp[pace]"
   enfocus-switch-mcp check --env-file C:\ProgramData\EnfocusSwitchMCP\config.env
   ```
3. Download NSSM (https://nssm.cc) and run, elevated:
   ```powershell
   .\switch-mcp\deploy\windows\install-service.ps1 -Exe "<path>\enfocus-switch-mcp.exe" `
       -ConfigFile C:\ProgramData\EnfocusSwitchMCP\config.env -ServiceUser ".\svc-switchmcp"
   ```
   The script installs the service, starts it, and registers the weekday 6:30 digest task. Add `-NoDigest`
   to skip the digest.
4. Test: `Invoke-RestMethod http://127.0.0.1:8765/health` should return `ok: True`.

### 3.4 Install: Linux (systemd)

```bash
sudo useradd --system --home /var/lib/enfocus-switch-mcp --shell /usr/sbin/nologin switchmcp
sudo install -d -o switchmcp -m 700 /var/lib/enfocus-switch-mcp
sudo install -d -m 750 -g switchmcp /etc/enfocus-switch-mcp        # config files: 640 root:switchmcp
sudo python3 -m venv /opt/enfocus-switch-mcp
sudo /opt/enfocus-switch-mcp/bin/pip install "<repo>/switch-mcp[pace]"
sudo cp <repo>/switch-mcp/deploy/systemd/*.service <repo>/switch-mcp/deploy/systemd/*.timer /etc/systemd/system/
sudo -u switchmcp SWITCH_ENV_FILE=/etc/enfocus-switch-mcp/config.env /opt/enfocus-switch-mcp/bin/enfocus-switch-mcp check
sudo systemctl daemon-reload
sudo systemctl enable --now enfocus-switch-service enfocus-switch-digest.timer
curl -s http://127.0.0.1:8765/health
```

### 3.5 Install: Docker

```bash
cd <repo>/switch-mcp
docker build -t enfocus-switch-mcp:$(git rev-parse --short HEAD) .
docker run -d --name enfocus-switch-service --restart unless-stopped \
  -p 127.0.0.1:8765:8765 \
  --env-file /etc/enfocus-switch-mcp/config.env \
  -v /etc/enfocus-switch-mcp:/etc/enfocus-switch-mcp:ro \
  -v enfocus-switch-data:/data \
  enfocus-switch-mcp:$(git rev-parse --short HEAD)
```

Inside the container, the image defaults `AUDIT_LOG`, `ANALYTICS_DB` and `SWITCH_DOWNLOAD_DIR` to `/data`.
In `config.env`, use the in-container paths (`/etc/enfocus-switch-mcp/...`). Publish the port on
127.0.0.1 only, and put the reverse proxy in front.

### 3.6 HTTPS and exposure

The service listens on **127.0.0.1** by default. If Switch runs on the same machine, you can leave it there.
For anything else, put a **reverse proxy with TLS** in front: IIS with ARR/URL Rewrite, nginx, or your
load balancer. Expose it **only to the internal network**; the portal backend calls it server to server.
Example nginx:

```nginx
server {
  listen 443 ssl;
  server_name switch-automation.drummond.local;
  ssl_certificate     /etc/ssl/drummond/switch-automation.crt;
  ssl_certificate_key /etc/ssl/drummond/switch-automation.key;
  client_max_body_size 100m;          # match SERVICE_MAX_UPLOAD_MB
  allow 10.0.0.0/8; deny all;         # internal callers only
  location / { proxy_pass http://127.0.0.1:8765; proxy_read_timeout 120s; }
}
```

Alternatively set `SERVICE_TLS_CERT` and `SERVICE_TLS_KEY` to serve https directly.

---

## 4. Wiring callers to the service

Every request needs the header `X-API-Key: <key>` (or `Authorization: Bearer <key>`). Bodies are JSON,
except `/preflight`, which takes the PDF itself. Errors come back as `{"error": "..."}` with a meaningful
HTTP status:

| Status | Meaning |
|---|---|
| 400 | Bad input |
| 401 | No or wrong key |
| 403 | The key lacks the scope |
| 404 | Unknown job |
| 409 | Job not in a state for this action |
| 413 | Upload too large |
| 415 | Wrong content type |
| 422 | File unreadable |
| 429 | Rate limit |
| 502 | Switch unreachable |

### 4.1 Switch flows → `POST /switch/events` (D1, B2, A3)

Add an **HTTP request** element (Switch configurator) at each milestone, after the preflight checkpoint,
when the proof is sent, and so on. Settings:

| Setting | Value |
|---|---|
| URL | `https://switch-automation.drummond.local/switch/events` |
| Method | POST |
| Header | `X-API-Key: <switch key>`, `Content-Type: application/json` |
| Body | `{"event": "proof_sent", "job_id": "<job id>", "pace_job_number": "[Metadata.Text:Path=\"...\"]"}` |

- **`event`** must be a name in `service_status_map.json`.
- **`job_id`** is the Switch Web Services job ID. **Verify in UAT which Switch variable gives it in your Switch
  version** (compare with `find_jobs` in Claude).
- **`pace_job_number`** is optional. Without it, the service looks for the number in the job name, using
  `PACE_JOB_NUMBER_PATTERNS`.

If the HTTP request element can't set a header in your Switch version, use a script element instead, or
put the service behind the proxy with a path-specific rule. Record which you chose in the runbook.

The response lists each step with a status: `planned`, `done`, `manual`, `skipped` or `failed`. With
`explain: true`, it also returns the CSR and customer write-ups; save them into job metadata if you want
them downstream.

### 4.2 Intake flow → `POST /switch/match-job` (B4)

`{"text": "<file name or email subject>"}` returns `{"job_number": "123456" | null, "ambiguous": bool,
"candidates": [...]}`. Route `null`/ambiguous results to a person.

### 4.3 Portal backend → `POST /preflight` (A2)

```
POST /preflight?name=flyer.pdf&customer=ACME%20Corp&trim=8.5x11&colors=4/4&job_number=123456
Content-Type: application/pdf
X-API-Key: <portal key>
<the PDF bytes>
```

- All query parameters are optional.
- Show the response's `message` to the customer, and `ticket.issues` when present.
- `job_number` is only compared when the Pace job belongs to `customer`.
- **Call it from the portal's server, never from the browser**: the key must stay secret. The portal's own
  login decides which `customer` to send.

### 4.4 Portal "Approve proof" → `POST /proof/approve` (A5)

`{"job_id": "...", "approved_by": "customer name or email", "pace_job_number": "123456"}`.

- It routes the Switch job via `SWITCH_APPROVE_CONNECTION`, sets `PACE_PROOF_APPROVED_STATUS` and adds a
  Pace note.
- It returns 409 if the job isn't waiting in that checkpoint.
- Pass `"dry_run": true` to preview.

### 4.5 Quote request form → `POST /rfq/validate` (C2)

Send the form fields: `product`, `quantity` ("500, 1000"), `trim`, `pages`, `colors`, `stock`, `binding`,
`folding`, `due_date` (YYYY-MM-DD), `delivery`, `artwork`. The response gives `ready_to_quote`, `missing`,
`problems` and `questions_for_customer`.

---

## 5. After deployment

- Add `/health` to monitoring (see [runbook.md](runbook.md)).
- Back up `analytics.sqlite` and `audit.jsonl` with the server's normal backups.
- Record in the runbook: the host, the keys issued (names only), which Switch flows call the service, and
  who can change the config.

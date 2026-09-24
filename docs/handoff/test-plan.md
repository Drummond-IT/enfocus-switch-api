# Test and UAT plan

Three layers:

1. **Automated tests.** These already pass in CI and must keep passing.
2. **Staging validation** by IT against a test Switch flow, a Pace test system and sample files.
3. **User acceptance (UAT)** by the people who will use each component, with the pass criteria below.

A component goes live only when its UAT is signed off (see [rollout.md](rollout.md)).

---

## 1. Automated tests

```bash
cd switch-mcp
uv run --extra dev ruff check src tests
uv run --extra dev pytest -q
```

CI (`.github/workflows/switch-mcp.yml`) runs these on every pull request:
- Linux and macOS, Python 3.10 and 3.12.
- An informational Windows job.
- A Docker build with a smoke test.

What the suite covers:

| Area | Tests |
|---|---|
| Switch client and MCP tools | `test_client.py`, `test_server.py`, `test_e2e.py` (real stdio process against a fake Switch over HTTP, with real RSA login) |
| Preflight parsing and write-ups | `test_preflight.py` (XML v2/v3, JSON, text; hostile inputs: entity bombs, huge page ranges) |
| Automations | `test_automation.py` (ticket check, estimate draft, auto-fix plan, job matching, digest, proof approval, Pace read gateway) |
| Pace writes | `test_pace_writer.py` (JSON/XML escaping, allow-list, off by default, HTTP errors, audit) |
| Customer rules, analytics, RFQ | `test_customer_rules.py`, `test_analytics.py`, `test_rfq.py` |
| Automation service | `test_service.py` (auth, scopes, rate limit, size limits, every endpoint, dry run, cross-customer isolation) |

**Before live testing:** run the suite on the target OS, especially **Windows** if the service or staff
machines run Windows. Record the result in the Asana task.

---

## 2. Staging setup

Set up once; it's reused by every UAT below.

| Item | Details |
|---|---|
| Switch | A **test flow** that copies the production preflight flow. It has: a submit point, a PitStop preflight, a checkpoint with `Approve` / `Reject` (and later `Auto-fix`) connections, and a "customer" metadata field |
| Switch users | `ai-connector-test` (write access to the test flow only) and `automation-service-test` |
| Sample files | 25+ real customer PDFs with known problems (see the file set below), plus their PitStop reports |
| Pace | Read: the replica/read-only role. Write: the **Pace test system** only |
| Service | A test instance on a non-production port with `SERVICE_DRY_RUN=true`, then `false` for write tests |
| Claude | 2–3 test machines with the connector installed: one read-only, one with write access |

**Sample file set** (collect from prepress; strip customer names if they're sensitive):
- clean, print-ready files
- missing bleed
- 3 mm bleed
- low-res images
- RGB images
- fonts not embedded
- spot colors not on the ticket
- wrong trim size: one proportional, one not
- wrong page count
- a color file on a 1/1 ticket
- a saddle-stitch job with 22 pages
- an encrypted PDF
- a damaged PDF
- a huge PDF (over 300 MB)

---

## 3. UAT scripts

Each script lists **who** runs it, **steps**, and **pass criteria**. Record the result, the date, the
tester and any wording fixes in the Asana task for that component.

### Core connector (IT + Switch admin)

1. `enfocus-switch-mcp check` on each test machine.
   **Pass:** it ends with "All checks passed", shows the right Switch user and permissions, and a wrong
   password gives a clear error.
2. In Claude: "What's waiting in checkpoints?", "Show job X", "Errors in the last 12 hours?".
   **Pass:** the answers match Switch Designer.
3. On the read-only machine, ask Claude to route a job.
   **Pass:** there is no route tool; Claude says it can't.
4. On the write machine: route, lock, rush and submit on the **test flow**.
   **Pass:** each action appears in Switch under the test user. Claude asks for confirmation before
   routing. Routing a job someone else already moved is refused (stale `updated`).

### A1 Preflight explanations (2 prepress + 2 CSRs)

1. For 20+ real reports: "Explain the preflight report for job X for the customer" and "… for a CSR".
   **Pass:**
   - At least 90% of issues land in the right category, with the right fix owner.
   - The customer text is accurate and has no jargon.
   - The CSR text says what to do next.
   - The verdict (`ready` / `prepress_can_fix` / `needs_customer`) matches what prepress would decide.
2. Log each wording or ownership fix. Apply the fixes in `knowledge.py` and re-test those reports.

### A2 Client self-serve preflight (IT + portal developer + 1 CSR)

1. `curl` the test service with each sample file (see deployment.md 4.3), with and without `trim` / `colors`.
   **Pass:**
   - Clean files come back `ready`.
   - Each problem file names its problem in customer language.
   - Encrypted, damaged and non-PDF files, and files over the size limit, get a clear 4xx message with no
     internal details.
   - It responds within 10 s for a typical 20 MB file.
2. Send a `job_number` belonging to customer A with `customer` = B.
   **Pass:** the ticket says "couldn't find"; none of A's order details are returned.
3. Portal integration in portal staging.
   **Pass:** the key is only on the portal server (check the browser dev tools). The message shows up
   correctly. Uploads over the portal's own limit are refused before reaching the service.

### A3 / A5 / D1 Pace writes (Pace admin + 1 CSR), Pace **test** system only

1. With `PACE_ALLOW_WRITE=false`, run `approve_proof` (dry run, then real) on a test job.
   **Pass:** the Switch route happens and the Pace steps are `manual`, with instructions.
2. Fill in `pace_api.json` and set `PACE_ALLOW_WRITE=true` and `PACE_ALLOWED_STATUSES`.
   **Pass:**
   - The status and note appear on the Pace test job.
   - A note containing quotes, `<`, `&` and a line break arrives exactly as typed.
   - A status not on the allow-list is refused.
   - A Pace API outage gives step `failed`, with the Switch route still done and reported.
3. Check `AUDIT_LOG`.
   **Pass:** one line per attempt (done, refused and failed), and no passwords.
4. For each event in `service_status_map.json`, fire it from the Switch test flow.
   **Pass:** with `SERVICE_DRY_RUN=true` the steps are `planned`; with `false` the Pace status changes; an
   unknown event name gets a 400.

### A4 "Where's my job?" (2 CSRs)

1. For 10 live orders: "Where is order X and what should I tell the customer?"
   **Pass:** the Switch location and Pace status are both correct, and the suggested reply is usable
   as-is or with small edits.

### B1 File vs. ticket (2 prepress)

1. Run each sample file against its (real or made-up) ticket, with the Pace job number once the `job_spec`
   SQL is in place.
   **Pass:**
   - Every seeded mismatch is found.
   - No false "does not match" on clean files; tune `SIZE_TOLERANCE_IN` if needed.
   - The messages make sense to a CSR.

### B2 Auto-fix routing (Switch admin + prepress lead)

1. `plan_autofixes` on 10 checkpoint jobs.
   **Pass:** the auto-fixable / prepress / customer split matches the prepress lead's judgement.
2. Service event `preflight_done` with `auto_route: true`, first with `SERVICE_AUTO_ROUTE=false` (skipped),
   then `true` (routed) on the test flow.
   **Pass:**
   - Only jobs whose open issues are all auto-fixable are routed.
   - Jobs needing the customer are never routed automatically.
   - Every route is in the audit log.

### B3 Digest (prepress lead)

1. `enfocus-switch-mcp digest`, then `--post` to a test Teams channel.
   **Pass:** the contents match Switch; stuck jobs are flagged at the agreed threshold; it posts on
   schedule for 5 working days.

### B4 Job matching (1 CSR + Switch admin)

1. Run 30 real file names and email subjects through `find_job_numbers` / `/switch/match-job`.
   **Pass:** at least 95% of those with a job number are matched; there are no false matches on
   phone numbers, dates or ZIP codes. Adjust `PACE_JOB_NUMBER_PATTERNS` until this passes.

### C1 / C2 / C3 Estimating (2 estimators)

1. `draft_item_from_pdf` on 10 recent jobs.
   **Pass:** the product, size, pages and inks are right; the item-template payload has the right Pace
   field names; nothing is priced.
2. `validate_job_spec` on 15 real RFQs (via Claude reading the email).
   **Pass:** the missing details and questions are what the estimator would have asked, and nothing is
   invented.
3. Similar jobs, once the SQL is in place.
   **Pass:** at least 3 useful comparables for common products.

### D2 Analytics and D3 customer rules (CSR lead)

1. Add 3 real customer agreements to `customer_rules.json`. Explain a report for each of those customers.
   **Pass:** accepted issues are listed as accepted, and the verdict changes as expected.
2. After two weeks of pilot use, run `enfocus-switch-mcp report --days 14`.
   **Pass:** the numbers are plausible, the top issues match prepress's experience, and nothing sensitive
   is stored (inspect the SQLite file).

### Service operations (IT)

**Pass criteria:**
- `/health` is in monitoring.
- The service restarts after a reboot and after being killed.
- Missing or wrong keys get 401; the wrong scope gets 403; the rate limit gives 429.
- The config check fails clearly on a bad keys file.
- Logs rotate.
- The restore of the analytics DB from backup has been tested.

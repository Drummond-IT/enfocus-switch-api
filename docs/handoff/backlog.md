# Backlog

Every remaining task to take the connector and the automation service live. It is organised to load
straight into Asana:
- **one section per phase**, matching [rollout.md](rollout.md);
- **one task per heading**, with the owner role, dependencies and acceptance criteria as the description
  and checklist.

Owner roles: **IT** = infrastructure/automation, **SW** = Switch admin, **PACE** = Pace admin/DBA,
**WEB** = portal developer, **CSR**, **PP** = prepress, **EST** = estimating, **LEAD** = the department
lead who signs off.

Task IDs (e.g. `P1-03`) are for cross-references. Put them in the Asana task names.

---

## Phase 0: Setup

### P0-01 Create Switch users for the connector and the service
- **Owner:** SW
- **Acceptance:**
  - [ ] `ai-connector`, `ai-connector-test` and `automation-service(-test)` exist, with access limited to
    the needed flows.
  - [ ] Passwords are stored in the password manager.

### P0-02 Build the Switch test flow
- **Owner:** SW
- **Acceptance:**
  - [ ] A copy of the production preflight flow, with a submit point, PitStop, a checkpoint with
    Approve/Reject connections and a customer metadata field.
  - [ ] A sample job passes through it end to end.

### P0-03 Collect the sample file set
- **Owner:** PP
- **Acceptance:**
  - [ ] 25+ PDFs covering every case in test-plan.md §2, with their PitStop reports, in a shared test
    folder.

### P0-04 Set up test machines with the connector
- **Owner:** IT
- **Depends on:** P0-01
- **Acceptance:**
  - [ ] 3 machines (read-only, write, estimating).
  - [ ] `enfocus-switch-mcp check` passes on each.
  - [ ] Claude Desktop shows the tools.

### P0-05 Run the automated tests on the target OS
- **Owner:** IT
- **Acceptance:**
  - [ ] `pytest` passes on the Windows (and/or Linux) hosts that will run the service and the connector.
  - [ ] Failures are fixed or logged as tasks.

### P0-06 Decide on the PHP sample dependencies (Dependabot alerts)
- **Owner:** IT
- **Acceptance:**
  - [ ] Either upgrade Guzzle/psr7 in `composer.json` and test the PHP sample, or delete the PHP sample
    (`src/`, `public/`, `composer.*`).
  - [ ] The Dependabot alerts are closed.

## Phase 1: Read-only Claude pilot

### P1-01 Pilot group install
- **Owner:** IT
- **Depends on:** P0-04
- **Acceptance:**
  - [ ] 2 prepress, 2 CSRs and 1 estimator have the read-only connector.
  - [ ] Each has read their training guide.

### P1-02 UAT: core connector
- **Owner:** IT, SW
- **Acceptance:**
  - [ ] Test plan "Core connector" steps 1–3 pass.
  - [ ] Signed off.

### P1-03 UAT: preflight explanations (A1), with wording fixes
- **Owner:** PP, CSR; IT applies the fixes
- **Acceptance:**
  - [ ] 20+ real reports reviewed.
  - [ ] At least 90% correct categories and owners.
  - [ ] All wording fixes applied in `knowledge.py` and re-tested.
  - [ ] Signed off by LEAD (prepress) and LEAD (CSR).

### P1-04 Map fix owners to house policy
- **Owner:** LEAD (prepress)
- **Acceptance:**
  - [ ] For each category in `knowledge.py`: who fixes it (customer / prepress / either) and whether it's
    charged.
  - [ ] Changes merged.

### P1-05 UAT: file vs. ticket (B1) with typed ticket details
- **Owner:** PP
- **Acceptance:**
  - [ ] Every seeded mismatch is found.
  - [ ] No false "does not match" on clean files.
  - [ ] `SIZE_TOLERANCE_IN` is agreed.

### P1-06 Schedule the morning digest (B3)
- **Owner:** IT
- **Acceptance:**
  - [ ] A Teams incoming webhook is created.
  - [ ] The digest posts on weekdays at the agreed time for a week.
  - [ ] `DIGEST_STUCK_HOURS` is agreed with prepress.

### P1-07 Tune RFQ required fields (C2)
- **Owner:** EST; IT edits `rfq.py`
- **Acceptance:**
  - [ ] `REQUIRED` and `QUESTIONS` match how estimators quote each product family.
  - [ ] 15 real RFQs pass UAT C2.

### P1-08 House products for estimate drafts (C1)
- **Owner:** EST; IT edits `estimate_draft.py`
- **Acceptance:**
  - [ ] `STANDARD_PRODUCTS` lists Drummond's standard sizes.
  - [ ] 10 recent jobs are drafted correctly.

### P1-09 Connect the estimating mailbox (C2)
- **Owner:** IT
- **Acceptance:**
  - [ ] The Microsoft 365 connector is enabled for estimators in Claude.
  - [ ] Claude can read an RFQ email and run `validate_job_spec` on it.

## Phase 2: Pace, read-only

### P2-01 Pace read-only role and connectivity
- **Owner:** PACE
- **Acceptance:**
  - [ ] A read-only role on a replica (or production, with approval).
  - [ ] Firewall open from the service host and pilot machines.
  - [ ] `check` reports "Pace database reachable".

### P2-02 Write the `job_spec` query
- **Owner:** PACE
- **Depends on:** P2-01
- **Acceptance:**
  - [ ] It returns the columns in `pace_queries.example.sql` for 20 sample jobs.
  - [ ] Values match Pace.
  - [ ] It runs in under 1 s.

### P2-03 Write the `job_status` query
- **Owner:** PACE
- **Depends on:** P2-01
- **Acceptance:**
  - [ ] It returns status, due date, CSR and customer for 20 sample jobs.
  - [ ] It runs in under 1 s.

### P2-04 Write the `similar_jobs` query (C3)
- **Owner:** PACE, EST
- **Acceptance:**
  - [ ] At least 3 useful comparables (with quantity and price) for common products from the last 12 months.

### P2-05 Pace item-template field map (C1)
- **Owner:** PACE, EST
- **Acceptance:**
  - [ ] `item_template_map.json` uses the real Pace field names.
  - [ ] An estimator confirms that a draft payload can be entered as-is.

### P2-06 Job number patterns (B4)
- **Owner:** CSR, SW
- **Acceptance:**
  - [ ] `PACE_JOB_NUMBER_PATTERNS` matches at least 95% of 30 real file names and subjects.
  - [ ] No false matches.

### P2-07 UAT: "Where's my job?" (A4) and matching (B4)
- **Owner:** CSR
- **Acceptance:**
  - [ ] Test plan A4 and B4 pass.
  - [ ] Signed off.

### P2-08 Customer rules (D3)
- **Owner:** CSR lead
- **Acceptance:**
  - [ ] The standing agreements for the top customers are collected in `customer_rules.json`, with
    evidence (waiver date) in `notes`.
  - [ ] UAT D3 passes.

### P2-09 Turn on analytics (D2)
- **Owner:** IT, CSR lead
- **Acceptance:**
  - [ ] `ANALYTICS_DB` is set on all installs; the retention period is agreed.
  - [ ] The first 30-day report is reviewed with the CSR lead.

## Phase 3: Switch writes from Claude

### P3-01 Decide who gets write access
- **Owner:** LEAD (prepress)
- **Acceptance:**
  - [ ] A named list, recorded in the runbook.

### P3-02 UAT: write tools on the test flow
- **Owner:** SW, PP
- **Acceptance:**
  - [ ] Test plan "Core connector" step 4 passes.
  - [ ] `approve_proof` routes and reports the Pace steps as manual.

### P3-03 Enable write access for the named users
- **Owner:** IT
- **Depends on:** P3-01, P3-02
- **Acceptance:**
  - [ ] Configs updated.
  - [ ] The users have read [training/prepress.md](training/prepress.md).
  - [ ] Two weeks with no wrong routing.

## Phase 4: Automation service (dry run)

### P4-01 Deploy the service to the test host
- **Owner:** IT
- **Acceptance:**
  - [ ] Installed per deployment.md §3 (Windows/Linux/Docker).
  - [ ] `/health` is in monitoring.
  - [ ] Test plan "Service operations" passes.

### P4-02 Reverse proxy with TLS
- **Owner:** IT
- **Acceptance:**
  - [ ] An https URL is reachable only from the internal network.
  - [ ] The certificate is from the internal CA.
  - [ ] The upload limit matches `SERVICE_MAX_UPLOAD_MB`.

### P4-03 Issue API keys
- **Owner:** IT
- **Acceptance:**
  - [ ] Keys for `switch` (and later `portal`, `rfq-form`) with minimal scopes.
  - [ ] The keys are stored in the callers' secret stores; the names are recorded in the runbook.

### P4-04 Event map
- **Owner:** SW, CSR lead
- **Acceptance:**
  - [ ] `service_status_map.json` lists each Switch milestone, the exact Pace status and the note text.
  - [ ] The CSR lead agrees the statuses.

### P4-05 Confirm how Switch sends the job ID and API key
- **Owner:** SW
- **Acceptance:**
  - [ ] The Switch variable that gives the Web Services job ID is identified and tested (or `job_name` is
    used instead).
  - [ ] The HTTP request element (or script element) sends the key header.
  - [ ] The choice is recorded in the runbook.

### P4-06 Wire the test flow to `/switch/events` and `/switch/match-job`
- **Owner:** SW
- **Depends on:** P4-01 … P4-05
- **Acceptance:**
  - [ ] Events fire at each milestone and responses show the planned steps.
  - [ ] Failures take the manual path (fail open).

### P4-07 Production flows in dry run for a week
- **Owner:** SW, IT
- **Acceptance:**
  - [ ] The audit log is reviewed daily; every planned step is correct.
  - [ ] There are no 5xx errors.

## Phase 5: Pace writes

### P5-01 Pace API account and test system access
- **Owner:** PACE
- **Acceptance:**
  - [ ] An API account limited to job status and notes (as far as Pace allows).
  - [ ] Credentials are in the password manager.

### P5-02 Fill in `pace_api.json`
- **Owner:** PACE
- **Depends on:** P5-01
- **Acceptance:**
  - [ ] `update_job_status` and `add_job_note` work against the Pace **test** system.
  - [ ] Test plan A3/A5/D1 steps 1–3 pass, including escaping and the allow-list.

### P5-03 Status allow-list
- **Owner:** CSR lead, PACE
- **Acceptance:**
  - [ ] `PACE_ALLOWED_STATUSES` holds only the statuses from P4-04 plus `PACE_PROOF_APPROVED_STATUS`.

### P5-04 Turn on Pace writes in production
- **Owner:** IT
- **Depends on:** P5-02, P5-03
- **Acceptance:**
  - [ ] `PACE_ALLOW_WRITE=true` and `SERVICE_DRY_RUN=false`.
  - [ ] The CSR lead reviews every automated Pace change for a week; no unexpected changes.

## Phase 6: Auto-fix routing

### P6-01 Build the auto-fix branch in Switch
- **Owner:** SW, PP
- **Acceptance:**
  - [ ] Checkpoint connection → PitStop Action Lists → re-preflight → back to the checkpoint.
  - [ ] Tested with the sample files.

### P6-02 Auto-fix map
- **Owner:** PP lead
- **Acceptance:**
  - [ ] `autofix_map.json` lists only the categories the branch reliably fixes.
  - [ ] `needs_review` is set where a proof check is needed.

### P6-03 UAT: auto-fix routing (B2)
- **Owner:** PP lead
- **Acceptance:**
  - [ ] Test plan B2 passes.
  - [ ] No customer-needed job is ever auto-routed.

### P6-04 Enable auto-route
- **Owner:** IT
- **Depends on:** P6-03
- **Acceptance:**
  - [ ] `SERVICE_AUTO_ROUTE=true`.
  - [ ] The PP lead reviews every auto-routed job for 2 weeks.

## Phase 7: Client-facing

### P7-01 Portal: self-serve preflight
- **Owner:** WEB
- **Acceptance:**
  - [ ] The portal backend calls `POST /preflight` after upload and shows the `message` / `ticket` issues.
  - [ ] The key is server-side only.
  - [ ] The customer comes from the portal login.

### P7-02 Portal: full PitStop result (A2 step 4)
- **Owner:** WEB, SW
- **Acceptance:**
  - [ ] The upload is also submitted to a "Client Preflight" submit point.
  - [ ] The explained report reaches the customer (portal or email) after CSR approval, if required.

### P7-03 Portal: proof approval button
- **Owner:** WEB
- **Acceptance:**
  - [ ] "Approve" calls `POST /proof/approve` with the approver's name.
  - [ ] The customer sees the result.
  - [ ] A 409 is shown as "already approved / not ready".

### P7-04 UAT A2 and A5 with friendly customers
- **Owner:** CSR lead, WEB
- **Acceptance:**
  - [ ] Test plan A2 passes.
  - [ ] 5–10 customers use it for 3 weeks; their feedback is reviewed.

## Phase 8: Everyone, and training

### P8-01 Train CSRs
- **Owner:** CSR lead
- **Acceptance:**
  - [ ] Session run with [training/csr.md](training/csr.md); attendance recorded.

### P8-02 Train prepress
- **Owner:** PP lead
- **Acceptance:**
  - [ ] Session run with [training/prepress.md](training/prepress.md).

### P8-03 Train estimating
- **Owner:** EST lead
- **Acceptance:**
  - [ ] Session run with [training/estimating.md](training/estimating.md).

### P8-04 Admin handover
- **Owner:** IT
- **Acceptance:**
  - [ ] Two IT staff can do every task in [training/admin.md](training/admin.md) and [runbook.md](runbook.md)
    unaided.

### P8-05 Install for all staff
- **Owner:** IT
- **Acceptance:**
  - [ ] Every CSR, prepress user and estimator has the connector, with the right role config.

### P8-06 Monthly analytics review
- **Owner:** CSR lead
- **Acceptance:**
  - [ ] A recurring task: review the report and choose 1–2 customers for client education.

## Later (not scheduled)

- **L-01 Pace → Switch sync (D1b):** lock or hold the Switch job when the Pace job goes on hold or is
  cancelled. Needs a Pace-side trigger.
- **L-02 Checkpoint time and proof-round analytics (D2b):** from the Switch Reporting module.
- **L-03 Near-real-time alerts:** post chosen Switch events to Teams from `/switch/events`.
- **L-04 Create Pace estimate items from drafts:** add an API operation to `pace_api.json` once the Pace
  API for items is confirmed.
- **L-05 Customer rules from Pace:** read them from a Pace customer custom field instead of the JSON file.
- **L-06 Run the file-vs-ticket check inside a Switch flow:** before PitStop, via `/switch/events`
  (`explain`) or a script element.
- **L-07 Shared rate limiting / central audit:** if the service is ever scaled to several instances, or
  the SIEM needs the audit log.

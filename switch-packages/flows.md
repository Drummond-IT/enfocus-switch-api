# Switch flows for Drummond One

Two flows. The portal finds each submit point by its exact name and fills the metadata fields it recognizes by name
(`src/switch.js`, `submitForPreflight`). As of 2026-11-18 Switch has 197 flows and 0 submit points.

## 1. Client portal preflight

| # | Element | Settings |
|---|---|---|
| 1 | Submit point **Client portal preflight** | Metadata fields, all text, none required: **Customer** (Pace customer ID), **Company**, **Job number**, **Portal upload** (the portal's file ID; previews and events are matched by it) |
| 2 | Folder | job name kept as sent |
| 3 | PitStop Server | profile **Drummond Portal Preflight**; report JSON (or XML v3); attach the report to the job |
| 4 | Image (render pages) | PNG of each page, about 850 px wide |
| 5 | HTTP request | POST each PNG to `https://client-portal.drummondit.workers.dev/api/switch/preview` (test site first), header `Authorization: Bearer <SWITCH_EVENTS_SECRET>`, multipart fields `kind=upload`, `upload=<Portal upload>`, `page`, `image`, optional `trim` and `bleed` (portal SETUP C21 step 3) **(unverified: element names and multipart settings in this Switch version)** |
| 6 | Folder "Checked" | end. The portal reads the report through `GET /api/v1/job/report/<id>` |

The secret is set in the HTTP element by whoever has the Switch sign-in; it is never put in this repo or in chat.

## 2. Portal auto-fix

| # | Element | Settings |
|---|---|---|
| 1 | Submit point **Portal auto-fix** | same four metadata fields |
| 2 | PitStop Server | action list **Drummond Portal Auto-fix** |
| 3 | PitStop Server | profile **Drummond Portal Preflight** (the check again) |
| 4 | Image and HTTP request | as flow 1, so the fixed copy has previews for the side-by-side (F-1332) |
| 5 | Folder "Fixed" | end |

## Import checklist (each step after Chris's go)

1. Switch Designer connected to atl1-switch03 with a user that may edit flows.
2. Profile and action list from PitStop Pro (`.ppp`, `.eal`) placed where PitStop Server reads them.
3. Build flow 1, then flow 2, as above. Leave both stopped.
4. Start flow 1 and run `testset/out/01` to `12`; compare each report with `testset/out/expected.json`.
5. Start flow 2 and run 02, 04, 07, 08, 10, 12; each fixed copy's report should be clean apart from the "Prepress look" notes.
6. Portal test site: `SWITCH_FIX_SUBMIT_POINT` = `Portal auto-fix`. Send a test file as the ZTEST1 test company and check
   the file's page: report, previews, Fix it for me, then Compare with your original.
7. Done when a test PNG from a design app goes in, gets a report and previews, is fixed on request, and the customer can
   choose the fixed file.

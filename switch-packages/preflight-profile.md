# PitStop profile: Drummond Portal Preflight

Start from the Enfocus standard profile for PDF/X-4 digital and offset printing in PitStop Pro (Enfocus Certified PDF
"PDF/X-4" family), save it as **Drummond Portal Preflight**, then set the checks below. Exact check names differ a little
by PitStop version: match by what they test. **(unverified: names in Drummond's PitStop version.)**

## Message wording (the portal depends on it)

The portal sorts PitStop's messages into problem types by words in them (`src/file-quality.js` and switch-mcp
`knowledge.py`). Keep each message's custom text containing the word in the last column, or the portal files it as
"Other problems" and offers no fix.

| Check | Limit | Level | Message must contain |
|---|---|---|---|
| Page boxes: TrimBox present | missing | Error | `trim` |
| Trim size matches the job | from Switch metadata "Job size" when present, else none | Error | `page size` |
| All pages the same size | any difference | Error | `page size` |
| Bleed | less than 0.125 in on any side where content touches the trim | Warning | `bleed` |
| Image resolution, color and grayscale | below 225 ppi warning, below 150 ppi error | Warning / Error | `resolution` |
| Image resolution, 1-bit | below 600 ppi | Warning | `resolution` |
| Color space | DeviceRGB, ICC RGB, Lab used | Warning | `RGB` |
| Spot colors | any not on the job | Warning | `spot` |
| Fonts | not embedded | Error | `font` |
| Fonts | Type 3 | Warning | `font` |
| Line weight | below 0.25 pt | Warning | `hairline` |
| Text size | below 6 pt; reversed text below 8 pt | Warning | `small text` |
| Overprint | white or 0% objects set to overprint | Warning | `overprint` |
| Black text | small text (below 12 pt) in 4-color black | Warning | `4-color black ink` |
| Total ink | above 300% | Warning | `ink` |
| Transparency | live transparency in a PDF/X-1a | Warning | `transparen` |
| Safety | text or logos closer than 0.125 in to the trim | Warning | `too close` |
| Security | password or restricted permissions | Error | `password` |

## Report

- Report format: **JSON** (PitStop 2023 or newer) or XML v3. The portal reads both (`summariseReport` in
  `src/switch.js`); switch-mcp's `preflight.py` reads both too.
- Keep fixes PitStop made listed under "fixes": the portal shows them as "Fixed automatically".

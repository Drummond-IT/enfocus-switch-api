# Drummond One preflight packages

Everything needed to switch on customer preflight and auto-fix in Drummond One (portal F-21, F-116, F-117, F-1332), built
before anyone touches the Switch server. Nothing here has been imported yet. Each import step waits for Chris's go.

| File | What it is |
|---|---|
| `preflight-profile.md` | The PitStop preflight profile "Drummond Portal Preflight": every check, its limit, error or warning, and the message wording the portal needs |
| `autofix-action-list.md` | The PitStop action list "Drummond Portal Auto-fix": the fixes in order, and which need a prepress look |
| `flows.md` | The two Switch flows the portal calls: **Client portal preflight** and **Portal auto-fix**, element by element, with the import checklist |
| `autofix_map.drummond.json` | switch-mcp's auto-fix map (`SWITCH_AUTOFIX_MAP`) matching the action list |
| `testset/` | 12 test PDFs, one per defect, with `expected.json`; `make-testset.mjs` remakes them |

## Order of work

1. **Here, no access needed (done):** the specs, the map and the test set.
2. **Profile and action list.** Build both in PitStop Pro's editor from the Enfocus standard profile named in
   `preflight-profile.md`, export them as `.ppp` and `.eal`, and add them here. This needs PitStop Pro on a machine
   (it isn't on ATL1-BJHLP73 as of 2026-11-18). The `.ppp` and `.eal` formats can't be written reliably without the editor.
3. **Flows.** In Switch Designer, connected to atl1-switch03, build the two flows in `flows.md` and give them the profile and
   action list. Then run every file in `testset/out/` through and compare with `expected.json`. This needs Switch Designer
   (also not on ATL1-BJHLP73) and a Switch sign-in that may edit flows.
4. **Portal.** The portal finds the submit points by name. On the test site only: set `SWITCH_FIX_SUBMIT_POINT` to
   `Portal auto-fix` (portal SETUP C21). After that, F-21, F-116, F-117 and F-1332 work for the test company.

## What needs admin rights

- Building or importing flows, and adding PitStop profiles to the server: a Switch user that may edit flows, in Switch
  Designer. The Switch REST API can read flows and submit jobs, but can't create flows.
- Installing Switch Designer or PitStop Pro on a PC: Windows admin on that PC and an Enfocus account sign-in.
- Starting or stopping flows: a Switch admin user (the REST API gets 403 otherwise).
- No firewall, DNS or security change is needed anywhere: atl1-switch03:51088 already answers from ATL1-BJHLP73.

## What the portal's own instant check already catches

The test set run through the portal's own instant check (`src/pdf-check.js` in wavelength-dashboard, against a letter job with bleed, 2026-11-18):

| File | Portal instant check | Only PitStop catches |
|---|---|---|
| 01 clean | ready | |
| 02, 03 RGB | RGB color | |
| 04 no bleed | No bleed | |
| 05 96 ppi | Low-resolution images, No bleed, RGB color | |
| 06 font | Font not embedded | |
| 07 hairline | ready | 0.1 pt line |
| 08 white overprint | ready | white overprint |
| 09 wrong size | Page size doesn't match | |
| 10 crop marks | Page size doesn't match, No trim size | trim from crop marks |
| 11 mixed sizes | Page count, different sizes | |
| 12 rich black text | ready | 400% black small text |

So PitStop adds thin lines, overprint, rich black and the actual fixes. The rest the customer already sees in seconds.

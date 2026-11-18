# PitStop action list: Drummond Portal Auto-fix

Runs only after the customer ticks "I agree to these changes" on the portal (F-116; D78 a, no charge). The fixed copy is
checked again with **Drummond Portal Preflight**, and the customer then chooses the fixed file or the original (F-1332).
Order matters: run top to bottom. **(unverified: action names in Drummond's PitStop version.)**

| # | Fix | Settings | Portal type | Prepress look |
|---|---|---|---|---|
| 1 | Set TrimBox from crop marks, then remove the marks | marks at the media corners; trim = inner corners | size | yes |
| 2 | Convert RGB and Lab to CMYK | house output intent (GRACoL 2013 CRPC6 unless prepress names another), relative colorimetric, black preserved | color | no |
| 3 | Remove overprint from white objects | | transparency | no |
| 4 | Small black text to 100% K | text below 12 pt in 4-color black | color | no |
| 5 | Thicken lines below 0.25 pt to 0.25 pt | | lines | no |
| 6 | Add bleed | mirror 0.125 in where content touches the trim | bleed | yes |
| 7 | Limit total ink | 300% | color | no |
| 8 | Set output intent | as #2 | color | no |

Not auto-fixed (only the customer can): low resolution, fonts not embedded, wrong trim size beyond 2%, page count,
password. The portal says so on the file's page.

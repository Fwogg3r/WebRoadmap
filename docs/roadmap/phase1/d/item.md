---
title: "Far-zoom detail: job tiles, phase link bundles, chains kept zoomed out"
nickname: Far detail
status: done
links: []
done: 2026-11-10
---
**What:** More detail when zoomed out. Far zoom now shows every job as a tile in its status colour (titled while the title fits), a job count per status under each phase name, and one arc per pair of linked phases (thicker and numbered with the link count). Very far out (below 17%), tiles drop their titles and the phase names grow over them. Clicking a tile selects it, and its whole chain of links stays drawn, scaled to stay visible, however far out you zoom.

**Why:** Owner request (2026-11-10): zoomed out, the map showed only empty phase boxes, with no job count, no links, and a selection that vanished.

**How:** Page only, no format changes. Item tiles are a fourth `lv-far` layer per item. Phase far labels moved to their own layer above the items (`.plab`, band height until `vfar`, then growing over the tiles; above the phase while a chain is lit). Bundles are computed in `build()` from item links and drawn as SVG arcs over the phase tops (`#edges .bnd`); job links sit in `#edges .jobs` and show at far only for the selected chain, with stroke, dashes, ports and arrowheads scaled by `--inv`. Hover chains are off at far so the bundles don't flicker.

**Notes:** Decisions from the owner's answers (2026-11-10): phase-to-phase bundles when nothing is selected; status tiles plus big titles; a click on a tile selects it (a click on empty phase space still zooms in). Tests: 29 pytest; browser check ALL OK (dark and light), with 6 new far checks (tiles visible, job count, bundles shown, click selects, chain links shown and bundles hidden, chain kept at 8% zoom). README screenshots regenerated. Possible follow-ups: bundle labels can overlap where arcs cross; phases laid out in several rows get arcs that cross other phases.

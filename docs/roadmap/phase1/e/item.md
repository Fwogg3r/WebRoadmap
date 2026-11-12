---
title: PCB-style phase tracks at far zoom
nickname: PCB tracks
status: done
links: [phase1/d]
done: 2026-11-12
---
**What:** The far-zoom phase-to-phase links are now straight orthogonal tracks, like PCB traces, instead of crossing Bézier arcs: each link rises from a pad on the source phase's top, runs along its own level and drops into the target with an arrow, with 45° bends.

**Why:** Owner request (2026-11-12): on a real roadmap (Particle, 13 phases, ~40 phase links) the arcs were a tangle nobody could read; the zoomed-in grid routing was clean.

**How:** Page only (`app.js` `build()` + `drawBundles()`, `style.css`). Levels are assigned like interval colouring: shorter spans first, each span above every span it contains, partial overlaps on distinct levels, so nested links never cross. Ports on each phase top: links going left first (lowest level leftmost), then links going right (lowest level rightmost), so tracks leaving one phase fan out without crossing. Track pitch, port spacing and bends are in screen pixels and are recomputed as the far zoom changes. Counts sit on the track's horizontal run.

**Notes:** Tests: 29 pytest; browser check ALL OK (dark and light); README screenshots regenerated. Checked headless on the Particle roadmap: about 22 levels, readable. Remaining crossings are only where spans partly overlap (inherent). Possible follow-up: link counts can still sit on another track's vertical.

---
title: Grid layout, border ports, coloured links, drag and resize
nickname: Grid
status: active
bold: true
links: []
---
**What:** Phases and groups lay their items and sub-groups out as near-square grids that grow sideways and downwards; links are routed like wires through the gaps, with a dot wherever they cross a phase or group border; each source item's links get their own colour. Items, groups and phases can be dragged and dropped, and phases and groups resized by their corner, all saved to the files.

**Why:** The owner's sketch (2026-11-10): tall single columns and curved arrows crossing boxes got hard to follow; a grid with tidy channels and border ports reads like a circuit diagram, and moving or resizing things by hand is quicker than editing frontmatter.

**How:**
- Layout (page): four passes over a tree (root, phases, groups, items). Shape: `ceil(sqrt(n))` columns, or the most that fit a stored `width:`. Route: each link becomes channel references: source column gap, then row gap below, then out through the right border, up to the lowest container both ends share, then down through left borders into the target. Size: every gap grows to `2*margin + lanes*11px`. Place: top-down, with spare room going to the right and bottom gutters. Links between non-adjacent phases run just below the phases they pass. Exits from a cell narrower than its column turn down right next to the cell.
- Interaction: left-drag on an item, group header or phase header moves it (ghost, blue drop bar, outlined target, auto-pan at the edges, Esc cancels); empty space still pans; far zoom pans only. The corner handle resizes with a live reflow; double-click resets.
- Format/CLI/API: `width`/`height` on phases and groups (`set` rounds them, refuses them on items, and `check` errors on non-positive values). New `roadmap arrange CONTAINER ID...` and `/api/arrange`: moves each listed entry in (`mv` rules) and writes `order: 1..N`. `items_per_column` is no longer used.

**Notes:** Decisions from the owner's answers (2026-11-10): one colour per source item; one dot per link, with gaps scaling so borders don't crowd; drag/resize saved to files; near-square grid. Tests: 29 pytest (2 new: arrange, sizes); browser check ALL OK in dark and light, with new checks for the 2x2 grid, one path per link, 10 colours, a dot on each border crossed, no path through an item box, a drag into another phase (links rewritten), a reorder, a resize (live reflow, then saved), double-click reset and Esc cancel. Branch `grid-layout`, not merged yet. Possible follow-ups: lane ordering to reduce crossings in busy groups; touch drag (touch pans only for now).

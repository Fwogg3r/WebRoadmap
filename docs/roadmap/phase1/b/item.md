---
title: "Groups inside phases: nested group rectangles"
nickname: Groups
status: done
bold: true
links: [phase1/c]
done: 2026-11-10
---
**What:** Groups inside a phase: named rectangles that hold related items, laid out side by side (horizontally, wrapping into a grid) within the phase, and groups can nest inside groups (a group box containing smaller group boxes and items). A long phase reads as a few labelled clusters instead of one tall list.

**Why:** Big phases become hard to scan. PersonalMCPJobController's "Hardening and reach" phase reached 46 items before it was split into five phases by hand; groups would let a phase stay one phase while showing its structure.

**How (proposal, to settle in a design pass):**
- Format: a group is a subfolder of a phase (or of another group) holding a `group.md` (name, nickname, status, color, summary), so the folder tree is the nesting. Items keep their own folders inside it. Ids stay stable when items move between groups (e.g. ids stay `phase4/x` regardless of group, with the group as a field or folder path), so links, commit messages and code comments don't break; this is the main lesson from the 2026-11-10 pmjc split, where moving items to new phases changed 40 ids and 20 references had to be rewritten.
- Layout is horizontal first, not one tall stack: a phase with groups grows wider, and its groups sit side by side (a row of group rectangles that wraps into a grid when there are many), each group laying out its own items and sub-groups the same way, so a big phase reads as a block of clusters rather than a long column. Group width follows its content (items in a group can form short columns, e.g. at most N per column, then a new column to the right). Links between items still route across groups and phases.
- Page: each group is a bordered, labelled rectangle inside the phase; nested groups are rectangles inside it; collapse/expand per group; group status colour from its items (or set explicitly); links can target a whole group like they can target a phase.
- CLI: `roadmap add-group PHASE --name ...` (optionally `--in GROUP`), `roadmap mv ITEM GROUP`, `list` and `show` print the tree; `check` validates group files and warns on empty groups.
- FORMAT.md and the demo roadmap updated; existing roadmaps without groups keep working unchanged.

**Notes:** Requested 2026-11-10 by the owner while splitting the pmjc roadmap.

Done 2026-11-10 (branch `groups`):
- Format: a group is a `g-<slug>` folder (in a phase or another group) with `group.md` (`name`, `nickname`, `status`, `color`, `order`, summary). Ids leave the group folders out: items stay `phaseN/x`, groups are `phaseN/g-<slug>`; letters and group slugs are unique per phase, so moving within a phase changes no id. Group status is explicit or derived from its items. New `roadmap.md` setting `items_per_column` (default 4).
- CLI/API: `add-group PHASE --name [--in GROUP]`, `add --group`, `mv` item/group into a phase or group (same phase: id kept; other phase: free letter/slug, links rewritten), `set`/`show` on groups, `rm GROUP [--force]`, `list` prints the tree (`#` = group), JSON has `groups` per phase (with `parent`, `items`, `groups`) and `group` per item.
- `check`: missing/invalid `group.md`, bad group folder names, unknown status, duplicate letters/slugs in a phase, links to missing groups (errors); empty groups (warning).
- Page: grouped phases grow wider (top-level items in short columns, groups side by side, grid of ceil(sqrt n) per row beyond 3); nested rectangles; far-zoom group nicknames; collapse (links end at the header); panel with + Item / + Group / Collapse / Edit (rename keeps id, status "from items", move to phase or group); item edit has a Phase / group selector.
- Tests: 27 pytest (7 new for groups); browser check passes in dark and light with new layout assertions (side by side, nested inside parent, 3 columns for 9 items, ungrouped phase still 300 wide) and editor steps (collapse, create, rename, move in, nested group). Demo phase 5 has three groups, one nested, one with 9 items.

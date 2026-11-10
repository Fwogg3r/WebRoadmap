---
title: "Groups inside phases: nested group rectangles"
nickname: Groups
status: planned
bold: true
links: []
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

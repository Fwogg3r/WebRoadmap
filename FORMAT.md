# Roadmap folder format (v1)

A project's roadmap lives in `docs/roadmap/` (found by walking up from the cwd; `--dir` overrides). Plain files, so people, agents and git all edit the same thing. The tool never stores state elsewhere.

```
docs/roadmap/
  roadmap.md            project title + optional settings (frontmatter), intro text (body)
  phase1/
    phase.md            the phase: name, nickname, status, color, summary
    a/item.md           one roadmap entry
    b/item.md
  phase2/
    phase.md
    a/item.md
    g-solver/           a group (optional): folder name g-<slug>
      group.md          the group: name, nickname, status, color, order, summary
      b/item.md         an item in the group: its id is still phase2/b
      g-tuning/         a group nested inside g-solver (any depth)
        group.md
        c/item.md       phase2/c
  ...
```

- **Phases** are columns, laid out left to right by their number (`phase1`, `phase2`, ..., `phase10`: numeric sort, not lexical). Inserting a phase between 2 and 3 renumbers the later folders and rewrites every link to them (the tool does this; by hand, use `roadmap mv`).
- **Items** are boxes inside a phase column, top to bottom by folder name (`a`..`z`, then `aa`, `ab`, ...; `order:` in frontmatter overrides).
- **Groups** (optional) are rectangles inside a phase holding related items; groups nest. A folder whose name starts with `g-` is a group; any other folder is an item. Groups and items inside one container sort by folder name (`order:` overrides).
- **Ids:** a phase is `phaseN`; an item is `phaseN/x` (e.g. `phase3/b`); a group is `phaseN/g-<slug>` (e.g. `phase3/g-solver`). Item and group ids leave out the group folders between the phase and the entry, so **moving an item or group between groups of the same phase never changes any id**. For that, item letters and group slugs are unique per phase across all its groups (`check` reports duplicates). Moving to another phase does change the id (the tool picks a free letter/slug there and rewrites links).

## `roadmap.md`

```markdown
---
title: Particle
subtitle: GPU particle space simulator → survival game
---
Free text shown in the header / info panel.
```

## `phase.md`

```markdown
---
name: Rigid bodies and contact        # full name (shown at mid/near zoom)
nickname: Bodies                       # 1–2 words, shown when zoomed far out
status: done                           # done | active | planned | idea | dropped
color: "#3b82f6"                       # optional; default comes from status
dates: 2026-10-20 .. 2026-10-28        # optional free text
width: 900                             # optional: size in page pixels (sets the grid's columns); see Layout
height: 700                            # optional: minimum height
---
One-paragraph summary of the phase: what it delivers and why it exists.
```

## `group.md`

```markdown
---
name: Solver work            # required: shown in the group's header
nickname: Solver             # optional: shown when zoomed far out
status: active               # optional: done | active | planned | idea | dropped;
                             #   without it the status comes from the items inside (see below)
color: "#3b82f6"             # optional; default comes from status
order: 2                     # optional sort key among its siblings (items and groups)
width: 640                   # optional: size in page pixels (sets the grid's columns)
height: 400                  # optional: minimum height
---
One-paragraph summary of the group.
```

- The folder name is `g-` plus lowercase letters, digits and dashes. `roadmap add-group` makes it from the name (`"Solver work"` -> `g-solver-work`, `-2` if taken in the phase); renaming the group later keeps the folder and id.
- Status from items (when `status` is absent), over every item in the group and its sub-groups: none -> planned; all dropped -> dropped; all non-dropped done -> done; any active or done -> active; all idea -> idea; otherwise planned.
- Groups have no `links`; items link to a group like to a phase (`links: [phase3/g-solver]`).

### Layout

Phases sit left to right. Inside a phase or group, its items and groups (one list: items first, then groups, unless `order:` says otherwise) form a near-square grid: `ceil(sqrt(n))` columns, filled row by row; cells are left/top aligned and a column is as wide as its widest cell. `width:` (page pixels) instead picks the most columns that fit in that width, and `width:`/`height:` are minimum sizes (spare room goes to the right/bottom edge). Without them the size is automatic; each phase takes its own height. Collapsing a group (in the page) shows just its header; links to items inside it end at that header.

Links are drawn as orthogonal wires through the gaps: out of the source's right side, down the column gap to the row gap below, along it and out through the container's right border (a dot), up to the container both ends share, then in through left borders (dots) to the target's left side. A link between non-adjacent phases runs just below the phases it passes. Each link gets its own lane in every gap it uses, and gaps widen to fit their lanes. Links are coloured per source item (a 10-colour palette).

## `item.md`

```markdown
---
title: XPBD rigid bodies
nickname: XPBD                 # optional short label for far zoom
status: done                   # done | active | planned | idea | dropped
color: "#22c55e"               # optional override (any CSS colour)
bold: false                    # optional: emphasise the box (milestones)
links: [phase3/a, phase3/c]    # outgoing edges: items (or whole phases) this one leads to
done: 2026-10-24               # optional date
refs: [docs/DECISIONS.md#xpbd, "commit 1a2b3c4"]   # optional pointers
order: 2                       # optional sort key inside the phase
---
**What:** one or two sentences.

**Why:** the reason it exists / the problem it solves.

**How:** the approach, key mechanisms.

**Notes:** outcomes, numbers, open issues. Markdown (bold, italics, lists, `code`, links).
```

- `links` are **outgoing** edges (this item → the entries it enables/leads to), like a linked list's `next`. A link target is an item id (`phase4/b`), a group id (`phase4/g-solver`) or a phase id (`phase4`). Incoming edges are derived, never stored. Links may point backwards or within a phase; the renderer draws them anyway.
- Unknown frontmatter keys are kept untouched on rewrite (forward compatibility).
- Only `title` (item) and `name` (phase) are required. Defaults: `status: planned`, no colour override, `bold: false`, `links: []`.
- The body is Markdown; the What/Why/How headings are a convention, not parsed.
- Frontmatter is a YAML subset: scalars, quoted strings, inline lists `[a, b]` and block lists (`- a`). Writers emit exactly the style above.

## Validation (`roadmap check`)

Errors: missing `phase.md`/`item.md`/`group.md`, missing required field, unknown status, a link to a non-existent id (item, group or phase), the same item letter or group slug used by two folders in one phase (`duplicate id`), a bad group folder name. Warnings: an item with no incoming or outgoing links (except in the first phase), duplicate titles, an empty group.

## Tool notes (WebRoadmap 0.1)

Groups (`g-*` folders with `group.md`) were added; later the grid layout replaced column stacks (`width`/`height` on phases and groups, `roadmap arrange`; the older `items_per_column` setting is ignored); roadmaps without them read exactly as before. An older tool reads a `g-*` folder as an item without `item.md` (an error), so update the tool before adding groups. Behaviour worth knowing:

- Writers emit LF line endings; readers accept CRLF.
- New items take the first letter in `a..z, aa, ...` that is free in the whole phase (all groups included); any other lowercase folder name not starting with `g-` is still read as an item id.
- `roadmap arrange CONTAINER ID...` (what drag and drop calls) moves each listed item/group into the phase or group (same rules as `mv`) and writes `order: 1..N` over the container's children, listed ones first.
- `width`/`height` must be positive numbers (`check` errors otherwise); `set` rounds them to whole pixels and refuses them on items.
- `roadmap mv` of an item or group within its phase only moves the folder. To another phase, the moved item (or each item and group inside a moved group) keeps its letter/slug when free there, otherwise takes a free one; every link is rewritten.
- `roadmap rm GROUP` refuses a group that still holds items or groups; `--force` deletes it with everything inside and drops links to all of them.
- A changed key is rewritten in the canonical style above; untouched keys (with their comments and quoting) are kept byte for byte, and new keys are inserted at their position in the order above.
- `roadmap add-phase --after phase0` inserts before `phase1`. Phase operations close numbering gaps.

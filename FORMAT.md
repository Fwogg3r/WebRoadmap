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
  ...
```

- **Phases** are columns, laid out left to right by their number (`phase1`, `phase2`, ..., `phase10`: numeric sort, not lexical). Inserting a phase between 2 and 3 renumbers the later folders and rewrites every link to them (the tool does this; by hand, use `roadmap mv`).
- **Items** are boxes inside a phase column, top to bottom by folder name (`a`..`z`, then `aa`, `ab`, ...; `order:` in frontmatter overrides).
- **Ids:** an item is `phaseN/x` (e.g. `phase3/b`), a phase is `phaseN`. Ids are folder paths, so they are stable until someone moves a folder.

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
---
One-paragraph summary of the phase: what it delivers and why it exists.
```

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

- `links` are **outgoing** edges (this item → the entries it enables/leads to), like a linked list's `next`. A link target is an item id (`phase4/b`) or a phase id (`phase4`). Incoming edges are derived, never stored. Links may point backwards or within a phase; the renderer draws them anyway.
- Unknown frontmatter keys are kept untouched on rewrite (forward compatibility).
- Only `title` (item) and `name` (phase) are required. Defaults: `status: planned`, no colour override, `bold: false`, `links: []`.
- The body is Markdown; the What/Why/How headings are a convention, not parsed.
- Frontmatter is a YAML subset: scalars, quoted strings, inline lists `[a, b]` and block lists (`- a`). Writers emit exactly the style above.

## Validation (`roadmap check`)

Errors: missing `phase.md`/`item.md`, missing required field, unknown status, a link to a non-existent id. Warnings: an item with no incoming or outgoing links (except in the first phase), duplicate titles.

## Tool notes (WebRoadmap 0.1)

No fields were added to the format. Behaviour worth knowing:

- Writers emit LF line endings; readers accept CRLF.
- New items take the first free folder name in `a..z, aa, ...`; any other lowercase folder name is still read as an item id.
- A changed key is rewritten in the canonical style above; untouched keys (with their comments and quoting) are kept byte for byte, and new keys are inserted at their position in the order above.
- `roadmap add-phase --after phase0` inserts before `phase1`. Phase operations close numbering gaps.

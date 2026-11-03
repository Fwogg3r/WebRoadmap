---
name: roadmap
description: Read and update a project's roadmap in docs/roadmap/ (WebRoadmap format) with the `roadmap` CLI. Use when asked to add or update roadmap entries, mark items or phases done/active, link roadmap items, add or reorder phases, check the roadmap, or show it in the browser ("update the roadmap", "mark phase 3 done", "add this to the roadmap").
---

# Roadmap (WebRoadmap)

The roadmap is plain files in `docs/roadmap/` (spec: FORMAT.md in github.com/Fwogg3r/WebRoadmap):
`roadmap.md`, `phaseN/phase.md` (a column), `phaseN/x/item.md` (a box; id `phaseN/x`).
Item frontmatter: `title`, `nickname`, `status` (done | active | planned | idea | dropped),
`color`, `bold` (milestone), `links` (outgoing: ids of items or phases this one leads to),
optional `done`, `refs`, `order`; body = Markdown notes (**What / Why / How / Notes**).

Always go through the CLI (it renumbers phases and rewrites links); it finds `docs/roadmap`
by walking up from the cwd (`--dir` overrides). Add `--json` for machine-readable output.

```sh
roadmap list [--json]                    # overview: phases, items, status, links
roadmap show phase3/b                    # one entry with notes and incoming links
roadmap add phase3 --title "Swept contact" --nickname Sweep --status planned \
        --link phase4/a --body-file notes.md      # prints the new id
roadmap set phase3/b status=done done=2026-11-03  # any field; body=@file; key= removes
roadmap set phase3 status=done                     # phases too (name, nickname, status, color, dates, summary=@file)
roadmap link phase3/b phase4/a           # / roadmap unlink A B
roadmap add-phase --name "Energy" --nickname Energy [--after phase2]
roadmap mv phase3/b phase5               # item to another phase (new id printed)
roadmap mv phase5 2                      # phase to position 2 (renumbers, rewrites links)
roadmap rm phase3/b                      # drops links to it
roadmap check                            # exit 1 on errors (dangling links, bad status, ...)
roadmap serve                            # prints http://127.0.0.1:PORT (never opens a window)
```

Rules:
- **Every change to a project that has `docs/roadmap/` updates the roadmap in the same commit**
  (status on start/finish, outcomes in Notes, new entries for new work or findings). If the
  project's CLAUDE.md lacks the managed rule block, run `roadmap agent-rule` to add it.
- `roadmap init` (new project) also adds that rule to CLAUDE.md; `roadmap serve` never creates folders.
- Run `roadmap check` after changes; fix errors before finishing.
- Ids change on `mv` and phase insertion: re-read ids from the command output, never guess.
- Keep notes short: What / Why / How, then outcomes or numbers under **Notes**.
- Mark an item `done` (and set `done: YYYY-MM-DD`) when its work is merged; a phase is `done`
  when all its non-dropped items are.
- A running `roadmap serve` page picks up file changes within a second; no restart needed.

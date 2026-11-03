# WebRoadmap

A portable, local, interactive roadmap for any project. The roadmap is a plain folder of
Markdown files (`docs/roadmap/`) that people, coding agents and git all edit; `roadmap serve`
shows it as a zoomable left-to-right box map you can also edit in the browser.

| Far: phases | Mid: items and links | Near: full notes |
|---|---|---|
| ![far](docs/screenshots/far.png) | ![mid](docs/screenshots/mid.png) | ![near](docs/screenshots/near.png) |

![editing an item](docs/screenshots/edit-light.png)

- **Phases are columns**, items are boxes stacked in them, `links` are curved arrows.
- **Semantic zoom:** far = phase blocks with nickname, name and a progress bar; mid = item
  titles, status chips and arrows; near = the whole box with its What/Why/How notes (Markdown).
- **Status colours:** done green, active amber, planned grey, idea dashed purple, dropped faded and
  struck through; any entry can override its colour; `bold: true` marks milestones.
- **Live:** the page follows the files on disk within a second (agent edits, `git pull`). If
  you are mid-edit it shows a "changed on disk" notice instead of overwriting your form.
- No build step, no dependencies: Python standard library + plain HTML/CSS/JS.

## Install

Needs [uv](https://docs.astral.sh/uv/).

```sh
uv tool install C:\path\to\WebRoadmap        # or: uv tool install git+https://github.com/Fwogg3r/WebRoadmap
roadmap --version
```

Update after pulling: `uv tool install --reinstall C:\path\to\WebRoadmap`.

## Start a roadmap in a project

```sh
cd my-project
roadmap init              # creates docs/roadmap/ with roadmap.md and a sample phase1/a
roadmap serve             # prints http://127.0.0.1:8790 (first free port from 8790); opens nothing
```

Open the printed URL yourself (`roadmap serve --open` opens it for you). `roadmap` finds
`docs/roadmap/` by walking up from the current folder; `--dir PATH` points elsewhere.
`examples/demo/` is a sample roadmap: `roadmap --dir examples/demo/docs/roadmap serve`.

## The folder

```
docs/roadmap/
  roadmap.md          title, subtitle (frontmatter) + intro text
  phase1/phase.md     a column: name, nickname, status, color, dates + summary
  phase1/a/item.md    a box: title, nickname, status, color, bold, links, done, refs, order + notes
```

Ids are paths: `phase3` (phase), `phase3/b` (item). `links: [phase4/a, phase5]` are outgoing
edges to items or whole phases. Unknown frontmatter keys are preserved. Full spec:
[FORMAT.md](FORMAT.md).

## Editing in the browser

- **Click** a box or phase header: side panel with the rendered notes, outgoing (*Leads to*)
  and incoming (*Comes from*) links, problems.
- **Edit:** title/name, nickname, status, colour (picker, or *Reset* to the status colour),
  bold, phase (moves the item and rewrites links), links (pick from the dropdown, or
  *Pick on map* and click the target box), Markdown notes with a live preview
  (**B**/*I* buttons, Ctrl+B, Ctrl+I; Ctrl+S or Ctrl+Enter saves).
- **Add:** the `+` at the foot of a column adds an item; `+ Phase` (top bar, or the column after
  the last phase) adds a phase; a phase's *+ Phase after* inserts one and renumbers the later
  folders, rewriting every link. *←/→* move a phase.
- **Delete** (with confirmation) removes the entry and every link to it.
- Hovering or selecting a box highlights its whole upstream and downstream chain.
- **Search** (`/`): text in titles, notes and ids; `status:active` filters by status; Enter
  jumps to the next hit.
- Validation problems (dangling links, unknown status, missing files) show as a red button in
  the top bar and a red outline/`!` on the box.

## Controls

| | Mouse / keyboard | Touch |
|---|---|---|
| Pan | drag with the left or middle button; arrow keys (Shift = faster) | one-finger drag (also after tap-and-hold) |
| Zoom | wheel about the cursor; Ctrl+wheel / trackpad pinch; `+` `-` | two-finger pinch |
| Sideways | Shift+wheel | |
| Fit all | `0` or *Fit* | *Fit* |
| Zoom to a box | double-click | |
| Close panel / cancel link pick | Esc | × |

The view is remembered per roadmap (browser storage). URL hash parameters make shareable
views: `#zoom=far|mid|near|fit|0.8&focus=phase3/b&select=phase3/b&edit=phase3/b`.

## Agents: CLI and Claude skill

Every command takes `--json` for machine-readable output; the HTTP API (`POST /api/<op>`)
runs the same Python functions, so the page and the CLI always agree.

```sh
roadmap list [--json]
roadmap show phase3/b
roadmap add phase3 --title "Swept contact" --nickname Sweep --status planned --link phase4/a --body-file notes.md
roadmap add-phase --name "Energy" --nickname Energy [--after phase2]     # phase0 = insert first
roadmap set phase3/b status=done done=2026-11-03 body=@notes.md          # key= removes a field
roadmap link phase3/b phase4/a        # roadmap unlink A B
roadmap mv phase3/b phase5            # item to another phase (prints the new id)
roadmap mv phase5 2                   # phase to position 2: renumbers folders, rewrites links
roadmap renumber                      # close gaps after hand edits
roadmap rm phase3/b                   # removes links to it too
roadmap check                         # exit 1 on errors
```

`roadmap install-skill` copies the bundled Claude skill (`src/webroadmap/skill/SKILL.md`,
name `roadmap`) to `~/.claude/skills/roadmap/` (overwrites, with a version header), so Claude
Code uses the CLI when asked to add or update roadmap entries or mark phases done.

### Keeping it current

`roadmap init` adds a managed "every change updates the roadmap" rule to the project's
`CLAUDE.md` (created if missing; `--no-agent-rule` skips it). For a project that already has a
roadmap, run `roadmap agent-rule` (`--file AGENTS.md` for other agents). The block sits between
`<!-- roadmap-rule:start -->` / `<!-- roadmap-rule:end -->` markers, so re-running refreshes it in
place after a WebRoadmap update; line endings are kept. It tells agents to set an entry's status
when work starts or finishes, add outcomes to its Notes, add entries for new work, and run
`roadmap check` before committing, in the same commit as the change.

`roadmap serve` does not create anything: without a `docs/roadmap/` above the current folder it
stops and points at `roadmap init`, so running it from the wrong folder can't scatter roadmaps.

## Phone / LAN

`roadmap serve --host 0.0.0.0` listens on every interface and prints the LAN address. There is
**no authentication**: anyone on the network can edit the roadmap, so only do this on a trusted
network. The page works on a phone (bottom-sheet panel, touch pan and pinch).

![phone](docs/screenshots/phone.png)

## Development

```sh
uv run pytest                       # format round-trip, mutations, check, HTTP API
uv run tests/browser_check.py       # headless Chrome/Edge end-to-end check; rewrites docs/screenshots
uv run tests/browser_check.py --scheme light
```

The browser check never opens a visible window. Layout: `src/webroadmap/fmt.py` (frontmatter
parser/writer), `model.py` (every operation), `cli.py`, `server.py`, `static/` (the page).

"""The roadmap folder (FORMAT.md): load, validate and every mutation.

The CLI and the HTTP server both call these functions; nothing else touches
the files. Every mutation re-reads the folder first, so concurrent edits by
agents or git are seen, and writes LF text.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import threading
from dataclasses import dataclass, field
from pathlib import Path

from .fmt import Doc, FormatError

STATUSES = ("done", "active", "planned", "idea", "dropped")
PHASE_RE = re.compile(r"^phase(\d+)$")
SLUG_RE = re.compile(r"^[a-z]+$")
ITEM_KEYS = ("title", "nickname", "status", "color", "bold", "links", "done", "refs", "order")
PHASE_KEYS = ("name", "nickname", "status", "color", "dates")

# One process-wide lock: the server is threaded, and a mutation is a
# read-modify-write over several files.
LOCK = threading.RLock()


class RoadmapError(Exception):
    pass


# ---------------------------------------------------------------- discovery

def find_root(start: Path | None = None) -> Path | None:
    """Walk up from ``start`` looking for docs/roadmap (or being inside it)."""
    p = (start or Path.cwd()).resolve()
    for d in [p, *p.parents]:
        cand = d / "docs" / "roadmap"
        if (cand / "roadmap.md").is_file() or (cand.is_dir() and any(_phase_dirs(cand))):
            return cand
        if d.name == "roadmap" and (d / "roadmap.md").is_file():
            return d
    return None


def _phase_dirs(root: Path):
    if not root.is_dir():
        return
    for e in root.iterdir():
        m = PHASE_RE.match(e.name)
        if m and e.is_dir():
            yield int(m.group(1)), e


def slug_key(name: str):
    return (len(name), name)


def slug_of(n: int) -> str:
    """0 -> a, 25 -> z, 26 -> aa (bijective base 26)."""
    s = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(97 + r) + s
    return s


# ---------------------------------------------------------------- reading

def _read(path: Path) -> Doc:
    return Doc.parse(path.read_text(encoding="utf-8"))


def _write(path: Path, doc: Doc):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(doc.dump())


@dataclass
class Item:
    id: str
    slug: str
    path: Path
    doc: Doc | None
    error: str | None = None

    @property
    def links(self) -> list[str]:
        v = self.doc.get("links", []) if self.doc else []
        return [str(x) for x in (v if isinstance(v, list) else [v])]


@dataclass
class Phase:
    id: str
    num: int
    path: Path
    doc: Doc | None
    items: list[Item] = field(default_factory=list)
    error: str | None = None


@dataclass
class Roadmap:
    root: Path
    doc: Doc | None
    phases: list[Phase]

    def phase(self, pid: str) -> Phase:
        for p in self.phases:
            if p.id == pid:
                return p
        raise RoadmapError(f"no phase {pid}")

    def item(self, iid: str) -> Item:
        pid, _, _ = iid.partition("/")
        for it in self.phase(pid).items:
            if it.id == iid:
                return it
        raise RoadmapError(f"no item {iid}")

    def items(self):
        for p in self.phases:
            yield from p.items

    def ids(self) -> set[str]:
        return {p.id for p in self.phases} | {i.id for i in self.items()}


def _load_doc(path: Path):
    if not path.is_file():
        return None, f"missing {path.name}"
    try:
        return _read(path), None
    except (FormatError, UnicodeDecodeError) as e:
        return None, f"{path.name}: {e}"


def load(root: Path) -> Roadmap:
    root = Path(root)
    if not root.is_dir():
        raise RoadmapError(f"no roadmap folder at {root}")
    rdoc = _read(root / "roadmap.md") if (root / "roadmap.md").is_file() else None
    phases = []
    for num, pdir in sorted(_phase_dirs(root)):
        doc, err = _load_doc(pdir / "phase.md")
        ph = Phase(f"phase{num}", num, pdir, doc, error=err)
        items = []
        for e in pdir.iterdir():
            if e.is_dir() and not e.name.startswith("."):
                idoc, ierr = _load_doc(e / "item.md")
                items.append(Item(f"{ph.id}/{e.name}", e.name, e, idoc, error=ierr))
        items.sort(key=lambda it: slug_key(it.slug))

        def order(ix_it):
            ix, it = ix_it
            o = it.doc.get("order") if it.doc else None
            return (o if isinstance(o, (int, float)) else ix, slug_key(it.slug))

        ph.items = [it for _, it in sorted(enumerate(items), key=order)]
        phases.append(ph)
    return Roadmap(root, rdoc, phases)


def version(root: Path) -> str:
    """Cheap change token: paths, sizes and mtimes of every file."""
    h = hashlib.sha1()
    for dp, dns, fns in os.walk(root):
        dns.sort()
        for fn in sorted(fns):
            p = os.path.join(dp, fn)
            try:
                st = os.stat(p)
            except OSError:
                continue
            h.update(f"{os.path.relpath(p, root)}|{st.st_size}|{st.st_mtime_ns}\n".encode())
    return h.hexdigest()[:16]


# ---------------------------------------------------------------- JSON view

def _clean(v):
    if isinstance(v, list):
        return [_clean(x) for x in v]
    return v


def item_json(it: Item) -> dict:
    d = dict(it.doc.fields) if it.doc else {}
    out = {k: _clean(v) for k, v in d.items()}
    out.update(
        id=it.id,
        slug=it.slug,
        title=str(d.get("title") or it.slug),
        status=str(d.get("status") or "planned"),
        bold=bool(d.get("bold") or False),
        links=it.links,
        body=it.doc.body.strip("\n") if it.doc else "",
    )
    if it.error:
        out["error"] = it.error
    return out


def phase_json(ph: Phase) -> dict:
    d = dict(ph.doc.fields) if ph.doc else {}
    out = {k: _clean(v) for k, v in d.items()}
    out.update(
        id=ph.id,
        num=ph.num,
        name=str(d.get("name") or ph.id),
        status=str(d.get("status") or "planned"),
        summary=ph.doc.body.strip("\n") if ph.doc else "",
        items=[item_json(i) for i in ph.items],
    )
    if ph.error:
        out["error"] = ph.error
    return out


def to_json(rm: Roadmap) -> dict:
    d = dict(rm.doc.fields) if rm.doc else {}
    return {
        **{k: _clean(v) for k, v in d.items()},
        "title": str(d.get("title") or rm.root.parent.parent.name or "Roadmap"),
        "intro": rm.doc.body.strip("\n") if rm.doc else "",
        "root": str(rm.root),
        "phases": [phase_json(p) for p in rm.phases],
    }


def show(root: Path, eid: str) -> dict:
    rm = load(root)
    if "/" in eid:
        it = rm.item(eid)
        j = item_json(it)
        j["incoming"] = [i.id for i in rm.items() if eid in i.links]
        return j
    j = phase_json(rm.phase(eid))
    j["incoming"] = [i.id for i in rm.items() if eid in i.links]
    return j


# ---------------------------------------------------------------- validation

def check(root: Path) -> dict:
    rm = load(root)
    errors, warnings = [], []
    if rm.doc is None:
        warnings.append({"id": "roadmap", "msg": "missing roadmap.md"})
    ids = rm.ids()
    incoming: dict[str, int] = {}
    for it in rm.items():
        for l in it.links:
            incoming[l] = incoming.get(l, 0) + 1
    titles: dict[str, list[str]] = {}
    first = rm.phases[0].id if rm.phases else None
    for ph in rm.phases:
        if ph.error:
            errors.append({"id": ph.id, "msg": ph.error})
        elif not ph.doc.get("name"):
            errors.append({"id": ph.id, "msg": "phase has no name"})
        if ph.doc and str(ph.doc.get("status", "planned")) not in STATUSES:
            errors.append({"id": ph.id, "msg": f"unknown status '{ph.doc.get('status')}'"})
        for it in ph.items:
            if it.error:
                errors.append({"id": it.id, "msg": it.error})
                continue
            if not it.doc.get("title"):
                errors.append({"id": it.id, "msg": "item has no title"})
            st = str(it.doc.get("status", "planned"))
            if st not in STATUSES:
                errors.append({"id": it.id, "msg": f"unknown status '{st}'"})
            for l in it.links:
                if l not in ids:
                    errors.append({"id": it.id, "msg": f"link to non-existent '{l}'", "link": l})
            if ph.id != first and not it.links and not incoming.get(it.id):
                warnings.append({"id": it.id, "msg": "item has no incoming or outgoing links"})
            t = str(it.doc.get("title", "")).strip().lower()
            if t:
                titles.setdefault(t, []).append(it.id)
    for t, idl in titles.items():
        if len(idl) > 1:
            for i in idl:
                warnings.append({"id": i, "msg": f"duplicate title (also {', '.join(x for x in idl if x != i)})"})
    return {"ok": not errors, "errors": errors, "warnings": warnings}


# ---------------------------------------------------------------- mutations

def _rewrite_links(rm: Roadmap, mapping):
    """Apply ``mapping(id) -> new id | None`` (None drops the link) to every item."""
    for it in rm.items():
        if not it.doc:
            continue
        old = it.links
        new = []
        for l in old:
            n = mapping(l)
            if n is not None and n not in new:
                new.append(n)
        if new != old:
            _insert(it.doc, "links", new, ITEM_KEYS)
            _write(it.path / "item.md", it.doc)


def _check_status(v):
    if v is not None and str(v) not in STATUSES:
        raise RoadmapError(f"unknown status '{v}' (use {', '.join(STATUSES)})")


def _norm_links(v) -> list[str]:
    if v is None:
        return []
    if isinstance(v, str):
        v = [x.strip() for x in v.replace(",", " ").split()]
    return [str(x) for x in v if str(x).strip()]


def _apply_fields(doc: Doc, fields: dict, keys: tuple):
    for k, v in fields.items():
        if k in ("id", "body", "summary", "items", "incoming", "slug", "num", "error"):
            continue
        if isinstance(v, str) and v == "":
            v = None
        if k == "status":
            _check_status(v)
        if k == "links":
            v = _norm_links(v)
            if not v:
                v = [] if "links" in doc.fields else None
        if k == "bold":
            v = bool(v) if v is not None else None
            if v is False and "bold" not in doc.fields:
                v = None
        if k in ("title", "name") and v is None:
            raise RoadmapError(f"'{k}' is required")
        if v is not None and k not in doc.fields:
            _insert(doc, k, v, keys)
        else:
            doc.set(k, v)


def _insert(doc: Doc, key: str, value, keys: tuple):
    """Add a new key at its FORMAT.md position, leaving existing keys in place."""
    if key in doc.fields or key not in keys:
        doc.fields[key] = value
        return
    rank = keys.index(key)
    out, done = {}, False
    for k, v in doc.fields.items():
        if not done and k in keys and keys.index(k) > rank:
            out[key] = value
            done = True
        out[k] = v
    if not done:
        out[key] = value
    doc.fields = out


def _next_slug(ph: Phase) -> str:
    taken = {e.name for e in ph.path.iterdir()} if ph.path.is_dir() else set()
    n = 0
    while slug_of(n) in taken:
        n += 1
    return slug_of(n)


def add_item(root: Path, phase: str, title: str, *, body: str = "", **fields) -> str:
    with LOCK:
        rm = load(root)
        ph = rm.phase(phase)
        _check_status(fields.get("status"))
        links = _norm_links(fields.pop("links", None))
        ids = rm.ids()
        bad = [l for l in links if l not in ids]
        if bad:
            raise RoadmapError(f"link target(s) not found: {', '.join(bad)}")
        slug = _next_slug(ph)
        doc = Doc({"title": title})
        _apply_fields(doc, {k: v for k, v in fields.items() if v is not None}, ITEM_KEYS)
        if "status" not in doc.fields:
            _insert(doc, "status", "planned", ITEM_KEYS)
        _insert(doc, "links", links, ITEM_KEYS)
        doc.body = body or ""
        _write(ph.path / slug / "item.md", doc)
        return f"{ph.id}/{slug}"


def set_entry(root: Path, eid: str, fields: dict, body: str | None = None) -> str:
    with LOCK:
        rm = load(root)
        if "/" in eid:
            ent = rm.item(eid)
            path, keys = ent.path / "item.md", ITEM_KEYS
        else:
            ent = rm.phase(eid)
            path, keys = ent.path / "phase.md", PHASE_KEYS
        doc = ent.doc or Doc({})
        if "links" in fields and "/" not in eid:
            raise RoadmapError("phases have no links")
        if "links" in fields:
            ids = rm.ids()
            bad = [l for l in _norm_links(fields["links"]) if l not in ids]
            if bad:
                raise RoadmapError(f"link target(s) not found: {', '.join(bad)}")
        _apply_fields(doc, fields, keys)
        for alias in ("body", "summary"):
            if alias in fields and body is None:
                body = fields[alias]
        if body is not None:
            doc.body = body
        _write(path, doc)
        return eid


def link(root: Path, a: str, b: str) -> list[str]:
    with LOCK:
        rm = load(root)
        it = rm.item(a)
        if b not in rm.ids():
            raise RoadmapError(f"no entry {b}")
        if b == a:
            raise RoadmapError("an item cannot link to itself")
        links = it.links
        if b not in links:
            links.append(b)
            _insert(it.doc, "links", links, ITEM_KEYS)
            _write(it.path / "item.md", it.doc)
        return links


def unlink(root: Path, a: str, b: str) -> list[str]:
    with LOCK:
        rm = load(root)
        it = rm.item(a)
        links = [l for l in it.links if l != b]
        _insert(it.doc, "links", links, ITEM_KEYS)
        _write(it.path / "item.md", it.doc)
        return links


def move_item(root: Path, iid: str, phase: str) -> str:
    with LOCK:
        rm = load(root)
        it = rm.item(iid)
        ph = rm.phase(phase)
        if it.id.startswith(ph.id + "/"):
            return it.id
        slug = _next_slug(ph)
        new = f"{ph.id}/{slug}"
        shutil.move(str(it.path), str(ph.path / slug))
        rm = load(root)
        _rewrite_links(rm, lambda l: new if l == iid else l)
        return new


def _renumber(root: Path, order: list[int], drop: int | None = None) -> dict[int, int]:
    """Rename phase folders so ``order`` (old numbers) become phase1..N.

    ``drop`` is a phase number already deleted from disk whose links go away.
    Returns old -> new numbers and rewrites every link.
    """
    mapping = {old: i + 1 for i, old in enumerate(order)}
    moves = {o: n for o, n in mapping.items() if o != n}
    tmp = {}
    for o in moves:
        t = root / f".renumber-phase{o}"
        os.rename(root / f"phase{o}", t)
        tmp[o] = t
    for o, n in moves.items():
        os.rename(tmp[o], root / f"phase{n}")

    def remap(l: str):
        pid, sep, rest = l.partition("/")
        m = PHASE_RE.match(pid)
        if not m:
            return l
        num = int(m.group(1))
        if drop is not None and num == drop:
            return None
        if num in mapping:
            return f"phase{mapping[num]}{sep}{rest}"
        return l

    _rewrite_links(load(root), remap)
    return mapping


def add_phase(root: Path, name: str, nickname: str | None = None, *, after: str | None = None,
              body: str = "", **fields) -> str:
    """Add a phase at the end, or right after ``after`` ("phaseN", or "phase0" for first)."""
    with LOCK:
        rm = load(root)
        nums = [p.num for p in rm.phases]
        _check_status(fields.get("status"))
        if after is None:
            pos = len(nums)
        else:
            m = PHASE_RE.match(after)
            if not m:
                raise RoadmapError(f"bad phase id {after}")
            a = int(m.group(1))
            if a != 0 and a not in nums:
                raise RoadmapError(f"no phase {after}")
            pos = 0 if a == 0 else nums.index(a) + 1
        _renumber(root, nums)  # compact first so the new number is free
        n = len(nums)
        # shift phases at index >= pos up by one, top down
        for i in range(n, pos, -1):
            os.rename(root / f"phase{i}", root / f"phase{i + 1}")
        shift = {i: (i + 1 if i > pos else i) for i in range(1, n + 1)}

        def remap(l):
            pid, sep, rest = l.partition("/")
            m = PHASE_RE.match(pid)
            if m and int(m.group(1)) in shift:
                return f"phase{shift[int(m.group(1))]}{sep}{rest}"
            return l

        _rewrite_links(load(root), remap)
        doc = Doc({"name": name})
        if nickname:
            fields["nickname"] = nickname
        _apply_fields(doc, {k: v for k, v in fields.items() if v is not None}, PHASE_KEYS)
        if "status" not in doc.fields:
            _insert(doc, "status", "planned", PHASE_KEYS)
        doc.body = body or ""
        _write(root / f"phase{pos + 1}" / "phase.md", doc)
        return f"phase{pos + 1}"


def move_phase(root: Path, pid: str, position: int) -> str:
    """Move a phase to 1-based ``position``; renumbers all phases."""
    with LOCK:
        rm = load(root)
        ph = rm.phase(pid)
        nums = [p.num for p in rm.phases]
        nums.remove(ph.num)
        position = max(1, min(position, len(nums) + 1))
        nums.insert(position - 1, ph.num)
        mapping = _renumber(root, nums)
        return f"phase{mapping[ph.num]}"


def renumber(root: Path) -> dict:
    """Close gaps (phase1, phase3 -> phase1, phase2)."""
    with LOCK:
        rm = load(root)
        m = _renumber(root, [p.num for p in rm.phases])
        return {f"phase{o}": f"phase{n}" for o, n in m.items() if o != n}


def remove(root: Path, eid: str) -> list[str]:
    """Delete an item or a whole phase; drops links to it. Returns removed ids."""
    with LOCK:
        rm = load(root)
        if "/" in eid:
            it = rm.item(eid)
            shutil.rmtree(it.path)
            _rewrite_links(load(root), lambda l: None if l == eid else l)
            return [eid]
        ph = rm.phase(eid)
        gone = [ph.id] + [i.id for i in ph.items]
        shutil.rmtree(ph.path)
        nums = [p.num for p in rm.phases if p.num != ph.num]
        _renumber(root, nums, drop=ph.num)
        return gone


def move(root: Path, eid: str, target: str) -> str:
    """CLI/API ``mv``: item -> phase, or phase -> position (number or phaseN)."""
    if "/" in eid:
        return move_item(root, eid, target)
    m = PHASE_RE.match(str(target))
    pos = int(m.group(1)) if m else int(target)
    return move_phase(root, eid, pos)


# ---------------------------------------------------------------- init

SAMPLE_ROADMAP = """---
title: {title}
subtitle: What this project is heading towards
---
Roadmap for {title}. Phases are columns (left to right), items are boxes.
Edit in the browser (`roadmap serve`) or with the `roadmap` CLI.
"""

SAMPLE_PHASE = """---
name: First steps
nickname: Start
status: active
---
The first phase: what it delivers and why it exists.
"""

SAMPLE_ITEM = """---
title: Set up the roadmap
nickname: Roadmap
status: active
bold: true
links: []
---
**What:** a `docs/roadmap/` folder that people, agents and git all edit.

**Why:** one picture of where the project is going.

**How:** `roadmap serve` for the map, `roadmap add` / `roadmap set` for agents.
"""


def init(target: Path, title: str | None = None) -> Path:
    target = Path(target)
    root = target if target.name == "roadmap" else target / "docs" / "roadmap"
    if root.exists() and any(root.iterdir()):
        raise RoadmapError(f"{root} already exists and is not empty")
    title = title or (root.parent.parent.name if root.name == "roadmap" else target.name) or "Project"
    for rel, text in (
        ("roadmap.md", SAMPLE_ROADMAP.format(title=title)),
        ("phase1/phase.md", SAMPLE_PHASE),
        ("phase1/a/item.md", SAMPLE_ITEM),
    ):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
    return root


RULE_START = "<!-- roadmap-rule:start (managed by `roadmap agent-rule`) -->"
RULE_END = "<!-- roadmap-rule:end -->"
AGENT_RULE = """## Roadmap (keep it current)

- `docs/roadmap/` is this project's roadmap and its single source of truth for planned, active and finished work (WebRoadmap format; view it with `roadmap serve`).
- **Every change must update the roadmap in the same commit:** set the entry's status when work starts or finishes (`roadmap set ID status=active|done`), add outcomes and numbers to its **Notes**, add entries for new work, ideas or findings (`roadmap add PHASE --title ...`), and fix links (`roadmap link A B`). Run `roadmap check` before committing.
- This applies to agents and subagents too: a brief or task that changes the project names the roadmap entry it updates."""


def project_dir(root: Path) -> Path:
    """The project folder that owns a roadmap root (docs/roadmap -> the folder above docs)."""
    root = Path(root)
    if root.name == "roadmap" and root.parent.name == "docs":
        return root.parent.parent
    return root.parent


def add_agent_rule(project: Path, filename: str = "CLAUDE.md") -> tuple[Path, str]:
    """Insert or refresh the managed roadmap rule in the project's agent instructions.

    Returns (path, "created" | "added" | "updated" | "unchanged"). The block sits between
    markers, so re-running replaces it in place instead of appending a second copy; the
    file's line endings are kept.
    """
    path = Path(project) / filename
    block = f"{RULE_START}\n{AGENT_RULE}\n{RULE_END}"
    if not path.exists():
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(block + "\n")
        return path, "created"
    raw = path.read_bytes().decode("utf-8")
    nl = "\r\n" if "\r\n" in raw else "\n"
    text = raw.replace("\r\n", "\n")
    if RULE_START in text and RULE_END in text:
        a = text.index(RULE_START)
        b = text.index(RULE_END) + len(RULE_END)
        new = text[:a] + block + text[b:]
        state = "unchanged" if new == text else "updated"
    else:
        new = text.rstrip("\n") + "\n\n" + block + "\n"
        state = "added"
    if state != "unchanged":
        path.write_bytes(new.replace("\n", nl).encode("utf-8"))
    return path, state

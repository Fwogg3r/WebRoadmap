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
PHASE_KEYS = ("name", "nickname", "status", "color", "dates", "width", "height")
GROUP_KEYS = ("name", "nickname", "status", "color", "order", "width", "height")
GROUP_RE = re.compile(r"^g-[a-z0-9][a-z0-9-]*$")

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
    group: str | None = None  # id of the group folder it sits in (None = phase top level)

    @property
    def links(self) -> list[str]:
        v = self.doc.get("links", []) if self.doc else []
        return [str(x) for x in (v if isinstance(v, list) else [v])]


@dataclass
class Group:
    """A ``g-<slug>`` folder inside a phase or another group (holds ``group.md``)."""
    id: str
    slug: str
    path: Path
    doc: Doc | None
    parent: str  # phase id or group id
    items: list[Item] = field(default_factory=list)  # direct items
    groups: list["Group"] = field(default_factory=list)  # direct sub-groups
    error: str | None = None

    def walk_items(self):
        yield from self.items
        for g in self.groups:
            yield from g.walk_items()

    def walk_groups(self):
        for g in self.groups:
            yield g
            yield from g.walk_groups()


@dataclass
class Phase:
    id: str
    num: int
    path: Path
    doc: Doc | None
    items: list[Item] = field(default_factory=list)  # every item, groups included (tree order)
    error: str | None = None
    groups: list[Group] = field(default_factory=list)  # every group, nested ones included
    top_items: list[Item] = field(default_factory=list)
    top_groups: list[Group] = field(default_factory=list)


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

    def group(self, gid: str) -> Group:
        pid, _, _ = gid.partition("/")
        for g in self.phase(pid).groups:
            if g.id == gid:
                return g
        raise RoadmapError(f"no group {gid}")

    def items(self):
        for p in self.phases:
            yield from p.items

    def groups(self):
        for p in self.phases:
            yield from p.groups

    def ids(self) -> set[str]:
        return {p.id for p in self.phases} | {i.id for i in self.items()} | {g.id for g in self.groups()}


def is_group_id(eid: str) -> bool:
    _, sep, rest = str(eid).partition("/")
    return bool(sep) and rest.startswith("g-")


def is_item_id(eid: str) -> bool:
    return "/" in str(eid) and not is_group_id(eid)


def _load_doc(path: Path):
    if not path.is_file():
        return None, f"missing {path.name}"
    try:
        return _read(path), None
    except (FormatError, UnicodeDecodeError) as e:
        return None, f"{path.name}: {e}"


def _ordered(entries):
    """Sort by folder name, then let an ``order:`` key override the position."""
    entries.sort(key=lambda e: slug_key(e.slug))

    def order(ix_e):
        ix, e = ix_e
        o = e.doc.get("order") if e.doc else None
        return (o if isinstance(o, (int, float)) else ix, slug_key(e.slug))

    return [e for _, e in sorted(enumerate(entries), key=order)]


def _load_container(ph: Phase, cdir: Path, gid: str | None):
    """Read the items and groups directly inside ``cdir`` (a phase or group folder)."""
    items, groups = [], []
    for e in cdir.iterdir():
        if not e.is_dir() or e.name.startswith("."):
            continue
        if e.name.startswith("g-"):
            gdoc, gerr = _load_doc(e / "group.md")
            if gerr is None and not GROUP_RE.match(e.name):
                gerr = f"bad group folder name '{e.name}' (use g-<lowercase letters, digits, dashes>)"
            g = Group(f"{ph.id}/{e.name}", e.name, e, gdoc, gid or ph.id, error=gerr)
            g.items, g.groups = _load_container(ph, e, g.id)
            groups.append(g)
        else:
            idoc, ierr = _load_doc(e / "item.md")
            items.append(Item(f"{ph.id}/{e.name}", e.name, e, idoc, error=ierr, group=gid))
    return _ordered(items), _ordered(groups)


def load(root: Path) -> Roadmap:
    root = Path(root)
    if not root.is_dir():
        raise RoadmapError(f"no roadmap folder at {root}")
    rdoc = _read(root / "roadmap.md") if (root / "roadmap.md").is_file() else None
    phases = []
    for num, pdir in sorted(_phase_dirs(root)):
        doc, err = _load_doc(pdir / "phase.md")
        ph = Phase(f"phase{num}", num, pdir, doc, error=err)
        ph.top_items, ph.top_groups = _load_container(ph, pdir, None)
        ph.items = list(ph.top_items)
        for g in ph.top_groups:
            ph.groups.append(g)
            ph.groups.extend(g.walk_groups())
            ph.items.extend(g.walk_items())
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
        group=it.group,
        body=it.doc.body.strip("\n") if it.doc else "",
    )
    if it.error:
        out["error"] = it.error
    return out


def derived_status(items) -> str:
    """A group's status from its items: all done -> done, any progress -> active, ..."""
    sts = [str(i.doc.get("status") or "planned") if i.doc else "planned" for i in items]
    live = [s for s in sts if s != "dropped"]
    if not sts:
        return "planned"
    if not live:
        return "dropped"
    if all(s == "done" for s in live):
        return "done"
    if any(s in ("active", "done") for s in live):
        return "active"
    if all(s == "idea" for s in live):
        return "idea"
    return "planned"


def group_json(g: Group) -> dict:
    d = dict(g.doc.fields) if g.doc else {}
    out = {k: _clean(v) for k, v in d.items()}
    explicit = d.get("status")
    out.update(
        id=g.id,
        slug=g.slug,
        name=str(d.get("name") or g.slug),
        status=str(explicit) if explicit else derived_status(list(g.walk_items())),
        status_from_items=not explicit,
        parent=g.parent,
        summary=g.doc.body.strip("\n") if g.doc else "",
        items=[i.id for i in g.items],
        groups=[x.id for x in g.groups],
    )
    if g.error:
        out["error"] = g.error
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
        groups=[group_json(g) for g in ph.groups],
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
    if is_group_id(eid):
        j = group_json(rm.group(eid))
        j["incoming"] = [i.id for i in rm.items() if eid in i.links]
        return j
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
        _check_size(ph, errors)
        if ph.doc and str(ph.doc.get("status", "planned")) not in STATUSES:
            errors.append({"id": ph.id, "msg": f"unknown status '{ph.doc.get('status')}'"})
        seen: dict[str, list] = {}
        for e in [*ph.items, *ph.groups]:
            seen.setdefault(e.id, []).append(e)
        for eid, es in seen.items():
            if len(es) > 1:
                where = ", ".join(x.path.relative_to(ph.path).as_posix() for x in es)
                errors.append({"id": eid, "msg": f"duplicate id: {len(es)} folders named '{es[0].slug}' in {ph.id} ({where})"})
        for g in ph.groups:
            if g.error:
                errors.append({"id": g.id, "msg": g.error})
                continue
            if not g.doc.get("name"):
                errors.append({"id": g.id, "msg": "group has no name"})
            if "status" in g.doc.fields and str(g.doc.get("status")) not in STATUSES:
                errors.append({"id": g.id, "msg": f"unknown status '{g.doc.get('status')}'"})
            _check_size(g, errors)
            if not g.items and not g.groups:
                warnings.append({"id": g.id, "msg": "empty group"})
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


def _check_size(ent, errors):
    for k in ("width", "height"):
        v = ent.doc.get(k) if ent.doc else None
        if v is not None and not (isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0):
            errors.append({"id": ent.id, "msg": f"{k} must be a positive number (got '{v}')"})


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
        if k in ("id", "body", "summary", "items", "incoming", "slug", "num", "error",
                 "group", "groups", "parent", "status_from_items"):
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
    """First free item letter in the phase: letters are unique across all its groups."""
    taken = {e.name for e in ph.path.iterdir()} if ph.path.is_dir() else set()
    taken |= {i.slug for i in ph.items}
    n = 0
    while slug_of(n) in taken:
        n += 1
    return slug_of(n)


def _group_slug(taken: set[str], name: str) -> str:
    """``g-<words of the name>``, made unique within the phase (``-2``, ``-3``, ...)."""
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:32].strip("-") or "group"
    base = "g-" + base
    slug, n = base, 2
    while slug in taken:
        slug, n = f"{base}-{n}", n + 1
    return slug


def _container(rm: Roadmap, target: str) -> tuple[Phase, Path, "Group | None"]:
    """A phase id or group id -> (phase, folder, group or None)."""
    if is_group_id(target):
        g = rm.group(target)
        return rm.phase(target.partition("/")[0]), g.path, g
    if "/" in target:
        raise RoadmapError(f"{target} is an item, not a phase or group")
    ph = rm.phase(target)
    return ph, ph.path, None


def add_item(root: Path, phase: str, title: str, *, body: str = "", group: str | None = None, **fields) -> str:
    """Add an item to a phase (or straight into ``group``, a group id in that phase)."""
    with LOCK:
        rm = load(root)
        if group:
            ph, cdir, _ = _container(rm, group)
            if phase and ph.id != phase:
                raise RoadmapError(f"group {group} is not in {phase}")
        else:
            ph = rm.phase(phase)
            cdir = ph.path
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
        _write(cdir / slug / "item.md", doc)
        return f"{ph.id}/{slug}"


def add_group(root: Path, phase: str | None, name: str, nickname: str | None = None, *,
              parent: str | None = None, body: str = "", **fields) -> str:
    """Add a group to a phase, or inside ``parent`` (a group id). Returns ``phaseN/g-<slug>``."""
    with LOCK:
        rm = load(root)
        if parent:
            ph, cdir, _ = _container(rm, parent)
            if phase and ph.id != phase:
                raise RoadmapError(f"group {parent} is not in {phase}")
        else:
            if not phase:
                raise RoadmapError("give a phase or a parent group")
            ph = rm.phase(phase)
            cdir = ph.path
        if not str(name or "").strip():
            raise RoadmapError("'name' is required")
        _check_status(fields.get("status"))
        slug = _group_slug({g.slug for g in ph.groups}, name)
        doc = Doc({"name": name})
        if nickname:
            fields["nickname"] = nickname
        _apply_fields(doc, {k: v for k, v in fields.items() if v is not None}, GROUP_KEYS)
        doc.body = body or ""
        _write(cdir / slug / "group.md", doc)
        return f"{ph.id}/{slug}"


def set_entry(root: Path, eid: str, fields: dict, body: str | None = None) -> str:
    with LOCK:
        rm = load(root)
        if is_group_id(eid):
            ent = rm.group(eid)
            path, keys = ent.path / "group.md", GROUP_KEYS
        elif "/" in eid:
            ent = rm.item(eid)
            path, keys = ent.path / "item.md", ITEM_KEYS
        else:
            ent = rm.phase(eid)
            path, keys = ent.path / "phase.md", PHASE_KEYS
        doc = ent.doc or Doc({})
        if "links" in fields and not is_item_id(eid):
            raise RoadmapError(("groups" if is_group_id(eid) else "phases") + " have no links")
        if "links" in fields:
            ids = rm.ids()
            bad = [l for l in _norm_links(fields["links"]) if l not in ids]
            if bad:
                raise RoadmapError(f"link target(s) not found: {', '.join(bad)}")
        if ("width" in fields or "height" in fields) and is_item_id(eid):
            raise RoadmapError("items have a fixed size (width/height are for phases and groups)")
        for k in ("width", "height"):
            v = fields.get(k)
            if v not in (None, ""):
                try:
                    fields[k] = v = int(round(float(v)))
                except (TypeError, ValueError):
                    raise RoadmapError(f"{k} must be a number") from None
                if v <= 0:
                    raise RoadmapError(f"{k} must be positive")
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
        if not is_item_id(a):
            raise RoadmapError("only items have links")
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


def move_item(root: Path, iid: str, target: str) -> str:
    """Move an item into a phase (top level) or a group.

    Inside the same phase the id does not change (only the folder moves). Into another
    phase the item takes the first free letter there and every link to it is rewritten.
    """
    with LOCK:
        rm = load(root)
        it = rm.item(iid)
        ph, cdir, _ = _container(rm, target)
        if it.id.startswith(ph.id + "/"):
            if it.path.parent != cdir:
                shutil.move(str(it.path), str(cdir / it.slug))
            return it.id
        slug = _next_slug(ph)
        new = f"{ph.id}/{slug}"
        shutil.move(str(it.path), str(cdir / slug))
        rm = load(root)
        _rewrite_links(rm, lambda l: new if l == iid else l)
        return new


def move_group(root: Path, gid: str, target: str) -> str:
    """Move a whole group (with its items and sub-groups) into a phase or another group.

    Inside the same phase no id changes. Into another phase the group keeps its slug when
    it is free there, its items take free letters in the new phase, and links are rewritten.
    """
    with LOCK:
        rm = load(root)
        g = rm.group(gid)
        ph, cdir, tg = _container(rm, target)
        if tg is not None and (tg.id == g.id or tg.id in {x.id for x in g.walk_groups()}):
            raise RoadmapError(f"cannot move {gid} into itself")
        src_pid = gid.partition("/")[0]
        if src_pid == ph.id:
            if g.path.parent != cdir:
                shutil.move(str(g.path), str(cdir / g.slug))
            return g.id
        # Another phase: keep each slug that is free there; colliding ones get new
        # slugs that also avoid every slug inside the moved tree (no rename clashes).
        mapping: dict[str, str] = {}
        renames: list[tuple[Path, str]] = []  # (old path, new folder name)
        moved_groups = [g, *g.walk_groups()]
        moved_items = list(g.walk_items())
        taken = {x.slug for x in ph.groups} | {i.slug for i in ph.items} | {e.name for e in ph.path.iterdir()}
        avoid = taken | {x.slug for x in moved_groups} | {i.slug for i in moved_items}
        for x in moved_groups:
            slug = x.slug
            if slug in taken:
                name = str(x.doc.get("name") or x.slug) if x.doc else x.slug
                slug = _group_slug(avoid, name)
                avoid.add(slug)
                renames.append((x.path, slug))
            mapping[x.id] = f"{ph.id}/{slug}"
        n = 0
        for it in moved_items:
            slug = it.slug
            if slug in taken:
                while slug_of(n) in avoid:
                    n += 1
                slug = slug_of(n)
                avoid.add(slug)
                renames.append((it.path, slug))
            mapping[it.id] = f"{ph.id}/{slug}"
        # Rename inside the source tree first (deepest paths first so parents stay valid).
        for old, new_name in sorted(renames, key=lambda r: -len(r[0].parts)):
            os.rename(old, old.parent / new_name)
        top_name = mapping[g.id].partition("/")[2]
        shutil.move(str(g.path.parent / top_name), str(cdir / top_name))
        _rewrite_links(load(root), lambda l: mapping.get(l, l))
        return mapping[g.id]


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


def remove(root: Path, eid: str, force: bool = False) -> list[str]:
    """Delete an item, a group or a whole phase; drops links to it. Returns removed ids.

    A group that still holds items or groups is only removed with ``force`` (and then
    everything inside goes too).
    """
    with LOCK:
        rm = load(root)
        if is_group_id(eid):
            g = rm.group(eid)
            inside = [x.id for x in g.walk_groups()] + [i.id for i in g.walk_items()]
            if inside and not force:
                raise RoadmapError(f"group {eid} is not empty ({len(inside)} entries); "
                                   "move them out first or use --force")
            gone = [g.id, *inside]
            shutil.rmtree(g.path)
            gs = set(gone)
            _rewrite_links(load(root), lambda l: None if l in gs else l)
            return gone
        if "/" in eid:
            it = rm.item(eid)
            shutil.rmtree(it.path)
            _rewrite_links(load(root), lambda l: None if l == eid else l)
            return [eid]
        ph = rm.phase(eid)
        gone = [ph.id] + [g.id for g in ph.groups] + [i.id for i in ph.items]
        shutil.rmtree(ph.path)
        nums = [p.num for p in rm.phases if p.num != ph.num]
        _renumber(root, nums, drop=ph.num)
        return gone


def arrange(root: Path, container: str, children: list[str]) -> dict[str, str]:
    """Drag and drop: make ``children`` (item and group ids) the contents of ``container``
    (a phase or group) in that order. Entries from elsewhere are moved in first (``mv``
    rules: same phase keeps the id, another phase gives a free one and rewrites links), then
    every child gets ``order: 1..N``. Children of the container left out keep their place
    after the listed ones. Returns {old id: new id} for the listed children.
    """
    with LOCK:
        mapping: dict[str, str] = {}
        rm = load(root)
        _container(rm, container)  # validates
        for cid in children:
            if cid == container:
                raise RoadmapError(f"cannot put {cid} inside itself")
            if not (is_item_id(cid) or is_group_id(cid)):
                raise RoadmapError(f"only items and groups can be arranged ({cid})")
            ent = rm.group(cid) if is_group_id(cid) else rm.item(cid)
            parent = ent.parent if is_group_id(cid) else (ent.group or cid.partition("/")[0])
            mapping[cid] = cid if parent == container else move(root, cid, container)
            if mapping[cid] != cid or parent != container:
                rm = load(root)
        rm = load(root)
        ph, _, grp = _container(rm, container)
        items, groups = (grp.items, grp.groups) if grp else (ph.top_items, ph.top_groups)
        here = {e.id: e for e in [*items, *groups]}
        listed = [mapping[c] for c in children]
        rest = [e.id for e in [*items, *groups] if e.id not in listed]
        for n, eid in enumerate(listed + rest, 1):
            e = here.get(eid)
            if e is None or e.doc is None:
                continue
            if e.doc.get("order") != n:
                _insert(e.doc, "order", n, GROUP_KEYS if is_group_id(eid) else ITEM_KEYS)
                _write(e.path / ("group.md" if is_group_id(eid) else "item.md"), e.doc)
        return mapping


def move(root: Path, eid: str, target: str) -> str:
    """CLI/API ``mv``: item -> phase or group, group -> phase or group, phase -> position."""
    if is_group_id(eid):
        return move_group(root, eid, str(target))
    if "/" in eid:
        return move_item(root, eid, str(target))
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

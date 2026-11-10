"""``roadmap`` command line. Thin wrapper over model.py (the server uses the same functions)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__, model
from .fmt import parse_value


def _root(args) -> Path:
    if args.dir:
        return Path(args.dir)
    r = model.find_root()
    if r is None:
        raise model.RoadmapError("no docs/roadmap found above the current folder (run `roadmap init` or pass --dir)")
    return r


def _out(args, data, text=None):
    if args.json:
        print(json.dumps(data, indent=2, ensure_ascii=False))
    elif text is not None:
        print(text)


def _body_arg(args) -> str | None:
    if getattr(args, "body_file", None):
        return Path(args.body_file).read_text(encoding="utf-8")
    return getattr(args, "body", None)


def cmd_list(args):
    rm = model.load(_root(args))
    data = model.to_json(rm)
    if args.json:
        for p in data["phases"]:
            for i in p["items"]:
                i.pop("body", None)
        return _out(args, data)
    lines = [f"{data['title']}  ({rm.root})"]
    for p in data["phases"]:
        nick = f" [{p['nickname']}]" if p.get("nickname") else ""
        lines.append(f"{p['id']:<10} {p['status']:<8} {p['name']}{nick}")
        items = {i["id"]: i for i in p["items"]}
        groups = {g["id"]: g for g in p["groups"]}

        def item_line(i, depth):
            links = f"  -> {', '.join(i['links'])}" if i["links"] else ""
            mark = "*" if i["bold"] else " "
            lines.append(f"  {'  ' * depth}{mark}{i['id']:<12} {i['status']:<8} {i['title']}{links}")

        def group_lines(g, depth):
            gnick = f" [{g['nickname']}]" if g.get("nickname") else ""
            st = g["status"] + ("~" if g.get("status_from_items") else "")
            lines.append(f"  {'  ' * depth}#{g['id']:<12} {st:<8} {g['name']}{gnick}")
            for iid in g["items"]:
                item_line(items[iid], depth + 1)
            for gid in g["groups"]:
                group_lines(groups[gid], depth + 1)

        for i in p["items"]:
            if not i.get("group"):
                item_line(i, 0)
        for g in p["groups"]:
            if g["parent"] == p["id"]:
                group_lines(g, 0)
    print("\n".join(lines))


def cmd_show(args):
    j = model.show(_root(args), args.id)
    if args.json:
        return _out(args, j)
    body = j.pop("body", None) or j.pop("summary", "")
    if not model.is_group_id(args.id):  # a phase lists its items and groups in full; show ids only
        j.pop("items", None)
        if "groups" in j:
            j["groups"] = [g["id"] for g in j["groups"]]
    for k, v in j.items():
        if isinstance(v, list):
            v = ", ".join(map(str, v)) or "(none)"
        print(f"{k}: {v}")
    if body:
        print("\n" + body)


def cmd_add(args):
    fields = dict(status=args.status, nickname=args.nickname, color=args.color,
                  bold=True if args.bold else None, links=args.link or None)
    iid = model.add_item(_root(args), args.phase, args.title, body=_body_arg(args) or "",
                         group=args.group, **fields)
    _out(args, {"id": iid}, iid)


def cmd_add_group(args):
    gid = model.add_group(_root(args), args.phase, args.name, args.nickname, parent=args.in_group,
                          body=_body_arg(args) or "", status=args.status, color=args.color)
    _out(args, {"id": gid}, gid)


def cmd_add_phase(args):
    pid = model.add_phase(_root(args), args.name, args.nickname, after=args.after,
                          body=_body_arg(args) or "", status=args.status, color=args.color)
    _out(args, {"id": pid}, pid)


def parse_assignments(pairs: list[str]) -> tuple[dict, str | None]:
    fields, body = {}, None
    for p in pairs:
        if "=" not in p:
            raise model.RoadmapError(f"expected key=value, got '{p}'")
        k, v = p.split("=", 1)
        k = k.strip()
        if k in ("body", "summary"):
            body = Path(v[1:]).read_text(encoding="utf-8") if v.startswith("@") else v
            continue
        if v.startswith("@"):
            v = Path(v[1:]).read_text(encoding="utf-8").strip()
        fields[k] = "" if v == "" else parse_value(v, []) if v.strip().startswith(("[", '"', "'")) or k in ("bold", "order") else v
    return fields, body


def cmd_set(args):
    fields, body = parse_assignments(args.assign)
    model.set_entry(_root(args), args.id, fields, body)
    _out(args, model.show(_root(args), args.id), f"updated {args.id}")


def cmd_link(args):
    links = model.link(_root(args), args.a, args.b)
    _out(args, {"id": args.a, "links": links}, f"{args.a} -> {', '.join(links)}")


def cmd_unlink(args):
    links = model.unlink(_root(args), args.a, args.b)
    _out(args, {"id": args.a, "links": links}, f"{args.a} -> {', '.join(links) or '(none)'}")


def cmd_mv(args):
    new = model.move(_root(args), args.id, args.target)
    _out(args, {"id": new, "from": args.id}, new)


def cmd_arrange(args):
    ids = model.arrange(_root(args), args.container, args.children)
    _out(args, {"container": args.container, "ids": ids}, " ".join(ids.values()))


def cmd_rm(args):
    gone = model.remove(_root(args), args.id, force=args.force)
    _out(args, {"removed": gone}, "removed " + ", ".join(gone))


def cmd_renumber(args):
    m = model.renumber(_root(args))
    _out(args, {"renamed": m}, "\n".join(f"{a} -> {b}" for a, b in m.items()) or "nothing to renumber")


def cmd_check(args):
    res = model.check(_root(args))
    if args.json:
        _out(args, res)
    else:
        for e in res["errors"]:
            print(f"error   {e['id']}: {e['msg']}")
        for w in res["warnings"]:
            print(f"warning {w['id']}: {w['msg']}")
        print(f"{len(res['errors'])} error(s), {len(res['warnings'])} warning(s)")
    return 0 if res["ok"] else 1


def cmd_init(args):
    root = model.init(Path(args.dir) if args.dir else Path.cwd(), args.title)
    msg = f"created {root}"
    rule = None
    if not args.no_agent_rule:
        path, state = model.add_agent_rule(model.project_dir(root))
        rule = {"file": str(path), "state": state}
        msg += f"\nroadmap rule {state} in {path}"
    _out(args, {"root": str(root), "agent_rule": rule}, msg)


def cmd_agent_rule(args):
    root = _root(args)
    path, state = model.add_agent_rule(model.project_dir(root), args.file)
    _out(args, {"file": str(path), "state": state}, f"roadmap rule {state} in {path}")


def cmd_serve(args):
    from .server import serve
    serve(_root(args), host=args.host, port=args.port, open_browser=args.open)


def cmd_install_skill(args):
    from importlib.resources import files
    src = files("webroadmap").joinpath("skill/SKILL.md").read_text(encoding="utf-8")
    dest = Path(args.dest) if args.dest else Path.home() / ".claude" / "skills" / "roadmap"
    dest.mkdir(parents=True, exist_ok=True)
    header = f"<!-- installed by webroadmap {__version__}; `roadmap install-skill` overwrites this file -->\n"
    # The version header goes after the frontmatter: the skill loader needs '---' first.
    if src.startswith("---\n"):
        end = src.index("\n---\n", 4) + 5
        text = src[:end] + header + src[end:]
    else:
        text = header + src
    with open(dest / "SKILL.md", "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    _out(args, {"path": str(dest / "SKILL.md")}, f"installed {dest / 'SKILL.md'}")


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--dir", default=argparse.SUPPRESS, help="roadmap folder (default: docs/roadmap found walking up)")
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="JSON output")
    ap = argparse.ArgumentParser(prog="roadmap", parents=[common],
                                 description="Interactive roadmap over a plain docs/roadmap/ folder.")
    ap.add_argument("--version", action="version", version=f"webroadmap {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def sp(name, fn, help):
        p = sub.add_parser(name, parents=[common], help=help)
        p.set_defaults(fn=fn)
        return p

    sp("list", cmd_list, "list phases, groups and items (as a tree)")
    p = sp("show", cmd_show, "show one item, group or phase")
    p.add_argument("id")
    p = sp("add", cmd_add, "add an item to a phase (prints the new id)")
    p.add_argument("phase")
    p.add_argument("--title", required=True)
    p.add_argument("--nickname")
    p.add_argument("--status", choices=model.STATUSES)
    p.add_argument("--color")
    p.add_argument("--bold", action="store_true")
    p.add_argument("--link", action="append", help="outgoing link (repeatable)")
    p.add_argument("--group", help="put the item straight into this group (phaseN/g-...)")
    p.add_argument("--body")
    p.add_argument("--body-file")
    p = sp("add-group", cmd_add_group, "add a group to a phase, or inside another group with --in (prints the new id)")
    p.add_argument("phase")
    p.add_argument("--name", required=True)
    p.add_argument("--nickname")
    p.add_argument("--in", dest="in_group", metavar="GROUP", help="parent group id (nests the new group)")
    p.add_argument("--status", choices=model.STATUSES, help="default: derived from the group's items")
    p.add_argument("--color")
    p.add_argument("--body", help="group summary")
    p.add_argument("--body-file")
    p = sp("add-phase", cmd_add_phase, "add a phase (at the end, or after --after phaseN; phase0 = first)")
    p.add_argument("--name", required=True)
    p.add_argument("--nickname")
    p.add_argument("--after")
    p.add_argument("--status", choices=model.STATUSES)
    p.add_argument("--color")
    p.add_argument("--body", help="phase summary")
    p.add_argument("--body-file")
    p = sp("set", cmd_set, "set fields: key=value ... (body=@file, key= removes)")
    p.add_argument("id")
    p.add_argument("assign", nargs="+")
    p = sp("link", cmd_link, "add link A -> B")
    p.add_argument("a")
    p.add_argument("b")
    p = sp("unlink", cmd_unlink, "remove link A -> B")
    p.add_argument("a")
    p.add_argument("b")
    p = sp("mv", cmd_mv, "move an item or group into a phase or group (ids kept within a phase), "
                         "or a phase to a position (renumbers, rewrites links)")
    p.add_argument("id")
    p.add_argument("target")
    p = sp("arrange", cmd_arrange, "put items/groups into a phase or group in this order (moves them in, sets order: 1..N)")
    p.add_argument("container")
    p.add_argument("children", nargs="+")
    p = sp("rm", cmd_rm, "delete an item, group or phase (drops links to it)")
    p.add_argument("id")
    p.add_argument("--force", action="store_true", help="also delete a group that is not empty")
    sp("renumber", cmd_renumber, "close gaps in phase numbers")
    sp("check", cmd_check, "validate (exit 1 on errors)")
    p = sp("init", cmd_init, "create docs/roadmap with a sample phase (and add the roadmap rule to CLAUDE.md)")
    p.add_argument("--title")
    p.add_argument("--no-agent-rule", action="store_true", help="don't touch CLAUDE.md")
    p = sp("agent-rule", cmd_agent_rule, "add or refresh the 'every change updates the roadmap' rule in the project's CLAUDE.md")
    p.add_argument("--file", default="CLAUDE.md", help="instructions file next to docs/ (default CLAUDE.md, e.g. AGENTS.md)")
    p = sp("serve", cmd_serve, "serve the interactive page")
    p.add_argument("--port", type=int)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--open", action="store_true", help="open the page in the default browser")
    p = sp("install-skill", cmd_install_skill, "install the Claude skill to ~/.claude/skills/roadmap/")
    p.add_argument("--dest", help="target folder (default ~/.claude/skills/roadmap)")
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    args.dir = getattr(args, "dir", None)
    args.json = getattr(args, "json", False)
    try:
        rc = args.fn(args)
    except (model.RoadmapError, OSError, ValueError) as e:
        if args.json:
            print(json.dumps({"error": str(e)}))
        else:
            print(f"roadmap: {e}", file=sys.stderr)
        return 2
    return rc or 0


if __name__ == "__main__":
    sys.exit(main())

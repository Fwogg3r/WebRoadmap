import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from webroadmap import cli, model
from webroadmap.fmt import Doc, FormatError, emit_value, parse_scalar
from webroadmap.server import make_server

ITEM = """---
title: XPBD rigid bodies
nickname: XPBD                 # optional short label for far zoom
status: done                   # done | active | planned | idea | dropped
color: "#22c55e"               # optional override (any CSS colour)
bold: false
links: [phase3/a, phase3/c]
done: 2026-10-24
refs: [docs/DECISIONS.md#xpbd, "commit 1a2b3c4"]
owner: someone                 # unknown key
tags:
  - physics
  - "solver, xpbd"
order: 2
---
**What:** one or two sentences.

- a list
"""


# ------------------------------------------------------------------ format

def test_parse_values():
    d = Doc.parse(ITEM)
    f = d.fields
    assert f["title"] == "XPBD rigid bodies"
    assert f["nickname"] == "XPBD"
    assert f["color"] == "#22c55e"
    assert f["bold"] is False
    assert f["links"] == ["phase3/a", "phase3/c"]
    assert f["done"] == "2026-10-24"
    assert f["refs"] == ["docs/DECISIONS.md#xpbd", "commit 1a2b3c4"]
    assert f["tags"] == ["physics", "solver, xpbd"]
    assert f["order"] == 2
    assert d.body.startswith("**What:**")


def test_round_trip_untouched_is_identical():
    assert Doc.parse(ITEM).dump() == ITEM


def test_round_trip_keeps_unknown_keys_and_comments():
    d = Doc.parse(ITEM)
    d.set("status", "active")
    out = d.dump()
    assert "status: active\n" in out
    assert "owner: someone                 # unknown key" in out
    assert '  - "solver, xpbd"' in out
    assert 'nickname: XPBD                 # optional short label' in out
    again = Doc.parse(out)
    assert again.fields["tags"] == ["physics", "solver, xpbd"]


def test_emit_quotes_when_needed():
    assert emit_value("#fff") == '"#fff"'
    assert emit_value("plain words") == "plain words"
    assert emit_value("a: b") == '"a: b"'
    assert emit_value("true") == '"true"'
    assert emit_value("12") == '"12"'
    assert emit_value(["a,b", "c"]) == '["a,b", c]'
    for s in ["#fff", "a: b", "true", "x # y", "[x]", "  pad", "quote\"d", "ünï"]:
        assert parse_scalar(emit_value(s)) == s


def test_crlf_and_errors():
    assert Doc.parse(ITEM.replace("\n", "\r\n")).fields["title"] == "XPBD rigid bodies"
    with pytest.raises(FormatError):
        Doc.parse("---\ntitle: x\n")
    with pytest.raises(FormatError):
        Doc.parse("---\ntitle: x\ntitle: y\n---\n")


# ------------------------------------------------------------------ model

@pytest.fixture
def root(tmp_path):
    r = model.init(tmp_path, "Test")
    model.add_item(r, "phase1", "B", links=["phase1/a"])
    model.add_phase(r, "Two", "Two")
    model.add_item(r, "phase2", "C")
    model.add_phase(r, "Three", "Three")
    model.add_item(r, "phase3", "D", links=["phase2/a", "phase2"])
    model.link(r, "phase1/a", "phase2/a")
    model.link(r, "phase1/a", "phase3")
    return r


def links(r, iid):
    return model.load(r).item(iid).links


def test_init_and_list(root):
    rm = model.load(root)
    assert [p.id for p in rm.phases] == ["phase1", "phase2", "phase3"]
    assert [i.id for i in rm.phases[0].items] == ["phase1/a", "phase1/b"]
    assert model.check(root)["ok"]
    assert model.find_root(root / "phase1" / "a") == root
    assert model.find_root(root.parent.parent) == root


def test_files_are_lf(root):
    for p in root.rglob("*.md"):
        assert b"\r\n" not in p.read_bytes()


def test_insert_phase_renumbers_and_rewrites(root):
    pid = model.add_phase(root, "Between", "Mid", after="phase1")
    assert pid == "phase2"
    rm = model.load(root)
    assert [p.doc.get("name") for p in rm.phases] == ["First steps", "Between", "Two", "Three"]
    assert links(root, "phase1/a") == ["phase3/a", "phase4"]
    assert links(root, "phase4/a") == ["phase3/a", "phase3"]
    assert model.add_phase(root, "Zero", "Z", after="phase0") == "phase1"
    assert links(root, "phase2/a") == ["phase4/a", "phase5"]
    assert model.check(root)["ok"]


def test_move_item_rewrites_links(root):
    new = model.move(root, "phase2/a", "phase1")
    assert new == "phase1/c"
    assert links(root, "phase1/a") == ["phase1/c", "phase3"]
    assert links(root, "phase3/a") == ["phase1/c", "phase2"]
    assert model.check(root)["ok"]


def test_move_phase_position(root):
    assert model.move(root, "phase3", "1") == "phase1"
    rm = model.load(root)
    assert [p.doc.get("name") for p in rm.phases] == ["Three", "First steps", "Two"]
    assert links(root, "phase1/a") == ["phase3/a", "phase3"]
    assert links(root, "phase2/a") == ["phase3/a", "phase1"]


def test_rm_item_and_phase_drop_links(root):
    model.remove(root, "phase2/a")
    assert links(root, "phase1/a") == ["phase3"]
    assert links(root, "phase3/a") == ["phase2"]
    gone = model.remove(root, "phase2")
    assert gone == ["phase2"]
    assert links(root, "phase2/a") == []          # old phase3/a, its phase2 link dropped
    assert links(root, "phase1/a") == ["phase2"]  # phase3 renumbered to phase2
    assert model.check(root)["ok"]


def test_set_fields_and_body(root, tmp_path):
    model.set_entry(root, "phase2/a", {"status": "done", "color": "#123456", "bold": True}, "**Body**")
    it = model.load(root).item("phase2/a")
    assert it.doc.fields["status"] == "done" and it.doc.fields["bold"] is True
    assert it.doc.body.strip() == "**Body**"
    text = (it.path / "item.md").read_text()
    assert list(it.doc.fields)[:5] == ["title", "status", "color", "bold", "links"]
    assert 'color: "#123456"' in text
    model.set_entry(root, "phase2/a", {"color": ""})
    assert "color" not in model.load(root).item("phase2/a").doc.fields
    with pytest.raises(model.RoadmapError):
        model.set_entry(root, "phase2/a", {"status": "bogus"})
    with pytest.raises(model.RoadmapError):
        model.set_entry(root, "phase2/a", {"links": ["phase9/z"]})
    model.set_entry(root, "phase2", {"name": "Renamed"}, "Summary text")
    assert model.show(root, "phase2")["summary"] == "Summary text"


def test_unknown_keys_survive_mutations(root):
    p = root / "phase2" / "a" / "item.md"
    p.write_text(p.read_text().replace("status: planned", "status: planned\nowner: me   # keep\nextra:\n  - x"), newline="\n")
    model.move(root, "phase1/a", "phase3")  # rewrites links in phase2/a? no: but loads/writes others
    model.set_entry(root, "phase2/a", {"status": "active"})
    model.add_phase(root, "Zero", "Z", after="phase0")
    text = (root / "phase3" / "a" / "item.md").read_text()
    assert "owner: me   # keep" in text and "extra:\n  - x" in text and "status: active" in text


def test_check_errors_and_warnings(root):
    (root / "phase2" / "b").mkdir()
    p = root / "phase3" / "a" / "item.md"
    p.write_text(p.read_text().replace("status: planned", "status: wip").replace("links: [phase2/a, phase2]", "links: [phase2/a, phase9/x]"), newline="\n")
    model.add_item(root, "phase2", "C")       # duplicate title
    model.add_item(root, "phase3", "Lonely")  # no links
    res = model.check(root)
    msgs = {(e["id"], e["msg"]) for e in res["errors"]}
    assert ("phase2/b", "missing item.md") in msgs
    assert ("phase3/a", "unknown status 'wip'") in msgs
    assert ("phase3/a", "link to non-existent 'phase9/x'") in msgs
    w = {(e["id"], e["msg"].split(" (")[0]) for e in res["warnings"]}
    assert ("phase3/b", "item has no incoming or outgoing links") in w
    assert ("phase2/a", "duplicate title") in w
    assert cli.main(["--dir", str(root), "check"]) == 1


def test_slugs():
    assert [model.slug_of(i) for i in (0, 25, 26, 27, 701, 702)] == ["a", "z", "aa", "ab", "zz", "aaa"]
    assert sorted(["b", "aa", "a", "z"], key=model.slug_key) == ["a", "b", "z", "aa"]


def test_cli_json(root, capsys, tmp_path):
    body = tmp_path / "b.md"
    body.write_text("**Why:** test")
    assert cli.main(["--dir", str(root), "add", "phase2", "--title", "New", "--link", "phase3/a", "--body-file", str(body), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["id"] == "phase2/b"
    assert cli.main(["--dir", str(root), "set", "phase2/b", "status=active", "bold=true", f"body=@{body}", "links=[phase3/a, phase3]"]) == 0
    capsys.readouterr()
    assert cli.main(["--dir", str(root), "show", "phase2/b", "--json"]) == 0
    j = json.loads(capsys.readouterr().out)
    assert j["status"] == "active" and j["bold"] is True and j["links"] == ["phase3/a", "phase3"]
    assert cli.main(["list", "--json", "--dir", str(root)]) == 0
    assert len(json.loads(capsys.readouterr().out)["phases"]) == 3
    assert cli.main(["--dir", str(root), "rm", "phase9/a"]) == 2


def test_install_skill(tmp_path, capsys):
    assert cli.main(["install-skill", "--dest", str(tmp_path / "s")]) == 0
    text = (tmp_path / "s" / "SKILL.md").read_text()
    assert text.startswith("---\nname: roadmap\n")
    assert "installed by webroadmap" in text


# ------------------------------------------------------------------ HTTP API

@pytest.fixture
def server(root):
    srv = make_server(root, "127.0.0.1", 0)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def call(base, path, data=None, headers=None):
    req = urllib.request.Request(base + path, method="POST" if data is not None else "GET",
                                 data=json.dumps(data).encode() if data is not None else None,
                                 headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read()) if "json" in r.headers["Content-Type"] else r.read()
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_api(server, root):
    code, d = call(server, "/api/roadmap")
    assert code == 200 and len(d["phases"]) == 3 and d["check"]["ok"]
    v0 = d["version"]
    code, r = call(server, "/api/add", {"phase": "phase2", "title": "Via API", "links": ["phase3/a"]})
    assert code == 200 and r["id"] == "phase2/b"
    assert call(server, "/api/version")[1]["version"] != v0
    assert call(server, "/api/set", {"id": "phase2/b", "fields": {"status": "done"}, "body": "x"})[0] == 200
    assert call(server, "/api/show?id=phase2/b")[1]["status"] == "done"
    assert call(server, "/api/link", {"a": "phase2/b", "b": "phase1"})[1]["links"] == ["phase3/a", "phase1"]
    assert call(server, "/api/unlink", {"a": "phase2/b", "b": "phase1"})[1]["links"] == ["phase3/a"]
    code, r = call(server, "/api/add-phase", {"name": "Ins", "nickname": "I", "after": "phase1"})
    assert r["id"] == "phase2" and links(root, "phase3/b") == ["phase4/a"]
    assert call(server, "/api/mv", {"id": "phase3/b", "target": "phase1"})[1]["id"] == "phase1/c"
    assert call(server, "/api/rm", {"id": "phase2"})[1]["removed"] == ["phase2"]
    code, r = call(server, "/api/set", {"id": "phase1/a", "fields": {"status": "nope"}})
    assert code == 400 and "unknown status" in r["error"]
    assert call(server, "/api/bogus", {})[0] == 400
    code, r = call(server, "/api/rm", {"id": "phase1/a"}, {"Origin": "http://evil.example"})
    assert code == 403
    code, page = call(server, "/")
    assert code == 200 and b"app.js" in page
    assert call(server, "/../pyproject.toml")[0] == 404
    assert model.check(root)["ok"]


def test_agent_rule_is_idempotent(tmp_path):
    (tmp_path / "CLAUDE.md").write_bytes(b"# Proj\r\n\r\nSome rules.\r\n")
    root = model.init(tmp_path, "P")
    path, state = model.add_agent_rule(model.project_dir(root))
    assert state == "added" and path == tmp_path / "CLAUDE.md"
    raw = path.read_bytes()
    assert raw.startswith(b"# Proj\r\n") and b"\n" not in raw.replace(b"\r\n", b"")  # CRLF kept
    assert raw.count(model.RULE_START.encode()) == 1
    assert model.add_agent_rule(tmp_path)[1] == "unchanged"


def test_init_creates_rule(tmp_path):
    assert cli.main(["init", "--dir", str(tmp_path)]) == 0
    assert model.RULE_START in (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")


# ------------------------------------------------------------------ groups

GROUP = """---
name: Solver work            # full name
nickname: Solver
status: active
color: "#123456"
owner: someone               # unknown key
---
Summary of the group.
"""


def test_group_md_round_trip(root):
    gdir = root / "phase2" / "g-solver"
    gdir.mkdir()
    (gdir / "group.md").write_text(GROUP, encoding="utf-8")
    g = model.load(root).group("phase2/g-solver")
    assert g.doc.fields["name"] == "Solver work" and g.parent == "phase2"
    assert g.doc.dump() == GROUP  # untouched file is identical
    model.set_entry(root, "phase2/g-solver", {"nickname": "Solv"})
    text = (gdir / "group.md").read_text(encoding="utf-8")
    assert "nickname: Solv\n" in text and "owner: someone" in text and "# full name" in text
    model.set_entry(root, "phase2/g-solver", {"status": ""})  # back to "from items"
    j = model.show(root, "phase2/g-solver")
    assert "status" not in model.load(root).group("phase2/g-solver").doc.fields
    assert j["status_from_items"] and j["status"] == "planned" and j["summary"] == "Summary of the group."
    with pytest.raises(model.RoadmapError):
        model.set_entry(root, "phase2/g-solver", {"links": ["phase1"]})


def test_groups_nest_and_ids_stay_stable(root):
    g1 = model.add_group(root, "phase2", "Core solver", "Core")
    assert g1 == "phase2/g-core-solver"
    g2 = model.add_group(root, None, "Inner part", parent=g1)
    assert g2 == "phase2/g-inner-part"
    assert (root / "phase2" / "g-core-solver" / "g-inner-part" / "group.md").is_file()
    assert model.add_group(root, "phase2", "Core solver") == "phase2/g-core-solver-2"
    # new item straight into a nested group: letter unique across the whole phase
    iid = model.add_item(root, "phase2", "Deep", group=g2, links=["phase3/a"])
    assert iid == "phase2/b" and (root / "phase2" / "g-core-solver" / "g-inner-part" / "b" / "item.md").is_file()
    assert model.add_item(root, "phase2", "Top") == "phase2/c"
    rm = model.load(root)
    assert [i.id for i in rm.phase("phase2").items] == ["phase2/a", "phase2/c", "phase2/b"]
    assert rm.item("phase2/b").group == g2
    assert [g.id for g in rm.group(g1).groups] == [g2]
    # moving between groups and back to the top level keeps the id
    assert model.move(root, "phase2/a", g1) == "phase2/a"
    assert (root / "phase2" / "g-core-solver" / "a" / "item.md").is_file()
    assert model.move(root, "phase2/b", g1) == "phase2/b"
    assert model.move(root, "phase2/b", "phase2") == "phase2/b"
    assert (root / "phase2" / "b" / "item.md").is_file()
    assert links(root, "phase3/a") == ["phase2/a", "phase2"]  # untouched
    # a group to another group in the same phase: no id changes
    assert model.move(root, g2, "phase2/g-core-solver-2") == g2
    assert model.load(root).group(g2).parent == "phase2/g-core-solver-2"
    with pytest.raises(model.RoadmapError):
        model.move(root, "phase2/g-core-solver-2", g2)  # into its own child
    assert model.check(root)["ok"]


def test_move_group_to_other_phase_rewrites(root):
    g = model.add_group(root, "phase2", "Pack")
    model.move(root, "phase2/a", g)  # phase2/a has incoming links
    model.add_item(root, "phase2", "Other", group=g)  # phase2/b
    model.link(root, "phase3/a", g)
    new = model.move(root, g, "phase1")
    assert new == "phase1/g-pack"
    rm = model.load(root)
    # phase1 has a, b: the moved items take free letters c, d
    assert sorted(i.id for i in rm.group(new).items) == ["phase1/c", "phase1/d"]
    assert "phase1/c" in links(root, "phase1/a") and "phase1/g-pack" in links(root, "phase3/a")
    assert model.check(root)["ok"]
    # an item into a group of another phase: new letter, links rewritten
    model.add_group(root, "phase3", "Pack")
    assert model.move(root, "phase1/c", "phase3/g-pack") == "phase3/b"
    assert "phase3/b" in links(root, "phase1/a")
    # moving the group back where a same-named group exists renames it
    assert model.move(root, "phase1/g-pack", "phase3") == "phase3/g-pack-2"
    assert model.check(root)["ok"]


def test_group_check_and_remove(root):
    import shutil
    g = model.add_group(root, "phase2", "Box")
    res = model.check(root)
    assert res["ok"] and {"id": g, "msg": "empty group"} in res["warnings"]
    model.link(root, "phase3/a", g)
    model.add_item(root, "phase2", "In box", group=g)  # phase2/b
    # duplicate letter in two folders of one phase, bad status, missing group.md, link to a missing group
    shutil.copytree(root / "phase2" / "a", root / "phase2" / "g-box" / "a")
    (root / "phase2" / "g-box" / "group.md").write_text("---\nname: Box\nstatus: soon\n---\n", encoding="utf-8")
    (root / "phase1" / "g-nofile").mkdir()
    p = root / "phase1" / "b" / "item.md"
    p.write_text(p.read_text(encoding="utf-8").replace("links: [", "links: [phase2/g-gone, "), encoding="utf-8")
    errs = model.check(root)["errors"]
    msgs = {(e["id"], e["msg"].split(":")[0].split(" '")[0]) for e in errs}
    assert ("phase2/a", "duplicate id") in msgs
    assert ("phase2/g-box", "unknown status") in msgs
    assert ("phase1/g-nofile", "missing group.md") in msgs
    assert ("phase1/b", "link to non-existent") in msgs
    shutil.rmtree(root / "phase2" / "g-box" / "a")
    shutil.rmtree(root / "phase1" / "g-nofile")
    model.unlink(root, "phase1/b", "phase2/g-gone")
    model.set_entry(root, g, {"status": "done"})
    # rm refuses a non-empty group unless forced, then drops links to it and its items
    with pytest.raises(model.RoadmapError, match="not empty"):
        model.remove(root, g)
    model.link(root, "phase1/a", "phase2/b")
    assert model.remove(root, g, force=True) == [g, "phase2/b"]
    assert g not in links(root, "phase3/a") and "phase2/b" not in links(root, "phase1/a")
    e = model.add_group(root, "phase2", "Empty")
    assert model.remove(root, e) == [e]
    assert model.check(root)["ok"]


def test_group_status_derived():
    def it(st):
        return model.Item("p/x", "x", Path("."), Doc({"title": "t", "status": st}))
    d = model.derived_status
    assert d([]) == "planned"
    assert d([it("done"), it("dropped")]) == "done"
    assert d([it("done"), it("planned")]) == "active"
    assert d([it("idea"), it("idea")]) == "idea"
    assert d([it("planned"), it("idea")]) == "planned"
    assert d([it("dropped")]) == "dropped"


def test_groups_cli_and_json(root, capsys):
    r = ["--dir", str(root)]
    assert cli.main([*r, "add-group", "phase2", "--name", "Outer box", "--nickname", "Outer"]) == 0
    assert capsys.readouterr().out.strip() == "phase2/g-outer-box"
    assert cli.main([*r, "add-group", "phase2", "--name", "Inner", "--in", "phase2/g-outer-box",
                     "--status", "idea", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["id"] == "phase2/g-inner"
    assert cli.main([*r, "add", "phase2", "--title", "Grouped", "--group", "phase2/g-inner"]) == 0
    assert capsys.readouterr().out.strip() == "phase2/b"
    assert cli.main([*r, "mv", "phase2/a", "phase2/g-outer-box"]) == 0
    assert capsys.readouterr().out.strip() == "phase2/a"
    assert cli.main([*r, "set", "phase2/g-inner", "name=Inner part", "summary=About it"]) == 0
    capsys.readouterr()
    assert cli.main([*r, "list"]) == 0
    out = capsys.readouterr().out.splitlines()
    i = next(n for n, l in enumerate(out) if "phase2/g-outer-box" in l)
    assert out[i].startswith("  #phase2/g-outer-box")
    assert out[i + 1].startswith("     phase2/a")
    assert out[i + 2].startswith("    #phase2/g-inner") and "Inner part" in out[i + 2]
    assert out[i + 3].startswith("       phase2/b")
    assert cli.main([*r, "list", "--json"]) == 0
    ph = json.loads(capsys.readouterr().out)["phases"][1]
    gs = {g["id"]: g for g in ph["groups"]}
    assert gs["phase2/g-outer-box"]["groups"] == ["phase2/g-inner"]
    assert gs["phase2/g-outer-box"]["items"] == ["phase2/a"]
    assert gs["phase2/g-inner"]["parent"] == "phase2/g-outer-box" and gs["phase2/g-inner"]["status"] == "idea"
    assert gs["phase2/g-outer-box"]["status_from_items"] is True
    assert {i["id"]: i["group"] for i in ph["items"]} == {"phase2/a": "phase2/g-outer-box", "phase2/b": "phase2/g-inner"}
    assert cli.main([*r, "show", "phase2/g-inner"]) == 0
    out = capsys.readouterr().out
    assert "items: phase2/b" in out and "About it" in out
    assert cli.main([*r, "rm", "phase2/g-outer-box"]) == 2
    assert "not empty" in capsys.readouterr().err
    assert cli.main([*r, "rm", "phase2/g-outer-box", "--force"]) == 0
    assert "phase2/b" in capsys.readouterr().out
    assert cli.main([*r, "check"]) == 0


def test_groups_api(server, root):
    code, r = call(server, "/api/add-group", {"phase": "phase3", "name": "Via API", "nickname": "API"})
    assert code == 200 and r["id"] == "phase3/g-via-api"
    code, r = call(server, "/api/add-group", {"name": "Sub", "in": "phase3/g-via-api"})
    assert code == 200 and r["id"] == "phase3/g-sub"
    code, r = call(server, "/api/add", {"phase": "phase3", "title": "In sub", "group": "phase3/g-sub"})
    assert r["id"] == "phase3/b"
    assert call(server, "/api/mv", {"id": "phase3/a", "target": "phase3/g-via-api"})[1]["id"] == "phase3/a"
    assert call(server, "/api/set", {"id": "phase3/g-sub", "fields": {"name": "Renamed"}})[0] == 200
    assert call(server, "/api/link", {"a": "phase1/a", "b": "phase3/g-sub"})[0] == 200
    code, d = call(server, "/api/roadmap")
    ph = d["phases"][2]
    assert [g["id"] for g in ph["groups"]] == ["phase3/g-via-api", "phase3/g-sub"]
    assert ph["groups"][1]["name"] == "Renamed" and d["check"]["ok"]
    assert call(server, "/api/show?id=phase3/g-sub")[1]["incoming"] == ["phase1/a"]
    code, r = call(server, "/api/rm", {"id": "phase3/g-via-api"})
    assert code == 400 and "not empty" in r["error"]
    code, r = call(server, "/api/rm", {"id": "phase3/g-via-api", "force": True})
    assert code == 200 and r["removed"][0] == "phase3/g-via-api"
    assert "phase3/g-sub" not in links(root, "phase1/a")


# ------------------------------------------------------------------ drag and drop, sizes

def test_arrange_moves_and_orders(root):
    g = model.add_group(root, "phase2", "Box")
    b = model.add_item(root, "phase2", "Second")  # phase2/b
    # reorder inside the phase: group first, then b, then a
    ids = model.arrange(root, "phase2", [g, b, "phase2/a"])
    assert ids == {g: g, b: b, "phase2/a": "phase2/a"}
    rm = model.load(root)
    assert rm.group(g).doc.fields["order"] == 1 and rm.item(b).doc.fields["order"] == 2
    assert [i.id for i in rm.phase("phase2").top_items] == [b, "phase2/a"]
    # drop an item from another phase into the group: new id, links rewritten
    ids = model.arrange(root, g, ["phase3/a"])
    new = ids["phase3/a"]
    assert new.startswith("phase2/") and model.load(root).item(new).group == g
    assert model.check(root)["ok"]
    with pytest.raises(model.RoadmapError):
        model.arrange(root, g, [g])
    with pytest.raises(model.RoadmapError):
        model.arrange(root, "phase2/a", [b])  # an item is not a container


def test_sizes(root, server):
    model.set_entry(root, "phase2", {"width": 812.6, "height": "500"})
    f = model.load(root).phase("phase2").doc.fields
    assert f["width"] == 813 and f["height"] == 500
    model.set_entry(root, "phase2", {"width": ""})
    assert "width" not in model.load(root).phase("phase2").doc.fields
    with pytest.raises(model.RoadmapError):
        model.set_entry(root, "phase2/a", {"width": 300})
    with pytest.raises(model.RoadmapError):
        model.set_entry(root, "phase2", {"height": "tall"})
    p = root / "phase2" / "phase.md"
    p.write_text(p.read_text(encoding="utf-8").replace("height: 500", "height: -3"), encoding="utf-8")
    assert any("height must be" in e["msg"] for e in model.check(root)["errors"])
    code, r = call(server, "/api/arrange", {"container": "phase2", "children": ["phase2/a"]})
    assert code == 200 and r["ids"] == {"phase2/a": "phase2/a"}

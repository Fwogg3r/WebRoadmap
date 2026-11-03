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

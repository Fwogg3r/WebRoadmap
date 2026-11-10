# /// script
# requires-python = ">=3.10"
# dependencies = ["playwright>=1.45"]
# ///
"""Headless end-to-end check of the page (not part of pytest: needs Chrome or Edge).

    uv run tests/browser_check.py [--shots docs/screenshots]

Serves a temp copy of examples/demo, drives the installed Chrome/Edge headless
(never a visible window), checks zoom levels, panning, editing, linking, live
reload and the dirty-form notice, and writes the README screenshots.
"""

import argparse
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from playwright.sync_api import sync_playwright  # noqa: E402

from webroadmap import model  # noqa: E402
from webroadmap.server import make_server  # noqa: E402

ok = True


def check(cond, msg):
    global ok
    print(("PASS " if cond else "FAIL ") + msg)
    ok &= bool(cond)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", default=str(REPO / "docs" / "screenshots"))
    ap.add_argument("--scheme", default="dark", choices=["dark", "light"])
    args = ap.parse_args()
    shots = Path(args.shots)
    shots.mkdir(parents=True, exist_ok=True)

    tmp = Path(tempfile.mkdtemp())
    shutil.copytree(REPO / "examples" / "demo" / "docs", tmp / "docs")
    root = tmp / "docs" / "roadmap"
    srv = make_server(root, "127.0.0.1", 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/"

    with sync_playwright() as p:
        browser = None
        for ch in ("chrome", "msedge"):
            try:
                browser = p.chromium.launch(channel=ch, headless=True)
                break
            except Exception:
                continue
        if browser is None:
            sys.exit("no Chrome or Edge found")
        page = browser.new_page(viewport={"width": 1400, "height": 820}, color_scheme=args.scheme)
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        answer = {"prompt": ""}  # what the next prompt() gets
        page.on("dialog", lambda d: d.accept(answer["prompt"]) if d.type == "prompt" else d.accept())

        # --- screenshots at the three zoom levels
        sfx = "" if args.scheme == "dark" else "-light"
        for name, h in [("far", "zoom=far"), ("mid", "zoom=mid&focus=phase2"),
                        ("near", "zoom=near&focus=phase3/b&select=phase3/b")]:
            page.goto(url + "?n=1#" + h)
            page.wait_for_selector(".item", state="attached")
            page.wait_for_timeout(500)
            check(page.eval_on_selector("#layer", f"e => e.classList.contains('{name}')"), f"zoom level {name}")
            page.screenshot(path=str(shots / f"{name}{sfx}.png"))
        # --- groups: side by side in their phase, nested inside the parent's rectangle
        page.goto(url + "?n=2#zoom=0.5&focus=phase5")
        page.wait_for_selector(".grp", state="attached")
        page.wait_for_timeout(500)
        page.screenshot(path=str(shots / f"groups{sfx}.png"))
        box = lambda gid: page.eval_on_selector(f'[data-id="{gid}"]:is(.grp,.item)', "e => { const r = e.getBoundingClientRect(); return [r.left, r.top, r.right, r.bottom]; }")
        c5, veh, world, base = (box(g) for g in ("phase5/c", "phase5/g-vehicles-and-craft", "phase5/g-world-and-resources", "phase5/g-base-building"))
        check(c5[2] < veh[0] and world[1] > c5[3] and base[1] > veh[3] and base[0] > world[2],
              "phase 5's item and 3 groups form a 2x2 grid (row by row)")
        outer, inner = box("phase5/g-world-and-resources"), box("phase5/g-ores-and-ice")
        check(outer[0] < inner[0] and outer[1] < inner[1] and inner[2] < outer[2] and inner[3] < outer[3], "nested group inside its parent")
        xs = {round(page.eval_on_selector(f'.item[data-id="phase5/{c}"]', "e => e.getBoundingClientRect().left")) for c in "hijklmnop"}
        check(len(xs) == 3, f"9 items in a group wrap into 3 columns ({len(xs)})")
        check(all(base[0] < x < base[2] for x in xs), "group items inside the group")
        x3 = {round(page.eval_on_selector(f'.item[data-id="phase3/{c}"]', "e => e.getBoundingClientRect().left")) for c in "abcd"}
        check(len(x3) == 2, f"4 items in a phase form a 2x2 grid ({len(x3)} columns)")
        # links: routed paths in per-source colours, a port dot on each border crossed
        n_paths = page.evaluate("document.querySelectorAll('#edges path[data-from]').length")
        n_links = sum(len(i.links) for i in model.load(root).items())
        check(n_paths == n_links, f"one path per link ({n_paths}/{n_links})")
        cols = page.evaluate("new Set([...document.querySelectorAll('#edges path[data-from]')].map(p => p.style.getPropertyValue('--ec'))).size")
        check(cols >= 8, f"links coloured per source item ({cols} colours)")
        # phase1/a -> phase2/a crosses phase1's right border and phase2's left border
        ports = page.evaluate("[...document.querySelectorAll('#edges circle.port[data-from=\"phase1/c\"]')].map(c => +c.getAttribute('cx'))")
        r1, r2 = page.evaluate("[S.rects.phase1.col, S.rects.phase2.col]")
        check(len(ports) == 2 and abs(ports[0] - (r1["x"] + r1["w"])) < 1 and abs(ports[1] - r2["x"]) < 1,
              f"a link between phases has a dot on each border ({ports})")
        # no path crosses an item box (sampled along each path)
        hits = page.evaluate("""() => { const boxes = [...document.querySelectorAll('.item')].map(e => [e.offsetLeft, e.offsetTop, e.offsetWidth, e.offsetHeight]);
            let bad = 0; for (const p of document.querySelectorAll('#edges path[data-from]')) { const L = p.getTotalLength();
              for (let t = 14; t < L - 14; t += 6) { const q = p.getPointAtLength(t);
                if (boxes.some(([x, y, w, h]) => q.x > x + 2 && q.x < x + w - 2 && q.y > y + 2 && q.y < y + h - 2)) { bad++; break; } } }
            return bad; }""")
        check(hits == 0, f"no link runs through an item box ({hits})")
        page.goto(url + "#edit=phase3/b&zoom=near&focus=phase3/b")
        page.wait_for_selector("#f-body")
        page.wait_for_timeout(300)
        page.screenshot(path=str(shots / f"edit{sfx}.png"))
        if args.scheme != "dark":
            browser.close()
            srv.shutdown()
            print("ALL OK" if ok else "FAILURES")
            sys.exit(0 if ok else 1)

        # --- wheel zoom and drag pan
        page.goto(url + "?n=101#zoom=mid&focus=phase2")
        page.wait_for_selector(".item", state="attached")
        page.evaluate("localStorage.clear()")
        s0 = page.evaluate("S.view.s")
        page.mouse.move(700, 400)
        for _ in range(10):
            page.mouse.wheel(0, 120)
        page.wait_for_timeout(100)
        s1 = page.evaluate("S.view.s")
        check(s1 < s0 * 0.3, f"wheel zooms out ({s0:.2f} -> {s1:.2f})")
        check(page.eval_on_selector("#layer", "e => e.classList.contains('far')"), "far level after zoom out")
        page.keyboard.press("0")
        x0 = page.evaluate("S.view.x")
        page.mouse.move(700, 600)
        page.mouse.down()
        page.mouse.move(500, 600, steps=8)
        page.mouse.up()
        check(abs(page.evaluate("S.view.x") - (x0 - 200)) < 2, "left drag pans")
        page.mouse.move(700, 600)
        page.mouse.down(button="middle")
        page.mouse.move(800, 600, steps=5)
        page.mouse.up(button="middle")
        check(abs(page.evaluate("S.view.x") - (x0 - 100)) < 2, "middle drag pans")

        # --- pan performance: 120 transform updates
        ms = page.evaluate("""() => new Promise(res => {
            let n = 0; const t0 = performance.now();
            function f() { S.view.x += 3; S.view.s *= 1.003; applyView();
              if (++n < 120) requestAnimationFrame(f); else res((performance.now() - t0) / n); }
            requestAnimationFrame(f); })""")
        check(ms < 25, f"pan/zoom frame time {ms:.1f} ms")

        # --- select + edit an item
        page.goto(url + "?n=102#zoom=mid&focus=phase2")
        page.wait_for_selector(".item", state="attached")
        page.click('.item[data-id="phase2/b"]')
        check(page.is_visible("#panel h2"), "click opens the panel")
        check(page.eval_on_selector('.item[data-id="phase1/b"]', "e => e.classList.contains('on')"), "upstream chain highlighted")
        page.click('[data-act="edit"]')
        page.fill("#f-title", "Sleep, wake and rest")
        page.select_option("#f-status", "active")
        page.click("#f-body")
        page.keyboard.press("Control+End")
        page.keyboard.type("\n\nextra")
        page.keyboard.press("Shift+Home")
        page.keyboard.press("Control+b")
        check("<strong>extra</strong>" in page.inner_html("#f-preview"), "Ctrl+B + live preview")
        page.select_option("#f-addlink", "phase4/b")
        page.click('[data-act="save"]')
        page.wait_for_timeout(400)
        it = model.load(root).item("phase2/b")
        check(it.doc.fields["title"] == "Sleep, wake and rest" and it.doc.fields["status"] == "active", "edit saved to file")
        check("**extra**" in it.doc.body and it.links == ["phase4/b"], "body + link saved")
        check(page.inner_text('.item[data-id="phase2/b"] .lv-mid .t') == "Sleep, wake and rest", "map updated")

        # --- pick a link on the map, then move to another phase
        page.click('[data-act="edit"]')
        page.click('[data-act="pick"]')
        page.click('.item[data-id="phase3/d"]')
        page.select_option("#f-phase", "phase3")
        page.click('[data-act="save"]')
        page.wait_for_timeout(400)
        check(model.load(root).item("phase3/e").links == ["phase4/b", "phase3/d"], "pick link + move item")
        check(model.check(root)["ok"], "roadmap still valid")

        # --- add item from the column foot
        page.click('.padd[data-add="phase4"]')
        page.wait_for_selector("#f-title")
        page.fill("#f-title", "Vaporisation")
        page.click('[data-act="save"]')
        page.wait_for_timeout(400)
        check(model.load(root).item("phase4/d").doc.fields["title"] == "Vaporisation", "add item")

        # --- add phase between, renumbering
        page.evaluate("zoomTo('phase1', 0.6)")
        page.wait_for_timeout(100)
        page.click('.phead[data-id="phase1"]')
        page.click('[data-act="phaseafter"]')
        page.wait_for_selector("#f-title")
        page.fill("#f-title", "Inserted")
        page.click('[data-act="save"]')
        page.wait_for_timeout(400)
        rm = model.load(root)
        check(rm.phases[1].doc.fields["name"] == "Inserted" and len(rm.phases) == 6, "insert phase")
        check(model.check(root)["ok"], "links rewritten after insert")

        # --- live reload from disk
        model.set_entry(root, "phase1/a", {"title": "Changed by an agent"})
        page.wait_for_timeout(1600)
        check(page.inner_text('.item[data-id="phase1/a"] .lv-mid .t') == "Changed by an agent", "live reload")

        # --- dirty form is not clobbered
        page.evaluate("zoomTo('phase1', 0.6)")
        page.wait_for_timeout(100)
        page.click('.item[data-id="phase1/c"]')
        page.click('[data-act="edit"]')
        page.fill("#f-title", "Half typed")
        model.set_entry(root, "phase1/c", {"status": "active"})
        page.wait_for_timeout(1600)
        check(page.is_visible("#notice") and page.input_value("#f-title") == "Half typed", "changed-on-disk notice keeps the form")
        page.click("#btn-reload")
        page.wait_for_timeout(300)

        # --- dangling link shows as a problem
        p_ = root / "phase1" / "b" / "item.md"
        p_.write_text(p_.read_text(encoding="utf-8").replace("links: [", "links: [phase9/zz, "), encoding="utf-8", newline="\n")
        page.wait_for_timeout(1600)
        check(page.is_visible("#btn-problems") and "error" in page.inner_text("#btn-problems"), "problems button")
        check(page.eval_on_selector('.item[data-id="phase1/b"]', "e => e.classList.contains('bad')"), "bad item highlighted")

        # --- search
        page.fill("#search", "status:idea")
        n_idea = sum(1 for i in model.load(root).items() if i.doc.fields.get("status") == "idea")
        check(page.inner_text("#search-count") == str(n_idea), f"search by status ({n_idea})")
        page.fill("#search", "")

        # --- delete item
        page.evaluate("zoomTo('phase4', 0.6)")
        page.wait_for_timeout(100)
        page.click('.item[data-id="phase4/d"]')
        page.click('[data-act="delete"]')
        page.wait_for_timeout(400)
        check(not (root / "phase4" / "d").exists(), "delete item")

        # --- groups in the editor (the game phase is phase6 after the insert above):
        # collapse, create, rename, move an item in
        page.evaluate("zoomTo('phase6', 0.5)")
        page.wait_for_timeout(100)
        page.click('.gtog[data-tog="phase6/g-base-building"]')
        page.wait_for_timeout(100)
        check(page.query_selector('.item[data-id="phase6/h"]') is None
              and page.eval_on_selector('.grp[data-id="phase6/g-base-building"]', "e => e.offsetHeight") < 100,
              "collapse hides a group's items")
        check(page.evaluate("S.rects['phase6/h'] === S.rects['phase6/g-base-building']"), "links to hidden items end at the group")
        page.click('.gtog[data-tog="phase6/g-base-building"]')
        page.wait_for_timeout(100)
        check(page.query_selector('.item[data-id="phase6/h"]') is not None, "expand shows them again")
        page.click('.phead[data-id="phase6"]')
        answer["prompt"] = "Logistics"
        page.click('[data-act="addgroup"]')
        page.wait_for_selector("#f-title")
        page.fill("#f-title", "Logistics hub")
        page.fill("#f-nickname", "Logi")
        page.click('[data-act="save"]')
        page.wait_for_timeout(400)
        g = model.load(root).group("phase6/g-logistics")
        check(g.doc.fields["name"] == "Logistics hub" and g.doc.fields["nickname"] == "Logi", "create + rename a group (id kept)")
        page.evaluate("zoomTo('phase6/c', 0.5)")
        page.wait_for_timeout(100)
        page.click('.item[data-id="phase6/c"]')
        page.click('[data-act="edit"]')
        page.select_option("#f-phase", "phase6/g-logistics")
        page.click('[data-act="save"]')
        page.wait_for_timeout(400)
        it = model.load(root).item("phase6/c")
        check(it.group == "phase6/g-logistics" and page.evaluate("S.sel") == "phase6/c", "move an item into a group, id unchanged")
        page.click('.grp[data-id="phase6/g-logistics"] .ghead')
        answer["prompt"] = "Inner"
        page.click('[data-act="addgroup"]')
        page.wait_for_selector("#f-title")
        page.click('[data-act="save"]')
        page.wait_for_timeout(400)
        check(model.load(root).group("phase6/g-inner").parent == "phase6/g-logistics", "nested group from the panel")
        errs = [e for e in model.check(root)["errors"] if e.get("link") != "phase9/zz"]  # planted above
        check(not errs, f"no new problems after group edits {errs}")

        # --- drag and drop: an item into another phase's grid, a reorder, a group resize
        def centre(sel):
            r = page.eval_on_selector(sel, "e => { const r = e.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2, r.left, r.top, r.right, r.bottom]; }")
            return r
        def drag_to(sel, x, y):
            c = centre(sel)
            page.mouse.move(c[0], c[1])
            page.mouse.down()
            page.mouse.move(x, y, steps=12)
            page.wait_for_timeout(80)
            page.mouse.up()
            page.wait_for_timeout(500)
        page.evaluate("select(null); zoomTo('phase2', 0.5)")  # the inserted (empty) phase, with phase1 and phase3 beside it
        page.wait_for_timeout(200)
        tgt = centre('.item[data-id="phase3/a"]')
        drag_to('.item[data-id="phase1/c"]', tgt[2] + 4, tgt[1])  # left half of phase2/a: goes before it
        rm = model.load(root)
        moved = [i for i in rm.phase("phase3").top_items if i.doc.fields.get("title", "").startswith("Command layer")]
        check(len(moved) == 1 and rm.phase("phase3").top_items[0] is moved[0], "drag an item into another phase, before the box it was dropped on")
        check(not [e for e in model.check(root)["errors"] if e.get("link") != "phase9/zz"], "links rewritten after the drag")
        first = rm.phase("phase3").top_items[0].id
        last = centre(f'.item[data-id="{rm.phase("phase3").top_items[-1].id}"]')
        drag_to(f'.item[data-id="{first}"]', last[4] - 6, last[1])  # right half of the last box: goes last
        check(model.load(root).phase("phase3").top_items[-1].id == first, "drag reorders inside a phase (order: saved)")
        page.evaluate("select(null); zoomTo('phase6/g-base-building', 0.45)")
        page.wait_for_timeout(200)
        g = "phase6/g-base-building"
        h = centre(f'.grp[data-id="{g}"] .rsz')
        page.mouse.move(h[0], h[1])
        page.mouse.down()
        gw0, sc = page.evaluate(f"[S.nodes['{g}'].w, S.view.s]")
        page.mouse.move(h[0] - (gw0 - 700) * sc, h[1] + 200, steps=10)  # about two columns wide
        page.wait_for_timeout(120)
        xs_live = {round(page.eval_on_selector(f'.item[data-id="phase6/{c}"]', "e => e.offsetLeft")) for c in "hijk"}
        page.mouse.up()
        page.wait_for_timeout(500)
        gw = model.load(root).group(g).doc.fields.get("width")
        xs_after = {round(page.eval_on_selector(f'.item[data-id="phase6/{c}"]', "e => e.offsetLeft")) for c in "hijklmnop"}
        check(len(xs_live) == 2 and isinstance(gw, int) and len(xs_after) == 2, f"resize reflows live and saves width ({gw}, {len(xs_after)} columns)")
        page.dblclick(f'.grp[data-id="{g}"] .rsz')
        page.wait_for_timeout(500)
        check("width" not in model.load(root).group(g).doc.fields, "double-click the corner: automatic size")
        c = centre('.item[data-id="phase6/h"]')
        page.mouse.move(c[0], c[1]); page.mouse.down(); page.mouse.move(c[0] + 300, c[1] + 40, steps=6)
        page.keyboard.press("Escape"); page.mouse.up(); page.wait_for_timeout(300)
        check(model.load(root).item("phase6/h").group == g, "Esc cancels a drag")

        # --- phone width
        page.set_viewport_size({"width": 390, "height": 800})
        page.goto(url + "?n=103#zoom=mid&focus=phase3&select=phase3/b")
        page.wait_for_timeout(500)
        page.screenshot(path=str(shots / "phone.png"))
        check(page.evaluate("document.documentElement.scrollWidth <= 390"), "no horizontal page scroll on phone")

        check(not errors, f"no page errors {errors}")
        browser.close()
    srv.shutdown()
    print("ALL OK" if ok else "FAILURES")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()

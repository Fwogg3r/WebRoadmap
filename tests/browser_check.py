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
        box = lambda gid: page.eval_on_selector(f'.grp[data-id="{gid}"]', "e => { const r = e.getBoundingClientRect(); return [r.left, r.top, r.right, r.bottom]; }")
        tops = [box(g) for g in ("phase5/g-vehicles-and-craft", "phase5/g-world-and-resources", "phase5/g-base-building")]
        check(tops[0][2] <= tops[1][0] and tops[1][2] <= tops[2][0], "groups of one phase side by side (x)")
        check(all(t[1] < tops[0][3] and tops[0][1] < t[3] for t in tops), "groups of one phase share a row (y ranges overlap)")
        outer, inner = box("phase5/g-world-and-resources"), box("phase5/g-ores-and-ice")
        check(outer[0] < inner[0] and outer[1] < inner[1] and inner[2] < outer[2] and inner[3] < outer[3], "nested group inside its parent")
        xs = {round(page.eval_on_selector(f'.item[data-id="phase5/{c}"]', "e => e.getBoundingClientRect().left")) for c in "hijklmnop"}
        check(len(xs) == 3, f"9 items in a group wrap into 3 columns ({len(xs)})")
        base = box("phase5/g-base-building")
        check(all(base[0] < x < base[2] for x in xs), "group items inside the group")
        p3 = page.eval_on_selector('.phead[data-id="phase3"]', "e => e.offsetWidth")
        p5 = page.eval_on_selector('.phead[data-id="phase5"]', "e => e.offsetWidth")
        check(p3 == 300 and p5 > 3 * p3, f"phase without groups keeps its width, phase with groups is wider ({p3}, {p5})")
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
        page.click('.addbtn[data-add="phase4"]')
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
        page.evaluate("zoomTo('phase6', 0.5)")
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

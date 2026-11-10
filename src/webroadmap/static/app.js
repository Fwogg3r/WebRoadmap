// WebRoadmap page: box map with pan/zoom, semantic zoom, side panel editor.
// All writes go through the server API (which edits the files); the page then
// reloads from disk. The map is one transformed layer: pan/zoom never relayout.
"use strict";

// ------------------------------------------------------------------ geometry
const COL_W = 300, COL_GAP = 120, PAD = 40;
const HEAD_H = 150, BOX_W = 276, BOX_H = 176, BOX_GAP = 22, FOOT_H = 54;
// Groups: inner padding, header height, gap between groups, between item columns, phase inset.
const GPAD = 16, GHEAD = 74, GGAP = 30, CGAP = 36, PIN = 12, GEMPTY = 44, GMIN_W = BOX_W + 2 * GPAD;
const FAR = 0.42, NEAR = 0.95, MIN_S = 0.04, MAX_S = 3;
const STATUSES = ["done", "active", "planned", "idea", "dropped"];

const $ = (s, el = document) => el.querySelector(s);
const esc = MD.esc;
const vp = $("#viewport"), layer = $("#layer"), nodesEl = $("#nodes"), edgesEl = $("#edges"), panel = $("#panel");

const S = {
  data: null, version: null, rects: {}, byId: {}, world: { w: 800, h: 600 },
  view: { x: 40, y: 40, s: 0.6 }, level: null,
  sel: null, hover: null, edit: null, pick: null, search: "", pendingDisk: false,
  out: {}, inn: {}, hidden: new Set(), collapsed: new Set(),
};

// ------------------------------------------------------------------ util
function toast(msg, error = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "show" + (error ? " error" : "");
  clearTimeout(toast.t);
  toast.t = setTimeout(() => (t.className = error ? "error" : ""), error ? 4000 : 1800);
}
function statusColor(st) {
  return `var(--s-${STATUSES.includes(st) ? st : "planned"})`;
}
const colorOf = (e) => e.color || statusColor(e.status);
const isGroup = (id) => !!id && /^[^/]+\/g-/.test(id);
const isItem = (id) => !!id && id.includes("/") && !isGroup(id);
const nameOf = (e) => e.title || e.name;
function store(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (_) {} }
function recall(k) { try { return JSON.parse(localStorage.getItem(k)); } catch (_) { return null; } }

async function api(op, args) {
  const r = await fetch("/api/" + op, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(args),
  });
  const j = await r.json().catch(() => ({ error: r.statusText }));
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}
async function mutate(op, args, msg) {
  try {
    const j = await api(op, args);
    await load(true);
    if (msg) toast(msg);
    return j;
  } catch (e) {
    toast(e.message, true);
    throw e;
  }
}

// ------------------------------------------------------------------ loading
async function load(force = false) {
  const r = await fetch("/api/roadmap", { cache: "no-store" });
  const d = await r.json();
  S.data = d;
  S.version = d.version;
  S.collapsed = new Set(recall("wr-collapsed:" + d.root) || []);
  S.pendingDisk = false;
  $("#notice").hidden = true;
  document.title = d.title + " roadmap";
  $("#title").textContent = d.title;
  $("#subtitle").textContent = d.subtitle || "";
  build();
  if (S.sel && !S.byId[S.sel]) S.sel = null;
  if (!S.edit || force) renderPanel();
}

async function poll() {
  try {
    const r = await fetch("/api/version", { cache: "no-store" });
    const { version } = await r.json();
    if (version !== S.version) {
      if (S.edit && S.edit.dirty) {
        if (!S.pendingDisk) { S.pendingDisk = true; $("#notice").hidden = false; }
      } else {
        const keepEdit = S.edit;
        await load();
        if (keepEdit) { S.edit = null; if (S.byId[keepEdit.id]) openEdit(keepEdit.id); }
      }
    }
  } catch (_) { /* server gone: keep the page */ }
  setTimeout(poll, 1000);
}

// ------------------------------------------------------------------ layout + DOM
// A phase without groups is one column of boxes (as always). A phase with groups grows
// wider: its top-level items form short columns (items_per_column, default 4) on the
// left, its groups sit side by side to the right, wrapping into a grid when there are
// many; each group lays out its own items and sub-groups the same way.
function itemBlock(n, cap) {
  if (!n) return { w: 0, h: 0, pos: [] };
  const cols = Math.max(1, Math.ceil(n / cap)), rows = Math.ceil(n / cols);
  const pos = [];
  for (let i = 0; i < n; i++) pos.push({ x: Math.floor(i / rows) * (BOX_W + CGAP), y: (i % rows) * (BOX_H + BOX_GAP) });
  return { w: cols * BOX_W + (cols - 1) * CGAP, h: rows * BOX_H + (rows - 1) * BOX_GAP, pos };
}
function groupGrid(nodes) {
  const n = nodes.length;
  if (!n) return { w: 0, h: 0, pos: [] };
  const per = n <= 3 ? n : Math.ceil(Math.sqrt(n));
  const pos = [];
  let y = 0, w = 0;
  for (let r = 0; r < n; r += per) {
    let x = 0, rh = 0;
    for (const nd of nodes.slice(r, r + per)) { pos.push({ x, y }); x += nd.w + GGAP; rh = Math.max(rh, nd.h); }
    w = Math.max(w, x - GGAP);
    y += rh + GGAP;
  }
  return { w, h: y - GGAP, pos };
}
function contentLayout(items, groups, cap, ctx) {
  const ib = itemBlock(items.length, cap);
  const nodes = groups.map((g) => measureGroup(g, cap, ctx));
  const gg = groupGrid(nodes);
  const gx = ib.w ? ib.w + GGAP : 0;
  return {
    w: gx + gg.w, h: Math.max(ib.h, gg.h),
    items: items.map((it, i) => ({ it, x: ib.pos[i].x, y: ib.pos[i].y })),
    groups: nodes.map((nd, i) => ({ nd, x: gx + gg.pos[i].x, y: gg.pos[i].y })),
  };
}
function measureGroup(g, cap, ctx) {
  const nd = { g, coll: S.collapsed.has(g.id) };
  if (nd.coll) { nd.w = GMIN_W; nd.h = GHEAD; return nd; }
  nd.c = contentLayout(g.items.map((i) => ctx.items[i]).filter(Boolean), g.groups.map((x) => ctx.groups[x]).filter(Boolean), cap, ctx);
  nd.w = Math.max(GMIN_W, nd.c.w + 2 * GPAD);
  nd.h = GHEAD + (nd.c.h || GEMPTY) + GPAD;
  return nd;
}
function groupItems(g, ctx, out = []) {
  for (const i of g.items) if (ctx.items[i]) out.push(ctx.items[i]);
  for (const x of g.groups) if (ctx.groups[x]) groupItems(ctx.groups[x], ctx, out);
  return out;
}
const progress = (items) => {
  const total = items.filter((i) => i.status !== "dropped").length;
  return { total, done: items.filter((i) => i.status === "done").length };
};

function build() {
  const d = S.data;
  S.rects = {}; S.byId = {}; S.out = {}; S.inn = {}; S.hidden = new Set();
  const cap = Math.max(1, parseInt(d.items_per_column, 10) || 4);
  const html = [], ghtml = [], ihtml = [];

  const itemHtml = (it, ix, iy) => {
    S.rects[it.id] = { x: ix, y: iy, w: BOX_W, h: BOX_H };
    const ic = colorOf(it);
    const chips = `<span class="chip" style="--c:${statusColor(it.status)}">${esc(it.status)}</span><span class="idl">${it.id}</span><span class="badge-bad" hidden>!</span>`;
    ihtml.push(`<div class="item st-${esc(it.status)}${it.bold ? " bold" : ""}" data-id="${it.id}" style="left:${ix}px;top:${iy}px;width:${BOX_W}px;height:${BOX_H}px;--c:${ic}">
      <div class="lv lv-mid"><div class="t">${esc(it.title)}</div><div class="chips">${chips}</div></div>
      <div class="lv lv-near"><div class="t">${esc(it.title)}</div><div class="chips">${chips}${it.nickname ? `<span class="idl">${esc(it.nickname)}</span>` : ""}</div>
        <div class="notes">${MD.render(it.body)}</div></div>
    </div>`);
  };
  // Place a measured group and its contents at (x, y); items in a collapsed group point at its header.
  const placeGroup = (nd, x, y, ctx, hiddenIn) => {
    const g = nd.g;
    const head = { x, y, w: nd.w, h: GHEAD, box: { x, y, w: nd.w, h: nd.h } };
    S.rects[g.id] = hiddenIn ? S.rects[hiddenIn] : head;
    const all = groupItems(g, ctx);
    const { done, total } = progress(all);
    const c = colorOf(g);
    const leaf = !g.groups.length;
    if (!hiddenIn) {
      ghtml.push(`<div class="grp st-${esc(g.status)}${nd.coll ? " coll" : ""}${leaf ? " leaf" : ""}${g.error ? " bad" : ""}" data-id="${g.id}" style="left:${x}px;top:${y}px;width:${nd.w}px;height:${nd.h}px;--c:${c}">
        <div class="ghead lv lv-mn">
          <button class="gtog" data-tog="${g.id}" title="${nd.coll ? "Expand" : "Collapse"}">${nd.coll ? "&#9656;" : "&#9662;"}</button>
          <div class="gtxt"><div class="gname">${esc(g.name)}</div>
            <div class="grow"><span class="chip" style="--c:${statusColor(g.status)}">${esc(g.status)}</span><span class="count">${done}/${total}</span><span class="idl">${g.id}</span></div></div>
          <button class="gadd" data-add="${g.phase}" data-group="${g.id}" title="Add an item to this group">+</button>
        </div>
        ${!nd.coll && !nd.c.items.length && !nd.c.groups.length ? `<div class="gempty lv lv-mn">empty group</div>` : ""}
        <div class="gfar lv lv-far"><div class="nick">${esc(g.nickname || g.name)}</div>${leaf || nd.coll ? `<div class="meta">${done}/${total}</div>` : ""}</div>
      </div>`);
    }
    const inner = hiddenIn || (nd.coll ? g.id : null);
    if (inner) {
      for (const it of all) { S.rects[it.id] = S.rects[inner]; ctx.hidden.add(it.id); }
      const walk = (gg) => gg.groups.forEach((x) => { const sg = ctx.groups[x]; if (sg) { S.rects[x] = S.rects[inner]; S.byId[x] = sg; walk(sg); } });
      walk(g);
      return;
    }
    const ox = x + GPAD, oy = y + GHEAD;
    for (const e of nd.c.items) itemHtml(e.it, ox + e.x, oy + e.y);
    for (const e of nd.c.groups) placeGroup(e.nd, ox + e.x, oy + e.y, ctx, null);
  };

  // Measure every phase first: widths vary, heights share the tallest column.
  const metas = d.phases.map((p) => {
    const ctx = { items: {}, groups: {}, hidden: S.hidden };
    p.groups = p.groups || [];
    for (const it of p.items) { ctx.items[it.id] = it; it.phase = p.id; S.byId[it.id] = it; }
    for (const g of p.groups) { ctx.groups[g.id] = g; g.phase = p.id; S.byId[g.id] = g; }
    const top = p.items.filter((i) => !i.group || !ctx.groups[i.group]);
    const topGroups = p.groups.filter((g) => g.parent === p.id || !ctx.groups[g.parent]);
    const grouped = topGroups.length > 0;
    const c = contentLayout(top, topGroups, grouped ? cap : Infinity, ctx);
    const w = grouped ? Math.max(COL_W, c.w + 2 * PIN) : COL_W;
    const foot = c.h ? c.h + BOX_GAP : 0;
    return { p, ctx, c, w, foot, grouped };
  });
  const colH = HEAD_H + 26 + Math.max(0, ...metas.map((m) => m.foot)) + FOOT_H + 20;
  let x = PAD;
  for (const m of metas) {
    const { p, c, w, ctx } = m, y = PAD;
    S.byId[p.id] = p;
    const { done, total } = progress(p.items);
    const pct = total ? Math.round((100 * done) / total) : p.status === "done" ? 100 : 0;
    const pc = colorOf(p);
    S.rects[p.id] = { x, y, w, h: HEAD_H, col: { x, y, w, h: colH } };
    html.push(`<div class="col st-${esc(p.status)}${m.grouped ? " grouped" : ""}" data-phase="${p.id}" style="left:${x - 10}px;top:${y - 10}px;width:${w + 20}px;height:${colH + 20}px;--c:${pc}">
      <div class="farc lv lv-far">
        <div class="nick">${esc(p.nickname || p.name)}</div>
        ${p.nickname ? `<div class="name">${esc(p.name)}</div>` : ""}
        <div class="prog"><i style="width:${pct}%"></i></div>
        <div class="meta">${done}/${total} done &middot; ${esc(p.status)}</div>
      </div></div>`);
    html.push(`<div class="phead lv st-${esc(p.status)}${p.error ? " bad" : ""}" data-id="${p.id}" style="left:${x}px;top:${y}px;width:${w}px;height:${HEAD_H}px;--c:${pc}">
      <div class="pid">${p.id}${p.dates ? " &middot; " + esc(String(p.dates)) : ""}</div>
      <div class="pname">${esc(p.name)}</div>
      <div class="pnick">${esc(p.nickname || "")}</div>
      <div class="row"><span class="chip" style="--c:${pc}">${esc(p.status)}</span><div class="prog"><i style="width:${pct}%"></i></div><span class="count">${done}/${total}</span></div>
    </div>`);
    const ox = x + (m.grouped ? PIN : (COL_W - BOX_W) / 2), oy = y + HEAD_H + 26;
    for (const e of c.items) itemHtml(e.it, ox + e.x, oy + e.y);
    for (const e of c.groups) placeGroup(e.nd, ox + e.x, oy + e.y, ctx, null);
    html.push(`<div class="addbtn lv" data-add="${p.id}" title="Add an item to ${p.id}" style="left:${ox}px;top:${oy + m.foot}px;width:${BOX_W}px;height:${FOOT_H - 10}px">+</div>`);
    x += w + COL_GAP;
  }
  const ax = x;
  html.push(`<div class="addphase lv" data-addphase="1" title="Add a phase" style="left:${ax}px;top:${PAD}px;width:${COL_W * 0.6}px;height:${HEAD_H}px">+ Phase</div>`);
  S.world = { w: ax + COL_W * 0.6 + PAD, h: PAD * 2 + colH };
  nodesEl.innerHTML = html.join("") + ghtml.join("") + ihtml.join("");
  $("#empty").hidden = d.phases.length > 0;
  $("#empty").textContent = "No phases yet: click “+ Phase”.";

  // Edges: outgoing links per item; missing targets are drawn as red stubs.
  const bad = new Set((d.check?.errors || []).filter((e) => e.link).map((e) => e.id));
  const paths = [];
  for (const p of d.phases) for (const it of p.items) {
    S.out[it.id] = it.links || [];
    for (const t of S.out[it.id]) (S.inn[t] = S.inn[t] || []).push(it.id);
  }
  for (const p of d.phases) for (const it of p.items) {
    const a = S.rects[it.id];
    if (!a) continue;
    for (const t of S.out[it.id]) {
      const b = S.rects[t];
      if (!b) {
        const x1 = a.x + a.w, y1 = a.y + a.h / 2;
        paths.push(`<path class="bad" data-from="${it.id}" data-to="${esc(t)}" d="M${x1},${y1} C${x1 + 40},${y1} ${x1 + 40},${y1 - 40} ${x1 + 70},${y1 - 40}" marker-end="url(#arr-bad)"/>`);
        continue;
      }
      if (a === b) continue; // both ends inside the same collapsed group
      const [d2, cls] = edgePath(a, b, isItem(t) && !S.hidden.has(t));
      paths.push(`<path class="${cls}" data-from="${it.id}" data-to="${t}" d="${d2}" marker-end="url(#arr)"/>`);
    }
  }
  edgesEl.setAttribute("width", S.world.w);
  edgesEl.setAttribute("height", S.world.h);
  edgesEl.innerHTML = `<defs>
    <marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M0,0L10,5L0,10z" style="fill:var(--edge)"/></marker>
    <marker id="arr-on" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" orient="auto"><path d="M0,0L10,5L0,10z" style="fill:var(--hl)"/></marker>
    <marker id="arr-bad" viewBox="0 0 10 10" refX="5" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M1,1L9,9M9,1L1,9" style="stroke:var(--bad);stroke-width:2.5"/></marker>
    </defs>` + paths.join("");
  for (const id of bad) {
    const el = nodesEl.querySelector(`.item[data-id="${CSS.escape(id)}"]`);
    if (el) { el.classList.add("bad"); el.querySelectorAll(".badge-bad").forEach((b) => (b.hidden = false)); }
  }
  // Problems button
  const errs = d.check?.errors || [], warns = d.check?.warnings || [];
  const pb = $("#btn-problems");
  pb.hidden = !errs.length && !warns.length;
  pb.textContent = errs.length ? `${errs.length} error${errs.length > 1 ? "s" : ""}` : `${warns.length} warning${warns.length > 1 ? "s" : ""}`;
  pb.style.background = errs.length ? "" : "var(--s-active)";
  pb.style.borderColor = errs.length ? "" : "var(--s-active)";

  applySearch();
  applyHighlight();
  markSel();
}

function edgePath(a, b, toItem) {
  const x1 = a.x + a.w, y1 = a.y + a.h / 2;
  let x2 = b.x, y2 = b.y + b.h / 2;
  const cls = toItem ? [] : ["tophase"];
  if (x2 >= x1 + 20) {
    const dx = Math.max(50, (x2 - x1) * 0.45);
    return [`M${x1},${y1} C${x1 + dx},${y1} ${x2 - dx},${y2} ${x2 - 2},${y2}`, cls.join(" ")];
  }
  if (b.x < a.x + a.w && a.x < b.x + b.w) {
    // Overlapping columns (same column, or a box under a wide group header): loop out of the right side and back in.
    x2 = b.x + b.w;
    const dx = 46 + Math.min(70, Math.abs(y2 - y1) * 0.12) + Math.max(0, x1 - x2);
    return [`M${x1},${y1} C${x1 + dx},${y1} ${x2 + dx},${y2} ${x2 + 2},${y2}`, cls.join(" ")];
  }
  cls.push("back");
  const dx = 140;
  return [`M${x1},${y1} C${x1 + dx},${y1} ${x2 - dx},${y2} ${x2 - 2},${y2}`, cls.join(" ")];
}

// ------------------------------------------------------------------ view (pan/zoom)
let raf = 0, idleT = 0;
function applyView() {
  if (raf) return;
  raf = requestAnimationFrame(() => {
    raf = 0;
    const v = S.view;
    layer.style.transform = `translate(${v.x}px,${v.y}px) scale(${v.s})`;
    layer.classList.add("moving");
    clearTimeout(idleT);
    // Drop will-change when idle so text re-rasterises sharply at the new scale.
    idleT = setTimeout(() => layer.classList.remove("moving"), 180);
    const lvl = v.s < FAR ? "far" : v.s < NEAR ? "mid" : "near";
    if (lvl !== S.level) {
      layer.classList.remove("far", "mid", "near");
      layer.classList.add(lvl);
      S.level = lvl;
      $("#zoom-level").textContent = lvl;
    }
    if (lvl === "far") {
      const inv = (1 / v.s).toFixed(2);
      if (layer.style.getPropertyValue("--inv") !== inv) layer.style.setProperty("--inv", inv);
    }
    clearTimeout(applyView.t);
    applyView.t = setTimeout(() => S.data && store("wr-view:" + S.data.root, S.view), 400);
  });
}
function zoomAt(f, cx, cy) {
  const v = S.view;
  const s2 = Math.min(MAX_S, Math.max(MIN_S, v.s * f));
  const k = s2 / v.s;
  v.x = cx - (cx - v.x) * k;
  v.y = cy - (cy - v.y) * k;
  v.s = s2;
  applyView();
}
function vpSize() {
  const r = vp.getBoundingClientRect();
  const pw = panel.hidden || innerWidth <= 720 ? 0 : panel.offsetWidth;
  return { w: r.width - pw, h: r.height };
}
function fitRect(r, pad = 40, maxS = 1.2) {
  const { w, h } = vpSize();
  const s = Math.max(MIN_S, Math.min(maxS, (w - 2 * pad) / r.w, (h - 2 * pad) / r.h));
  S.view = { s, x: w / 2 - (r.x + r.w / 2) * s, y: h / 2 - (r.y + r.h / 2) * s };
  applyView();
}
const fitAll = () => fitRect({ x: 0, y: 0, w: S.world.w, h: S.world.h }, 30, 1.2);
function zoomTo(id, s) {
  const r = S.rects[id];
  if (!r) return;
  if (r.box) return fitRect(r.box, 40, s);
  const { w, h } = vpSize();
  const box = r.col && s < NEAR ? r.col : r;
  S.view.s = s;
  S.view.x = w / 2 - (box.x + box.w / 2) * s;
  S.view.y = Math.min(60, h / 2 - (box.y + box.h / 2) * s);
  if (!r.col) S.view.y = h / 2 - (r.y + r.h / 2) * s;
  applyView();
}

// Pointer input: left/middle drag pans, a click without movement selects,
// two touches pinch-zoom. Wheel zooms about the cursor.
const ptrs = new Map();
let drag = null;
vp.addEventListener("pointerdown", (e) => {
  if (e.pointerType === "mouse" && e.button !== 0 && e.button !== 1) return;
  if (e.button === 1) e.preventDefault();
  vp.focus({ preventScroll: true });
  ptrs.set(e.pointerId, { x: e.clientX, y: e.clientY });
  if (ptrs.size === 1) {
    drag = { x0: e.clientX, y0: e.clientY, moved: false, target: e.target, button: e.button, t0: performance.now() };
  } else if (ptrs.size === 2) {
    drag = { pinch: true, moved: true };
  }
});
vp.addEventListener("pointermove", (e) => {
  const p = ptrs.get(e.pointerId);
  if (!p) {
    if (e.pointerType === "mouse") hoverAt(e.target);
    return;
  }
  if (ptrs.size === 2) {
    const [a, b] = [...ptrs.values()];
    const d0 = Math.hypot(a.x - b.x, a.y - b.y), m0 = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
    p.x = e.clientX; p.y = e.clientY;
    const [c, d] = [...ptrs.values()];
    const d1 = Math.hypot(c.x - d.x, c.y - d.y), m1 = { x: (c.x + d.x) / 2, y: (c.y + d.y) / 2 };
    const r = vp.getBoundingClientRect();
    S.view.x += m1.x - m0.x; S.view.y += m1.y - m0.y;
    if (d0 > 0) zoomAt(d1 / d0, m1.x - r.left, m1.y - r.top);
    else applyView();
    return;
  }
  const dx = e.clientX - p.x, dy = e.clientY - p.y;
  p.x = e.clientX; p.y = e.clientY;
  if (!drag) return;
  if (!drag.moved && Math.hypot(e.clientX - drag.x0, e.clientY - drag.y0) > 4) {
    drag.moved = true;
    vp.setPointerCapture(e.pointerId);
    vp.classList.add("panning");
  }
  if (drag.moved) { S.view.x += dx; S.view.y += dy; applyView(); }
});
function endPtr(e) {
  if (!ptrs.has(e.pointerId)) return;
  ptrs.delete(e.pointerId);
  if (ptrs.size === 0) {
    vp.classList.remove("panning");
    if (drag && !drag.moved && !drag.pinch && drag.button === 0 && e.type === "pointerup") clickAt(drag.target, e);
    drag = null;
  }
}
vp.addEventListener("pointerup", endPtr);
vp.addEventListener("pointercancel", endPtr);
vp.addEventListener("mousedown", (e) => { if (e.button === 1) e.preventDefault(); }); // no autoscroll
vp.addEventListener("dblclick", (e) => {
  if (e.target.closest(".gtog,.gadd")) return;
  const it = e.target.closest(".item,.phead,.ghead");
  const id = it && (it.dataset.id || it.parentElement.dataset.id);
  if (id) zoomTo(id, isItem(id) ? 1.6 : isGroup(id) ? 0.9 : 0.7);
});
vp.addEventListener("wheel", (e) => {
  e.preventDefault();
  const unit = e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? 400 : 1;
  if (e.shiftKey && !e.ctrlKey) {
    S.view.x -= (e.deltaY || e.deltaX) * unit;
    applyView();
    return;
  }
  const r = vp.getBoundingClientRect();
  const k = e.ctrlKey ? 0.01 : 0.0016; // ctrl+wheel = trackpad pinch: small deltas
  zoomAt(Math.exp(-e.deltaY * unit * k), e.clientX - r.left, e.clientY - r.top);
}, { passive: false });
vp.addEventListener("mouseleave", () => hoverAt(null));
addEventListener("resize", () => applyView());

addEventListener("keydown", (e) => {
  const tag = (e.target.tagName || "").toLowerCase();
  if (tag === "input" || tag === "textarea" || tag === "select") {
    if (e.key === "Escape") e.target.blur();
    return;
  }
  const step = e.shiftKey ? 300 : 80;
  const { w, h } = vpSize();
  switch (e.key) {
    case "ArrowLeft": S.view.x += step; break;
    case "ArrowRight": S.view.x -= step; break;
    case "ArrowUp": S.view.y += step; break;
    case "ArrowDown": S.view.y -= step; break;
    case "+": case "=": zoomAt(1.25, w / 2, h / 2); return;
    case "-": case "_": zoomAt(0.8, w / 2, h / 2); return;
    case "0": fitAll(); return;
    case "/": e.preventDefault(); $("#search").focus(); return;
    case "Escape":
      if (S.pick) return endPick();
      if (S.edit) return;
      select(null); return;
    default: return;
  }
  e.preventDefault();
  applyView();
});

// ------------------------------------------------------------------ selection, hover, chains
function hoverAt(target) {
  const el = target && target.closest && target.closest(".item,.phead,.ghead");
  const id = el ? el.dataset.id || el.parentElement.dataset.id : null;
  if (id === S.hover) return;
  S.hover = id;
  applyHighlight();
}
function chainOf(id) {
  const on = new Set([id]);
  const walk = (start, next) => {
    const stack = [start];
    while (stack.length) {
      const n = stack.pop();
      for (const m of next(n) || []) if (!on.has(m)) { on.add(m); stack.push(m); }
    }
  };
  walk(id, (n) => S.out[n]);
  walk(id, (n) => S.inn[n]);
  return on;
}
function applyHighlight() {
  const id = S.hover || S.sel;
  nodesEl.querySelectorAll(".on").forEach((e) => e.classList.remove("on"));
  edgesEl.querySelectorAll(".on").forEach((e) => { e.classList.remove("on"); e.setAttribute("marker-end", "url(#arr)"); });
  if (!id || !S.byId[id] || (!isItem(id) && !(S.inn[id] || []).length)) { layer.classList.remove("chain"); return; }
  const on = chainOf(id);
  layer.classList.add("chain");
  for (const n of on) nodesEl.querySelectorAll(`[data-id="${CSS.escape(n)}"]`).forEach((e) => e.classList.add("on"));
  edgesEl.querySelectorAll("path[data-from]").forEach((p) => {
    if (on.has(p.dataset.from) && on.has(p.dataset.to)) { p.classList.add("on"); p.setAttribute("marker-end", "url(#arr-on)"); }
  });
}
function markSel() {
  nodesEl.querySelectorAll(".sel").forEach((e) => e.classList.remove("sel"));
  if (S.sel) nodesEl.querySelectorAll(`[data-id="${CSS.escape(S.sel)}"]`).forEach((e) => e.classList.add("sel"));
}
function select(id) {
  S.panelMode = null;
  S.sel = id;
  S.edit = null;
  markSel();
  applyHighlight();
  renderPanel();
}

function clickAt(target, e) {
  const el = target.closest(".gtog,.gadd,.item,.phead,.addbtn,.addphase,.grp,.col");
  if (S.pick) {
    const g = el && el.closest(".grp");
    const t = el && (el.dataset.id || el.dataset.phase || (g && g.dataset.id));
    if (t) pickTarget(t);
    return;
  }
  if (!el) return select(null);
  if (el.dataset.tog) return toggleGroup(el.dataset.tog);
  if (el.dataset.add) return addItem(el.dataset.add, el.dataset.group);
  if (el.dataset.addphase) return addPhase(null);
  if (el.classList.contains("grp") && S.level === "far") return zoomTo(el.dataset.id, 0.6);
  if (el.classList.contains("col")) {
    if (S.level === "far") return zoomTo(el.dataset.phase, 0.62);
    return select(null);
  }
  select(el.dataset.id);
}

// ------------------------------------------------------------------ search
function applySearch() {
  const q = S.search.trim().toLowerCase();
  nodesEl.querySelectorAll(".dim,.hit").forEach((e) => e.classList.remove("dim", "hit"));
  if (!q) { $("#search-count").textContent = ""; return []; }
  const terms = q.split(/\s+/);
  const hits = [];
  for (const p of S.data.phases) {
    let phaseHit = false;
    for (const it of p.items) {
      let gs = "";
      for (let g = S.byId[it.group]; g && isGroup(g.id); g = S.byId[g.parent]) gs += ` ${g.name} ${g.nickname || ""}`;
      const hay = `${it.id} ${it.title} ${it.nickname || ""} ${MD.text(it.body)} ${p.name} ${p.nickname || ""}${gs}`.toLowerCase();
      const ok = terms.every((t) => {
        const m = /^status:(\w*)$/.exec(t);
        return m ? it.status.startsWith(m[1]) : hay.includes(t);
      });
      const el = nodesEl.querySelector(`.item[data-id="${CSS.escape(it.id)}"]`);
      if (ok) { hits.push(it.id); phaseHit = true; el && el.classList.add("hit"); } else el && el.classList.add("dim");
    }
    if (!phaseHit) nodesEl.querySelectorAll(`[data-phase="${p.id}"],.phead[data-id="${p.id}"]`).forEach((e) => e.classList.add("dim"));
    const hit = new Set(hits);
    for (const g of p.groups || []) {
      if (!descItems(g.id).some((i) => hit.has(i))) nodesEl.querySelector(`.grp[data-id="${CSS.escape(g.id)}"]`)?.classList.add("dim");
    }
  }
  $("#search-count").textContent = `${hits.length}`;
  return hits;
}
$("#search").addEventListener("input", (e) => { S.search = e.target.value; applySearch(); });
$("#search").addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    const hits = applySearch();
    if (hits.length) {
      const i = (applySearch.i = ((applySearch.i ?? -1) + 1) % hits.length);
      zoomTo(hits[i], Math.max(S.view.s, NEAR + 0.05));
      select(hits[i]);
    }
  }
});

// ------------------------------------------------------------------ panel: view
function linkChip(id, removable) {
  const ok = !!S.byId[id];
  const label = ok ? nameOf(S.byId[id]) : "missing";
  return `<span class="lnk${ok ? "" : " bad"}" data-goto="${esc(id)}" title="${esc(id)}"><b>${esc(id)}</b> ${esc(label)}${removable ? `<button class="rm" data-unlink="${esc(id)}" title="Remove link">&times;</button>` : ""}</span>`;
}
function renderPanel() {
  if (!S.sel && !S.panelMode) { panel.hidden = true; return; }
  panel.hidden = false;
  if (S.panelMode === "problems") return renderProblems();
  if (S.panelMode === "info") return renderInfo();
  if (S.edit) return renderEdit();
  const e = S.byId[S.sel];
  if (!e) { panel.hidden = true; return; }
  const item = isItem(S.sel), group = isGroup(S.sel);
  const c = colorOf(e);
  const probs = (S.data.check.errors || []).concat(S.data.check.warnings || []).filter((p) => p.id === S.sel);
  const extra = Object.entries(e).filter(([k]) => !["id", "slug", "title", "name", "nickname", "status", "color", "bold", "links", "body", "summary", "items", "num", "phase", "error", "group", "groups", "parent", "status_from_items"].includes(k));
  const chips = (ids) => ids.map((i) => linkChip(i)).join("") || '<span class="idl">none</span>';
  const where = item ? e.group || e.phase : group ? e.parent : null;
  panel.innerHTML = `
    <div class="ptop"><span class="idl">${S.sel}</span><button class="x" data-act="close" title="Close (Esc)">&times;</button></div>
    <h2>${esc(item ? e.title : e.name)}</h2>
    <div class="sub"><span class="chip" style="--c:${statusColor(e.status)}">${esc(e.status)}</span>${group && e.status_from_items ? '<span class="idl">from items</span>' : ""}
      ${e.nickname ? `<span>${esc(e.nickname)}</span>` : ""}${e.bold ? "<b>milestone</b>" : ""}
      ${e.color ? `<span class="chip" style="--c:${esc(e.color)}">${esc(e.color)}</span>` : ""}</div>
    ${probs.map((p) => `<div class="${(S.data.check.errors || []).includes(p) ? "err" : "warn"}">${esc(p.msg)}</div>`).join("")}
    <div class="md">${MD.render(item ? e.body : e.summary) || '<p style="color:var(--muted)">No notes yet.</p>'}</div>
    ${extra.length ? `<h4>Fields</h4>${extra.map(([k, v]) => `<div><span class="idl">${esc(k)}:</span> ${MD.inline(Array.isArray(v) ? v.join(", ") : String(v))}</div>`).join("")}` : ""}
    ${item ? `<h4>Leads to</h4><div class="links">${(e.links || []).map((l) => linkChip(l)).join("") || '<span class="idl">none</span>'}</div>` : ""}
    <h4>Comes from</h4><div class="links">${(S.inn[S.sel] || []).map((l) => linkChip(l)).join("") || '<span class="idl">none</span>'}</div>
    ${where && where !== e.phase ? `<h4>In group</h4><div class="links">${chips([where])}</div>` : ""}
    ${group ? `<h4>Items</h4><div class="links">${chips(e.items)}</div>${e.groups.length ? `<h4>Groups</h4><div class="links">${chips(e.groups)}</div>` : ""}` : ""}
    ${!item && !group ? `<h4>Items</h4><div class="links">${chips(e.items.map((i) => i.id))}</div>${(e.groups || []).length ? `<h4>Groups</h4><div class="links">${chips(e.groups.map((g) => g.id))}</div>` : ""}` : ""}
    <div class="actions">
      <button class="primary" data-act="edit">Edit</button>
      ${group ? `<button data-act="additem">+ Item</button><button data-act="addgroup" title="Add a group inside this one">+ Group</button>
        <button data-act="toggle">${S.collapsed.has(S.sel) ? "Expand" : "Collapse"}</button>` : ""}
      ${!item && !group ? `<button data-act="additem">+ Item</button><button data-act="addgroup">+ Group</button><button data-act="phaseafter">+ Phase after</button>
        <button data-act="left" title="Move phase left">&larr;</button><button data-act="right" title="Move phase right">&rarr;</button>` : ""}
      <button data-act="zoom">Zoom to</button>
      <button class="danger" data-act="delete">Delete</button>
    </div>`;
  panel.style.setProperty("--c", c);
}
function renderProblems() {
  const c = S.data.check;
  panel.innerHTML = `<div class="ptop"><b>Validation</b><button class="x" data-act="close">&times;</button></div>
    ${c.errors.map((p) => `<div class="problem err" data-goto="${esc(p.id)}"><span class="idl">${esc(p.id)}</span>${esc(p.msg)}</div>`).join("")}
    ${c.warnings.map((p) => `<div class="problem warn" data-goto="${esc(p.id)}"><span class="idl">${esc(p.id)}</span>${esc(p.msg)}</div>`).join("")}
    ${!c.errors.length && !c.warnings.length ? "<p>No problems.</p>" : ""}
    <p class="idl">Same rules as <code>roadmap check</code>.</p>`;
}
function renderInfo() {
  const d = S.data;
  panel.innerHTML = `<div class="ptop"><b>${esc(d.title)}</b><button class="x" data-act="close">&times;</button></div>
    ${d.subtitle ? `<div class="sub">${esc(d.subtitle)}</div>` : ""}
    <div class="md">${MD.render(d.intro)}</div>
    <h4>Folder</h4><div class="idl">${esc(d.root)}</div>
    <h4>Controls</h4><div class="md">${MD.render(`- **Pan:** drag (left or middle button), arrows, one finger
- **Zoom:** wheel / pinch / \`+\` \`-\`; \`0\` fits all; double-click zooms to a box
- **Shift+wheel:** scroll sideways
- **Click** a box to open it; **Edit** to change it; \`/\` searches (\`status:active\`)
- **Groups:** the arrow in a group's header collapses it; **+** adds an item to it; an item's **Edit** form moves it between groups
- Zoom out for phase overview, in for full notes`)}</div>`;
}

panel.addEventListener("click", async (e) => {
  const un = e.target.closest("[data-unlink]");
  if (un) {
    e.stopPropagation();
    if (S.edit) { S.edit.links = S.edit.links.filter((l) => l !== un.dataset.unlink); S.edit.dirty = true; renderEdit(true); }
    return;
  }
  const g = e.target.closest("[data-goto]");
  if (g && !S.edit) {
    const id = g.dataset.goto;
    S.panelMode = null;
    if (S.byId[id]) { select(id); zoomTo(id, Math.max(S.view.s, isItem(id) ? NEAR + 0.05 : 0.6)); }
    return;
  }
  const a = e.target.closest("[data-act]");
  if (!a) return;
  const act = a.dataset.act, id = S.sel, ent = S.byId[id];
  switch (act) {
    case "close":
      if (S.edit && S.edit.dirty && !confirm("Discard unsaved changes?")) return;
      S.panelMode = null; S.edit = null; select(null); break;
    case "edit": openEdit(id); break;
    case "zoom": zoomTo(id, isItem(id) ? 1.4 : isGroup(id) ? 0.9 : 0.62); break;
    case "additem": isGroup(id) ? addItem(ent.phase, id) : addItem(id); break;
    case "addgroup": addGroup(id); break;
    case "toggle": toggleGroup(id); renderPanel(); break;
    case "phaseafter": addPhase(id); break;
    case "left": case "right": {
      const n = S.data.phases.findIndex((p) => p.id === id) + 1 + (act === "left" ? -1 : 1);
      if (n < 1 || n > S.data.phases.length) return;
      const r = await mutate("mv", { id, target: String(n) }, `moved ${id} to position ${n}`);
      select(r.id);
      break;
    }
    case "delete": {
      const inner = isGroup(id) ? descItems(id).length + descGroups(id).length : 0;
      const what = isItem(id) ? `item ${id} "${ent.title}"`
        : isGroup(id) ? `group ${id} "${ent.name}"${inner ? ` and the ${inner} item(s) and group(s) inside it` : ""}`
        : `phase ${id} "${ent.name}" and its ${ent.items.length} item(s)`;
      if (!confirm(`Delete ${what}? Links to it are removed${isItem(id) || isGroup(id) ? "" : " and later phases renumbered"}.`)) return;
      await mutate("rm", { id, force: isGroup(id) }, `deleted ${id}`);
      select(null);
      break;
    }
    case "save": saveEdit(); break;
    case "cancel":
      if (S.edit.dirty && !confirm("Discard unsaved changes?")) return;
      S.edit = null; renderPanel(); break;
    case "pick": startPick(); break;
    case "resetcolor": S.edit.color = ""; S.edit.dirty = true; renderEdit(true); break;
    case "bold": wrapSel("**"); break;
    case "italic": wrapSel("*"); break;
    case "code": wrapSel("`"); break;
  }
});

// ------------------------------------------------------------------ panel: edit
function openEdit(id) {
  const e = S.byId[id];
  if (!e) return;
  S.sel = id;
  S.panelMode = null;
  const item = isItem(id), group = isGroup(id);
  S.edit = {
    id, item, group, dirty: false,
    title: nameOf(e), nickname: e.nickname || "", status: group && e.status_from_items ? "" : e.status, color: e.color || "",
    bold: !!e.bold, links: [...(e.links || [])], body: (item ? e.body : e.summary) || "",
    phase: item ? e.group || e.phase : group ? e.parent : null, dates: e.dates ? String(e.dates) : "",
  };
  S.edit.orig = JSON.parse(JSON.stringify(S.edit));
  markSel();
  renderPanel();
  setTimeout(() => $("#f-title")?.focus(), 0);
}
function renderEdit(keepFocus) {
  const f = S.edit, item = f.item, group = f.group;
  const active = keepFocus && document.activeElement && document.activeElement.id;
  const ids = Object.keys(S.byId).filter((x) => x !== f.id && !f.links.includes(x));
  const opt = (v, cur, label = v) => `<option value="${esc(v)}"${v === cur ? " selected" : ""}>${esc(label)}</option>`;
  panel.innerHTML = `
    <div class="ptop"><span class="idl">${f.id}</span><b>Edit</b><span class="dirty-note">${f.dirty ? "unsaved" : ""}</span><button class="x" data-act="close">&times;</button></div>
    <label>${item ? "Title" : "Name"}</label><input type="text" id="f-title" value="${esc(f.title)}">
    <div class="row2">
      <div><label>Nickname (far zoom)</label><input type="text" id="f-nickname" value="${esc(f.nickname)}"></div>
      <div><label>Status</label><select id="f-status">${group ? opt("", f.status, "(from items)") : ""}${STATUSES.map((s) => opt(s, f.status)).join("")}</select></div>
    </div>
    <label>Colour (empty = from status)</label>
    <div class="colorrow"><input type="color" id="f-colorpick" value="${/^#[0-9a-f]{6}$/i.test(f.color) ? f.color : "#3b82f6"}">
      <input type="text" id="f-color" value="${esc(f.color)}" placeholder="from status"><button data-act="resetcolor">Reset</button></div>
    ${item ? `<label class="check"><input type="checkbox" id="f-bold"${f.bold ? " checked" : ""}> Bold (milestone)</label>
      <label>Phase / group</label><select id="f-phase">${containerOptions(null, f.phase)}</select>
      <label>Links (leads to)</label>
      <div class="links">${f.links.map((l) => linkChip(l, true)).join("") || '<span class="idl">none</span>'}</div>
      <div class="colorrow" style="margin-top:6px"><select id="f-addlink"><option value="">Add link to...</option>${ids.map((x) => opt(x, "", `${x}: ${nameOf(S.byId[x])}`)).join("")}</select>
        <button data-act="pick" title="Click a box on the map">Pick on map</button></div>`
      : group ? `<label>Inside (phase or group)</label><select id="f-phase">${containerOptions(f.id, f.phase)}</select>`
      : `<label>Dates</label><input type="text" id="f-dates" value="${esc(f.dates)}" placeholder="2026-10-20 .. 2026-10-28">`}
    <label>${item ? "Notes (Markdown: What / Why / How)" : "Summary (Markdown)"}</label>
    <div class="mdtools"><button data-act="bold" title="Bold (Ctrl+B)"><b>B</b></button><button data-act="italic" title="Italic (Ctrl+I)"><i>I</i></button><button data-act="code" title="Code">&lt;/&gt;</button></div>
    <textarea id="f-body" spellcheck="true">${esc(f.body)}</textarea>
    <label>Preview</label><div class="preview md" id="f-preview">${MD.render(f.body)}</div>
    <div class="actions"><button class="primary" data-act="save">Save</button><button data-act="cancel">Cancel</button></div>`;
  if (active && $("#" + active)) $("#" + active).focus();
}
panel.addEventListener("input", (e) => {
  const f = S.edit;
  if (!f) return;
  const t = e.target;
  const key = { "f-title": "title", "f-nickname": "nickname", "f-color": "color", "f-body": "body", "f-dates": "dates" }[t.id];
  if (key) f[key] = t.value;
  if (t.id === "f-colorpick") { f.color = t.value; $("#f-color").value = t.value; }
  if (t.id === "f-body") $("#f-preview").innerHTML = MD.render(t.value);
  if (!f.dirty) { f.dirty = true; $(".dirty-note", panel).textContent = "unsaved"; }
});
panel.addEventListener("change", (e) => {
  const f = S.edit;
  if (!f) return;
  const t = e.target;
  if (t.id === "f-status") f.status = t.value;
  else if (t.id === "f-bold") f.bold = t.checked;
  else if (t.id === "f-phase") f.phase = t.value;
  else if (t.id === "f-addlink" && t.value) { f.links.push(t.value); f.dirty = true; return renderEdit(); }
  else return;
  f.dirty = true;
  $(".dirty-note", panel).textContent = "unsaved";
});
panel.addEventListener("keydown", (e) => {
  if (!S.edit) return;
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "b" && e.target.id === "f-body") { e.preventDefault(); wrapSel("**"); }
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "i" && e.target.id === "f-body") { e.preventDefault(); wrapSel("*"); }
  if ((e.ctrlKey || e.metaKey) && e.key === "Enter") { e.preventDefault(); saveEdit(); }
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") { e.preventDefault(); saveEdit(); }
});
function wrapSel(mark) {
  const ta = $("#f-body");
  if (!ta) return;
  const { selectionStart: a, selectionEnd: b, value: v } = ta;
  const inner = v.slice(a, b) || "text";
  ta.value = v.slice(0, a) + mark + inner + mark + v.slice(b);
  ta.focus();
  ta.setSelectionRange(a + mark.length, a + mark.length + inner.length);
  ta.dispatchEvent(new Event("input", { bubbles: true }));
}
async function saveEdit() {
  const f = S.edit, o = f.orig;
  const fields = {};
  if (!f.title.trim()) return toast((f.item ? "Title" : "Name") + " is required", true);
  if (f.title !== o.title) fields[f.item ? "title" : "name"] = f.title.trim();
  for (const k of ["nickname", "status", "color"]) if (f[k] !== o[k]) fields[k] = f[k];
  if (f.item && f.bold !== o.bold) fields.bold = f.bold;
  if (f.item && JSON.stringify(f.links) !== JSON.stringify(o.links)) fields.links = f.links;
  if (!f.item && !f.group && f.dates !== o.dates) fields.dates = f.dates;
  const args = { id: f.id, fields };
  if (f.body !== o.body) args.body = f.body;
  try {
    let id = f.id;
    if (Object.keys(fields).length || "body" in args) await api("set", args);
    if ((f.item || f.group) && f.phase !== o.phase) id = (await api("mv", { id, target: f.phase })).id;
    S.edit = null;
    S.sel = id;
    await load(true);
    toast(`saved ${id}`);
  } catch (e) { toast(e.message, true); }
}

// Link picking on the map
function startPick() { S.pick = true; document.body.classList.add("picking"); $("#pickhint").hidden = false; }
function endPick() { S.pick = null; document.body.classList.remove("picking"); $("#pickhint").hidden = true; }
function pickTarget(t) {
  const f = S.edit;
  endPick();
  if (!f || t === f.id) return;
  if (!f.links.includes(t)) { f.links.push(t); f.dirty = true; }
  renderEdit();
  toast(`link to ${t} added (save to keep)`);
}
$("#btn-pick-cancel").onclick = endPick;

// ------------------------------------------------------------------ add item/phase
async function addItem(phase, group) {
  if (S.edit && S.edit.dirty && !confirm("Discard unsaved changes?")) return;
  const r = await mutate("add", { phase, group: group || undefined, title: "New item", status: "planned" });
  S.sel = r.id;
  openEdit(r.id);
  zoomTo(r.id, Math.max(S.view.s, 0.7));
}
async function addGroup(parent) {
  if (S.edit && S.edit.dirty && !confirm("Discard unsaved changes?")) return;
  const args = isGroup(parent) ? { phase: S.byId[parent].phase, in: parent } : { phase: parent };
  // The name is asked first: it becomes the group's id (phaseN/g-<name>), which renames never change.
  const name = (prompt("Name of the new group (its id is made from it)", "New group") || "").trim();
  if (!name) return;
  const r = await mutate("add-group", { ...args, name });
  openEdit(r.id);
  zoomTo(r.id, Math.max(Math.min(S.view.s, 0.8), 0.5));
}
function toggleGroup(id) {
  if (S.collapsed.has(id)) S.collapsed.delete(id); else S.collapsed.add(id);
  store("wr-collapsed:" + S.data.root, [...S.collapsed]);
  build();
}
// Groups and items inside a group, all levels down.
function descGroups(gid) {
  const g = S.byId[gid], out = [];
  for (const x of (g && g.groups) || []) out.push(x, ...descGroups(x));
  return out;
}
function descItems(gid) {
  const out = [...((S.byId[gid] && S.byId[gid].items) || [])];
  for (const x of descGroups(gid)) out.push(...((S.byId[x] && S.byId[x].items) || []));
  return out;
}
// <option>s for "where does this go": every phase, its groups indented under it.
// ``skip`` (a group being edited) hides itself and its sub-groups.
function containerOptions(skip, cur) {
  const no = new Set(skip ? [skip, ...descGroups(skip)] : []);
  const out = [];
  const opt = (v, label) => out.push(`<option value="${esc(v)}"${v === cur ? " selected" : ""}>${esc(label)}</option>`);
  for (const p of S.data.phases) {
    opt(p.id, `${p.id}: ${p.name}`);
    const walk = (parent, depth) => {
      for (const g of (p.groups || []).filter((x) => x.parent === parent && !no.has(x.id))) {
        opt(g.id, `${"\u00a0\u00a0\u00a0".repeat(depth)}${g.id}: ${g.name}`);
        walk(g.id, depth + 1);
      }
    };
    walk(p.id, 1);
  }
  return out.join("");
}
async function addPhase(after) {
  if (S.edit && S.edit.dirty && !confirm("Discard unsaved changes?")) return;
  const r = await mutate("add-phase", { name: "New phase", nickname: "New", after: after || undefined });
  openEdit(r.id);
  zoomTo(r.id, Math.max(Math.min(S.view.s, 0.8), 0.5));
}

// ------------------------------------------------------------------ top bar
$("#btn-fit").onclick = fitAll;
$("#btn-zin").onclick = () => { const { w, h } = vpSize(); zoomAt(1.3, w / 2, h / 2); };
$("#btn-zout").onclick = () => { const { w, h } = vpSize(); zoomAt(1 / 1.3, w / 2, h / 2); };
$("#btn-addphase").onclick = () => addPhase(null);
$("#btn-problems").onclick = () => { S.edit = null; S.panelMode = "problems"; renderPanel(); };
$("#btn-info").onclick = () => { S.edit = null; S.panelMode = S.panelMode === "info" ? null : "info"; renderPanel(); };
$("#btn-reload").onclick = () => { S.edit = null; load(true); };
$("#btn-dismiss").onclick = () => { $("#notice").hidden = true; };

// ------------------------------------------------------------------ start
// URL hash: #zoom=far|mid|near|fit|<scale>&focus=ID&select=ID&edit=ID (shareable views, screenshots)
function applyHash(first) {
  const h = new URLSearchParams(location.hash.slice(1));
  const saved = first ? recall("wr-view:" + S.data.root) : null;
  if (h.get("select")) select(h.get("select"));
  if (h.get("edit")) openEdit(h.get("edit"));
  const z = h.get("zoom"), focus = h.get("focus");
  if (z === "fit" || (!z && !saved)) fitAll();
  else if (!z && saved) { S.view = saved; applyView(); }
  else if (z === "far") {
    fitAll();
    S.view.s = Math.min(S.view.s, FAR * 0.8);
    const { w, h: hh } = vpSize();
    S.view.x = w / 2 - (S.world.w / 2) * S.view.s;
    S.view.y = Math.max(20, hh / 2 - (S.world.h / 2) * S.view.s);
    applyView();
  } else {
    const s = z === "mid" ? 0.6 : z === "near" ? 1.25 : parseFloat(z) || 0.6;
    if (focus) zoomTo(focus, s);
    else { S.view = { s, x: 20, y: 20 }; applyView(); }
  }
}
addEventListener("hashchange", () => S.data && applyHash(false));

(async function start() {
  layer.classList.add("mid");
  try { await load(); } catch (e) { $("#empty").hidden = false; $("#empty").textContent = "Cannot load the roadmap: " + e.message; return; }
  applyHash(true);
  applyView();
  setTimeout(poll, 1000);
})();

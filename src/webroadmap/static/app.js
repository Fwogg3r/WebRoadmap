// WebRoadmap page: box map with pan/zoom, semantic zoom, side panel editor.
// All writes go through the server API (which edits the files); the page then
// reloads from disk. The map is one transformed layer: pan/zoom never relayout.
"use strict";

// ------------------------------------------------------------------ geometry
const PAD = 40, HEAD_H = 150, BOX_W = 276, BOX_H = 176;
const GHEAD = 74, GEMPTY = 44, GMIN_W = BOX_W + 36, PMIN_W = 300, PMIN_BODY = 120, ADDPH_W = 180;
// Gaps: a container's border gutter, between its columns, between its rows; between phases.
// Each gap grows to fit the link lanes routed through it (LANE apart, LANE_M margin).
const GUT = 18, GAP_C = 44, GAP_R = 34, PH_GAP = 110, LANE = 11, LANE_M = 12, END_SP = 16;
const NPAL = 10; // link colours (--e0..--e9), one per source item
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
// Every container (the roadmap, a phase, a group) lays its children out in a near-square
// grid (about sqrt(n) columns, filled row by row; a stored `width:` picks how many columns
// fit). Links are routed through the gaps between cells, like wires in channels: out of an
// item's right side, down its column gap, along the row gap below, out through the
// container's right border (a port dot), and so on up to the container both ends share;
// then down into the target through the left borders. Every link has its own lane in each
// gap it uses and gaps grow with their lanes, so borders never get crowded.
function sortKids(items, groups) {
  return [...items, ...groups].map((e, i) => ({ e, i, k: typeof e.order === "number" ? e.order : i }))
    .sort((a, b) => a.k - b.k || a.i - b.i).map((x) => x.e);
}
function makeTree(d) {
  const nodes = {}, ep = {}; // ep: id -> node a link to it starts/ends at (hidden ids -> collapsed group)
  const mk = (kind, ent, parent, hh = 0) => {
    const n = { kind, id: ent ? ent.id : "", ent, parent, children: [], hh, lanes: { c: [], r: [] } };
    if (ent) nodes[ent.id] = ep[ent.id] = n;
    return n;
  };
  const root = mk("root", null, null);
  for (const p of d.phases) {
    p.groups = p.groups || [];
    S.byId[p.id] = p;
    const pn = mk("phase", p, root, HEAD_H);
    root.children.push(pn);
    const items = {}, groups = {};
    for (const it of p.items) { items[it.id] = it; it.phase = p.id; S.byId[it.id] = it; }
    for (const g of p.groups) { groups[g.id] = g; g.phase = p.id; S.byId[g.id] = g; }
    const hide = (g, n) => {
      for (const i of g.items) { ep[i] = n; S.hidden.add(i); }
      for (const x of g.groups) if (groups[x]) { ep[x] = n; hide(groups[x], n); }
    };
    const fill = (n, its, gs) => {
      for (const e of sortKids(its, gs)) {
        if (!isGroup(e.id)) { n.children.push(mk("item", e, n)); continue; }
        const gn = mk("group", e, n, GHEAD);
        n.children.push(gn);
        if (S.collapsed.has(e.id)) { gn.coll = true; hide(e, gn); }
        else fill(gn, e.items.map((i) => items[i]).filter(Boolean), e.groups.map((x) => groups[x]).filter(Boolean));
      }
    };
    fill(pn, p.items.filter((i) => !i.group || !groups[i.group]), p.groups.filter((g) => g.parent === p.id || !groups[g.parent]));
  }
  return { root, nodes, ep };
}
// Size the user asked for (phase/group `width:`/`height:`, or the live resize drag).
function wantSize(n) {
  const r = S.resize;
  if (r && r.id === n.id) return { w: r.w, h: r.h };
  const num = (v) => (typeof v === "number" && v > 0 ? v : 0);
  return { w: num(n.ent && n.ent.width), h: num(n.ent && n.ent.height) };
}
const minW = (n) => (n.kind === "group" ? GMIN_W : n.kind === "phase" ? PMIN_W : 0);
function baseW(n, cols) {
  const cw = new Array(cols).fill(0);
  n.children.forEach((c, i) => { cw[i % cols] = Math.max(cw[i % cols], c.bw); });
  return cw.reduce((a, b) => a + b, 0) + 2 * GUT + (cols - 1) * GAP_C;
}
// Pass 1 (bottom-up): grid shape of every container, from base gap sizes.
function shape(n) {
  if (n.kind === "item") { n.bw = BOX_W; return; }
  if (n.coll) { n.bw = Math.max(GMIN_W, wantSize(n).w); n.cols = n.rows = 0; return; }
  n.children.forEach(shape);
  const k = n.children.length;
  let cols = n.kind === "root" ? k : Math.ceil(Math.sqrt(k));
  const want = wantSize(n).w;
  if (n.kind !== "root" && want && k) {
    cols = 1;
    for (let c = k; c > 1; c--) if (baseW(n, c) <= want) { cols = c; break; }
  }
  n.cols = k ? cols : 0;
  n.rows = k ? Math.ceil(k / cols) : 0;
  n.children.forEach((c, i) => { c.r = Math.floor(i / cols); c.c = i % cols; });
  n.bw = Math.max(minW(n), k ? baseW(n, cols) : 0, want);
}
// Pass 2: routes as channel references (lanes are numbered in registration order).
function lane(n, axis, j) {
  const k = n.lanes[axis][j] || 0;
  n.lanes[axis][j] = k + 1;
  return { n, axis, j, k };
}
function chainTo(n) { const out = []; for (let x = n; x; x = x.parent) out.unshift(x); return out; }
function route(A, B) {
  const ca = chainTo(A), cb = chainTo(B);
  let i = 0;
  while (i < ca.length && i < cb.length && ca[i] === cb[i]) i++;
  if (i === ca.length || i === cb.length) return null; // one end contains the other
  const L = ca[i - 1], sA = ca[i], sB = cb[i], ops = [];
  // Leaving a cell: its own lane right next to it when the column is wider than the cell.
  const out = (P, cell) => {
    const ref = lane(P, "c", cell.c + 1);
    ref.cell = cell; ref.ck = cell.exits = (cell.exits || 0) + 1;
    return ref;
  };
  for (let j = ca.length - 1; j > i; j--) {
    const cell = ca[j], P = ca[j - 1];
    ops.push({ x: out(P, cell) }, { y: lane(P, "r", cell.r + 1) }, { x: { right: P }, port: true });
  }
  ops.push({ x: out(L, sA) });
  if (sB.c !== sA.c + 1) {
    const ref = lane(L, "r", sA.r + 1);
    // At the top level: just under the phases it passes (forward: those between; back: the ends too).
    if (L.kind === "root") ref.span = sB.c > sA.c ? [sA.c + 1, sB.c - 1] : [sB.c, sA.c];
    ops.push({ y: ref }, { x: lane(L, "c", sB.c) });
  }
  for (let j = i; j < cb.length - 1; j++) {
    const P = cb[j], cell = cb[j + 1];
    ops.push({ y: lane(P, "r", cell.r) }, { x: { left: P }, port: true }, { x: lane(P, "c", cell.c) });
  }
  ops.push({ y: { end: B } }, { x: { left: B } });
  return ops;
}
// Pass 3 (bottom-up): real sizes, every gap wide enough for its lanes.
const sum = (a) => a.reduce((x, y) => x + y, 0);
function gapSize(n, axis, j, last) {
  const edge = j === 0 || j === last;
  const base = n.kind === "root" ? (edge || axis === "r" ? PAD : PH_GAP) : edge ? GUT : axis === "c" ? GAP_C : GAP_R;
  const cnt = n.lanes[axis][j] || 0;
  return Math.max(base, cnt ? 2 * LANE_M + cnt * LANE : 0);
}
function size(n) {
  if (n.kind === "item") { n.w = BOX_W; n.h = BOX_H; return; }
  if (n.coll) { n.w = Math.max(GMIN_W, wantSize(n).w); n.h = GHEAD; return; }
  n.children.forEach(size);
  const k = n.children.length;
  n.colW = new Array(n.cols).fill(0);
  n.rowH = new Array(n.rows).fill(0);
  for (const c of n.children) { n.colW[c.c] = Math.max(n.colW[c.c], c.w); n.rowH[c.r] = Math.max(n.rowH[c.r], c.h); }
  n.cgB = k ? [...Array(n.cols + 1)].map((_, j) => gapSize(n, "c", j, n.cols)) : [];
  n.rgB = k ? [...Array(n.rows + 1)].map((_, j) => gapSize(n, "r", j, n.rows)) : [];
  const w = k ? sum(n.colW) + sum(n.cgB) : 0;
  const h = n.hh + (k ? sum(n.rowH) + sum(n.rgB) : n.kind === "phase" ? PMIN_BODY : GEMPTY + GUT);
  n.cw = Math.max(minW(n), w);
  n.ch = h;
  const want = wantSize(n);
  n.w = Math.max(n.cw, want.w);
  n.h = Math.max(h, want.h);
}
// Pass 4 (top-down): positions; spare width/height goes to the right/bottom gutters.
function place(n, x, y) {
  n.x = x; n.y = y;
  if (n.kind === "item" || n.coll || !n.children.length) return;
  n.cgX = []; n.colX = []; n.rgY = []; n.rowY = [];
  let cx = x;
  for (let j = 0; j < n.cols; j++) { n.cgX[j] = cx; cx += n.cgB[j]; n.colX[j] = cx; cx += n.colW[j]; }
  n.cgX[n.cols] = cx;
  let cy = y + n.hh;
  for (let j = 0; j < n.rows; j++) { n.rgY[j] = cy; cy += n.rgB[j]; n.rowY[j] = cy; cy += n.rowH[j]; }
  n.rgY[n.rows] = cy;
  for (const c of n.children) place(c, n.colX[c.c], n.rowY[c.r]);
}
function laneAt(ref) {
  const { n, axis, j, k } = ref;
  if (ref.cell) {
    const c = ref.cell, free = n.colX[c.c] + n.colW[c.c] - (c.x + c.w);
    if (free >= 2 * LANE_M + c.exits * LANE) return c.x + c.w + LANE_M + (ref.ck - 0.5) * LANE;
  }
  if (ref.span) {
    const ph = n.children.slice(ref.span[0], ref.span[1] + 1);
    return Math.max(...ph.map((p) => p.y + p.h)) + LANE_M + (k + 0.5) * LANE;
  }
  const cnt = n.lanes[axis][j];
  const start = axis === "c" ? n.cgX[j] : n.rgY[j], gap = axis === "c" ? n.cgB[j] : n.rgB[j];
  return start + (gap - cnt * LANE) / 2 + (k + 0.5) * LANE;
}
// Where the k-th of cnt links leaves/enters a box: spread around the middle (items) or the header band.
function endY(nd, k, cnt) {
  const band = nd.kind === "item" ? nd.h : nd.hh || nd.h;
  const sp = Math.min(END_SP, (band - 16) / Math.max(1, cnt));
  return nd.y + band / 2 + (k - (cnt - 1) / 2) * sp;
}
// Orthogonal polyline -> path with rounded corners.
function roundedPath(pts, r = 12) {
  const p = [];
  for (const q of pts) {
    const l = p[p.length - 1];
    if (l && Math.abs(l[0] - q[0]) < 0.5 && Math.abs(l[1] - q[1]) < 0.5) continue;
    if (p.length >= 2) {
      const a = p[p.length - 2];
      if ((Math.abs(a[0] - l[0]) < 0.5 && Math.abs(l[0] - q[0]) < 0.5) || (Math.abs(a[1] - l[1]) < 0.5 && Math.abs(l[1] - q[1]) < 0.5)) p.pop();
    }
    p.push(q);
  }
  const f = (v) => v.toFixed(1);
  let d = `M${f(p[0][0])},${f(p[0][1])}`;
  for (let i = 1; i < p.length - 1; i++) {
    const [a, b, c] = [p[i - 1], p[i], p[i + 1]];
    const l1 = Math.hypot(b[0] - a[0], b[1] - a[1]), l2 = Math.hypot(c[0] - b[0], c[1] - b[1]);
    const rr = Math.min(r, l1 / 2, l2 / 2);
    const p1 = [b[0] + ((a[0] - b[0]) / l1) * rr, b[1] + ((a[1] - b[1]) / l1) * rr];
    const p2 = [b[0] + ((c[0] - b[0]) / l2) * rr, b[1] + ((c[1] - b[1]) / l2) * rr];
    d += `L${f(p1[0])},${f(p1[1])}Q${f(b[0])},${f(b[1])} ${f(p2[0])},${f(p2[1])}`;
  }
  const z = p[p.length - 1];
  return d + `L${f(z[0])},${f(z[1])}`;
}
const progress = (items) => {
  const total = items.filter((i) => i.status !== "dropped").length;
  return { total, done: items.filter((i) => i.status === "done").length };
};

function build() {
  const d = S.data;
  S.rects = {}; S.byId = {}; S.out = {}; S.inn = {}; S.hidden = new Set();
  const { root, nodes, ep } = makeTree(d);
  S.tree = root; S.nodes = nodes;

  // Links: outgoing per item (hidden ones start at their collapsed group).
  const bad = new Set((d.check?.errors || []).filter((e) => e.link).map((e) => e.id));
  const edges = [];
  let src = 0;
  for (const p of d.phases) for (const it of p.items) {
    S.out[it.id] = it.links || [];
    for (const t of S.out[it.id]) (S.inn[t] = S.inn[t] || []).push(it.id);
    if (S.out[it.id].length) {
      const col = src++ % NPAL;
      for (const t of S.out[it.id]) edges.push({ from: it.id, to: t, col, A: ep[it.id], B: ep[t] });
    }
  }
  shape(root);
  const outN = new Map(), inN = new Map();
  for (const e of edges) {
    if (!e.A || !e.B || e.A === e.B) continue;
    e.ops = route(e.A, e.B);
    e.ko = outN.get(e.A) || 0; outN.set(e.A, e.ko + 1);
    e.ki = inN.get(e.B) || 0; inN.set(e.B, e.ki + 1);
  }
  size(root);
  place(root, 0, 0);

  const html = [], ghtml = [], ihtml = [];
  const rsz = (n) => `<div class="rsz lv lv-mn" data-rsz="${n.id}" title="Drag to resize (double-click: automatic size)"></div>`;
  const emit = (n) => {
    if (n.kind === "item") {
      const it = n.ent;
      S.rects[it.id] = { x: n.x, y: n.y, w: n.w, h: n.h };
      const chips = `<span class="chip" style="--c:${statusColor(it.status)}">${esc(it.status)}</span><span class="idl">${it.id}</span><span class="badge-bad" hidden>!</span>`;
      ihtml.push(`<div class="item st-${esc(it.status)}${it.bold ? " bold" : ""}" data-id="${it.id}" style="left:${n.x}px;top:${n.y}px;width:${n.w}px;height:${n.h}px;--c:${colorOf(it)}">
        <div class="lv lv-mid"><div class="t">${esc(it.title)}</div><div class="chips">${chips}</div></div>
        <div class="lv lv-near"><div class="t">${esc(it.title)}</div><div class="chips">${chips}${it.nickname ? `<span class="idl">${esc(it.nickname)}</span>` : ""}</div>
          <div class="notes">${MD.render(it.body)}</div></div>
      </div>`);
      return;
    }
    if (n.kind === "group") {
      const g = n.ent;
      S.rects[g.id] = { x: n.x, y: n.y, w: n.w, h: GHEAD, box: { x: n.x, y: n.y, w: n.w, h: n.h } };
      const all = descItems(g.id).map((i) => S.byId[i]).filter(Boolean);
      const { done, total } = progress(all);
      const leaf = !g.groups.length;
      ghtml.push(`<div class="grp st-${esc(g.status)}${n.coll ? " coll" : ""}${leaf ? " leaf" : ""}${g.error ? " bad" : ""}" data-id="${g.id}" style="left:${n.x}px;top:${n.y}px;width:${n.w}px;height:${n.h}px;--c:${colorOf(g)}">
        <div class="ghead lv lv-mn">
          <button class="gtog" data-tog="${g.id}" title="${n.coll ? "Expand" : "Collapse"}">${n.coll ? "&#9656;" : "&#9662;"}</button>
          <div class="gtxt"><div class="gname">${esc(g.name)}</div>
            <div class="grow"><span class="chip" style="--c:${statusColor(g.status)}">${esc(g.status)}</span><span class="count">${done}/${total}</span><span class="idl">${g.id}</span></div></div>
          <button class="gadd" data-add="${g.phase}" data-group="${g.id}" title="Add an item to this group">+</button>
        </div>
        ${!n.coll && !n.children.length ? `<div class="gempty lv lv-mn">empty group</div>` : ""}
        <div class="gfar lv lv-far"><div class="nick">${esc(g.nickname || g.name)}</div>${leaf || n.coll ? `<div class="meta">${done}/${total}</div>` : ""}</div>
        ${n.coll ? "" : rsz(n)}
      </div>`);
    }
    if (n.kind === "phase") {
      const p = n.ent;
      const { done, total } = progress(p.items);
      const pct = total ? Math.round((100 * done) / total) : p.status === "done" ? 100 : 0;
      const pc = colorOf(p);
      const grouped = p.groups.length > 0 || n.cols > 1;
      S.rects[p.id] = { x: n.x, y: n.y, w: n.w, h: HEAD_H, col: { x: n.x, y: n.y, w: n.w, h: n.h } };
      html.push(`<div class="col st-${esc(p.status)}${grouped ? " grouped" : ""}" data-phase="${p.id}" style="left:${n.x}px;top:${n.y}px;width:${n.w}px;height:${n.h}px;--c:${pc}">
        <div class="farc lv lv-far">
          <div class="nick">${esc(p.nickname || p.name)}</div>
          ${p.nickname ? `<div class="name">${esc(p.name)}</div>` : ""}
          <div class="prog"><i style="width:${pct}%"></i></div>
          <div class="meta">${done}/${total} done &middot; ${esc(p.status)}</div>
        </div>${rsz(n)}</div>`);
      html.push(`<div class="phead lv st-${esc(p.status)}${p.error ? " bad" : ""}" data-id="${p.id}" style="left:${n.x}px;top:${n.y}px;width:${n.w}px;height:${HEAD_H}px;--c:${pc}">
        <div class="pid">${p.id}${p.dates ? " &middot; " + esc(String(p.dates)) : ""}</div>
        <div class="pname">${esc(p.name)}</div>
        <div class="pnick">${esc(p.nickname || "")}</div>
        <div class="row"><span class="chip" style="--c:${pc}">${esc(p.status)}</span><div class="prog"><i style="width:${pct}%"></i></div><span class="count">${done}/${total}</span></div>
        <button class="padd" data-add="${p.id}" title="Add an item to ${p.id}">+</button>
      </div>`);
    }
    n.children.forEach(emit);
  };
  root.children.forEach(emit);
  for (const id of S.hidden) { const n = ep[id]; if (n) S.rects[id] = S.rects[n.id]; }
  const ax = root.w;
  html.push(`<div class="addphase lv" data-addphase="1" title="Add a phase" style="left:${ax}px;top:${PAD}px;width:${ADDPH_W}px;height:${HEAD_H}px">+ Phase</div>`);
  S.world = { w: ax + ADDPH_W + PAD, h: root.h };
  nodesEl.innerHTML = html.join("") + ghtml.join("") + ihtml.join("");
  $("#empty").hidden = d.phases.length > 0;
  $("#empty").textContent = "No phases yet: click “+ Phase”.";

  // Edges: rounded orthogonal paths in the source item's colour, a dot where they cross a border.
  const paths = [], dots = [];
  const phaseIx = Object.fromEntries(d.phases.map((p, i) => [p.id, i]));
  for (const e of edges) {
    const da = `data-from="${e.from}" data-to="${esc(e.to)}"`, sty = `style="--ec:var(--e${e.col})"`;
    const a = S.rects[e.from];
    if (!e.B) {
      if (!a) continue;
      const x1 = a.x + a.w, y1 = a.y + a.h / 2;
      paths.push(`<path class="bad" ${da} d="M${x1},${y1} C${x1 + 40},${y1} ${x1 + 40},${y1 - 40} ${x1 + 70},${y1 - 40}" marker-end="url(#arr-bad)"/>`);
      continue;
    }
    if (e.A === e.B) continue; // both ends inside the same collapsed group
    const cls = [];
    if (!isItem(e.to)) cls.push("tophase");
    if (phaseIx[e.to.split("/")[0]] < phaseIx[e.from.split("/")[0]]) cls.push("back");
    const sy = endY(e.A, e.ko, outN.get(e.A)), ty = endY(e.B, e.ki, inN.get(e.B));
    let dpath;
    if (e.ops) {
      let cx = e.A.x + e.A.w, cy = sy;
      const pts = [[cx, cy]];
      for (const op of e.ops) {
        if ("x" in op) {
          cx = op.x.right ? op.x.right.x + op.x.right.w : op.x.left ? op.x.left.x : laneAt(op.x);
          if (op.port) dots.push(`<circle class="port" ${da} ${sty} cx="${cx.toFixed(1)}" cy="${cy.toFixed(1)}" r="5"/>`);
        } else cy = op.y.end ? ty : laneAt(op.y);
        pts.push([cx, cy]);
      }
      dpath = roundedPath(pts);
    } else {
      // One end contains the other (an item linking to its own group or phase): a small loop.
      const x1 = e.A.x + e.A.w, x2 = e.B.x;
      dpath = `M${x1},${sy} C${x1 + 60},${sy} ${x2 - 60},${ty} ${x2},${ty}`;
    }
    paths.push(`<path class="${cls.join(" ")}" ${da} ${sty} d="${dpath}" marker-end="url(#arr${e.col})"/>`);
  }
  edgesEl.setAttribute("width", S.world.w);
  edgesEl.setAttribute("height", S.world.h);
  const marker = (id, fill) => `<marker id="${id}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="13" markerHeight="13" markerUnits="userSpaceOnUse" orient="auto"><path d="M0,0L10,5L0,10z" style="fill:${fill}"/></marker>`;
  edgesEl.innerHTML = `<defs>${[...Array(NPAL)].map((_, k) => marker("arr" + k, `var(--e${k})`)).join("")}
    <marker id="arr-bad" viewBox="0 0 10 10" refX="5" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M1,1L9,9M9,1L1,9" style="stroke:var(--bad);stroke-width:2.5"/></marker>
    </defs>` + paths.join("") + dots.join("");
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

// Pointer input: left/middle drag on empty space pans; a left drag (mouse or pen) on an
// item, a group header or a phase header moves it, and its corner handle resizes a phase
// or group; a click without movement selects; two touches pinch-zoom; wheel zooms.
const ptrs = new Map();
let drag = null;
const dnd = document.createElement("div");
dnd.id = "dnd";
layer.appendChild(dnd);
function toWorld(cx, cy) {
  const r = vp.getBoundingClientRect();
  return { x: (cx - r.left - S.view.x) / S.view.s, y: (cy - r.top - S.view.y) / S.view.s };
}
// What a press on `t` would drag: a corner handle (resize) or a box (move).
function grabOf(t) {
  if (S.pick || !t.closest || S.level === "far") return null;
  const rz = t.closest("[data-rsz]");
  if (rz) return { kind: "resize", id: rz.dataset.rsz };
  if (t.closest("button")) return null;
  const el = t.closest(".item,.ghead,.phead");
  const id = el && (el.dataset.id || el.parentElement.dataset.id);
  return id && S.nodes[id] ? { kind: "move", id } : null;
}
vp.addEventListener("pointerdown", (e) => {
  if (e.pointerType === "mouse" && e.button !== 0 && e.button !== 1) return;
  if (e.button === 1) e.preventDefault();
  vp.focus({ preventScroll: true });
  ptrs.set(e.pointerId, { x: e.clientX, y: e.clientY });
  if (ptrs.size === 1) {
    const grab = e.button === 0 && e.pointerType !== "touch" ? grabOf(e.target) : null;
    drag = { x0: e.clientX, y0: e.clientY, moved: false, target: e.target, button: e.button, grab };
  } else if (ptrs.size === 2) {
    if (drag && drag.grab && drag.moved) endGrab(drag, false);
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
    if (drag.grab) startGrab(drag, e);
    if (!drag.grab) vp.classList.add("panning");
  }
  if (!drag.moved) return;
  if (drag.grab) moveGrab(drag, e);
  else { S.view.x += dx; S.view.y += dy; applyView(); }
});
function endPtr(e) {
  if (!ptrs.has(e.pointerId)) return;
  ptrs.delete(e.pointerId);
  if (ptrs.size === 0) {
    vp.classList.remove("panning");
    if (drag && drag.grab && drag.moved) endGrab(drag, e.type === "pointerup");
    else if (drag && !drag.moved && !drag.pinch && drag.button === 0 && e.type === "pointerup") clickAt(drag.target, e);
    drag = null;
  }
}
vp.addEventListener("pointerup", endPtr);
vp.addEventListener("pointercancel", endPtr);
vp.addEventListener("mousedown", (e) => { if (e.button === 1) e.preventDefault(); }); // no autoscroll
vp.addEventListener("dblclick", (e) => {
  const rz = e.target.closest("[data-rsz]");
  if (rz) {
    const id = rz.dataset.rsz, ent = S.byId[id];
    if (ent && (ent.width || ent.height)) mutate("set", { id, fields: { width: "", height: "" } }, `${id}: automatic size`);
    return;
  }
  if (e.target.closest(".gtog,.gadd,.padd")) return;
  const it = e.target.closest(".item,.phead,.ghead");
  const id = it && (it.dataset.id || it.parentElement.dataset.id);
  if (id) zoomTo(id, isItem(id) ? 1.6 : isGroup(id) ? 0.9 : 0.7);
});

// ------------------------------------------------------------------ drag and drop, resize
// Moving an item or group drops it into the phase or group under the pointer at a grid
// position (saved with `roadmap arrange`: folder move + `order:`); a phase header drops
// between phases (`roadmap mv`). Resizing reflows live and saves `width:`/`height:`.
function startGrab(g, e) {
  if (S.edit && S.edit.dirty) { toast("Save or cancel the open edit first", true); g.grab = null; return; }
  const n = S.nodes[g.grab.id];
  if (!n) { g.grab = null; return; }
  if (g.grab.kind === "resize") {
    S.resize = { id: n.id, w: Math.round(n.w), h: Math.round(n.h) };
    vp.classList.add("resizing");
    return;
  }
  g.p0 = toWorld(e.clientX, e.clientY);
  dnd.innerHTML = `<div class="ghost" style="left:${n.x}px;top:${n.y}px;width:${n.w}px;height:${n.h}px;--c:${colorOf(n.ent)}"><span>${esc(nameOf(n.ent))}</span></div><div class="dropbar" hidden></div>`;
  layer.classList.add("dragging");
  const ids = [n.id, ...(n.kind === "group" ? [...descItems(n.id), ...descGroups(n.id)] : []), ...(n.kind === "phase" ? n.ent.items.map((i) => i.id).concat(n.ent.groups.map((x) => x.id)) : [])];
  for (const id of ids) nodesEl.querySelectorAll(`[data-id="${CSS.escape(id)}"],[data-phase="${CSS.escape(id)}"]`).forEach((el) => el.classList.add("lifted"));
}
function autoPan(e) {
  const r = vp.getBoundingClientRect(), m = 36, st = 14;
  let dx = 0, dy = 0;
  if (e.clientX < r.left + m) dx = st; else if (e.clientX > r.right - m) dx = -st;
  if (e.clientY < r.top + m) dy = st; else if (e.clientY > r.bottom - m) dy = -st;
  if (dx || dy) { S.view.x += dx; S.view.y += dy; applyView(); }
}
function moveGrab(g, e) {
  autoPan(e);
  const w = toWorld(e.clientX, e.clientY);
  if (g.grab.kind === "resize") {
    const n = S.nodes[g.grab.id];
    if (!n) return;
    S.resize = { id: n.id, w: Math.max(minW(n), Math.round(w.x - n.x)), h: Math.max(n.hh + 60, Math.round(w.y - n.y)) };
    if (!g.raf) g.raf = requestAnimationFrame(() => { g.raf = 0; if (S.resize) build(); });
    return;
  }
  const ghost = dnd.firstChild, bar = dnd.lastChild;
  ghost.style.transform = `translate(${w.x - g.p0.x}px,${w.y - g.p0.y}px)`;
  g.drop = dropAt(S.nodes[g.grab.id], w);
  nodesEl.querySelectorAll(".droptarget").forEach((el) => el.classList.remove("droptarget"));
  if (!g.drop || g.drop.same) { bar.hidden = true; return; }
  const b = g.drop.bar;
  Object.assign(bar.style, { left: b.x + "px", top: b.y + "px", width: b.w + "px", height: b.h + "px" });
  bar.hidden = false;
  if (g.drop.C) nodesEl.querySelector(g.drop.C.kind === "phase" ? `.col[data-phase="${g.drop.C.id}"]` : `.grp[data-id="${CSS.escape(g.drop.C.id)}"]`)?.classList.add("droptarget");
}
function dropAt(n, w) {
  if (!n) return null;
  if (n.kind === "phase") {
    const all = S.tree.children, others = all.filter((p) => p !== n);
    const pos = others.filter((p) => w.x > p.x + p.w / 2).length;
    const last = others[others.length - 1];
    const x = pos < others.length ? others[pos].x - 16 : last ? last.x + last.w + 10 : n.x;
    return { phasePos: pos, same: pos === all.indexOf(n), bar: { x, y: n.y, w: 6, h: n.h } };
  }
  // The deepest expanded phase or group under the pointer (never the dragged group itself).
  let C = null;
  const walk = (c) => {
    for (const ch of c.children) {
      if (ch.kind === "item" || ch.coll || ch === n) continue;
      if (w.x >= ch.x && w.x <= ch.x + ch.w && w.y >= ch.y && w.y <= ch.y + ch.h) { C = ch; walk(ch); return; }
    }
  };
  walk(S.tree);
  if (!C) return null;
  const kids = C.children.filter((k) => k !== n);
  let idx = 0;
  for (const k of kids) {
    const top = C.rowY[k.r], bot = top + C.rowH[k.r];
    if (w.y > bot + C.rgB[k.r + 1] / 2 || (w.y >= top - C.rgB[k.r] / 2 && w.x > k.x + k.w / 2)) idx++;
  }
  let bar;
  if (idx < kids.length) { const k = kids[idx]; bar = { x: k.x - 12, y: k.y, w: 6, h: k.h }; }
  else if (kids.length) { const k = kids[kids.length - 1]; bar = { x: k.x + k.w + 6, y: k.y, w: 6, h: k.h }; }
  else bar = { x: C.x + GUT, y: C.y + C.hh + 8, w: Math.max(40, C.w - 2 * GUT), h: 6 };
  return { C, idx, kids, bar, same: C === n.parent && idx === C.children.indexOf(n) };
}
async function endGrab(g, commit) {
  layer.classList.remove("dragging");
  vp.classList.remove("resizing");
  dnd.innerHTML = '<div class="ghost" hidden></div><div class="dropbar" hidden></div>';
  nodesEl.querySelectorAll(".lifted,.droptarget").forEach((el) => el.classList.remove("lifted", "droptarget"));
  const id = g.grab.id;
  if (g.grab.kind === "resize") {
    if (g.raf) cancelAnimationFrame(g.raf);
    const r = S.resize;
    if (!commit || !r) { S.resize = null; build(); return; }
    try { await api("set", { id, fields: { width: r.w, height: r.h } }); toast(`${id}: ${r.w} × ${r.h} (double-click the corner for automatic size)`); }
    catch (err) { toast(err.message, true); }
    S.resize = null;
    await load(true);
    return;
  }
  const dr = g.drop;
  g.drop = null;
  if (!commit || !dr || dr.same) return;
  try {
    let nid = id;
    if (dr.phasePos !== undefined) {
      nid = (await mutate("mv", { id, target: String(dr.phasePos + 1) }, `moved ${id} to position ${dr.phasePos + 1}`)).id;
    } else {
      const ids = dr.kids.map((k) => k.id);
      ids.splice(dr.idx, 0, id);
      const r = await mutate("arrange", { container: dr.C.id, children: ids });
      nid = r.ids[id] || id;
      toast(nid === id ? `moved ${id} into ${dr.C.id}` : `moved ${id} to ${nid} (links updated)`);
    }
    if (S.sel === id && nid !== id) select(nid);
  } catch (_) { /* mutate showed the error */ }
}
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
      if (drag && drag.grab && drag.moved) { endGrab(drag, false); drag = null; ptrs.clear(); return; }
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
  edgesEl.querySelectorAll(".on").forEach((e) => e.classList.remove("on"));
  if (!id || !S.byId[id] || (!isItem(id) && !(S.inn[id] || []).length)) { layer.classList.remove("chain"); return; }
  const on = chainOf(id);
  layer.classList.add("chain");
  for (const n of on) nodesEl.querySelectorAll(`[data-id="${CSS.escape(n)}"]`).forEach((e) => e.classList.add("on"));
  edgesEl.querySelectorAll("[data-from]").forEach((p) => {
    if (on.has(p.dataset.from) && on.has(p.dataset.to)) p.classList.add("on");
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
  const el = target.closest(".gtog,.gadd,.padd,.item,.phead,.addphase,.grp,.col");
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
  const extra = Object.entries(e).filter(([k]) => !["id", "slug", "title", "name", "nickname", "status", "color", "bold", "links", "body", "summary", "items", "num", "phase", "error", "group", "groups", "parent", "status_from_items", "width", "height"].includes(k));
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
    ${!item && (e.width || e.height) ? `<h4>Size</h4><div>${e.width ? `${e.width} wide` : "automatic width"}, ${e.height ? `${e.height} tall` : "automatic height"} <button data-act="autosize">Automatic size</button></div>` : ""}
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
    <h4>Controls</h4><div class="md">${MD.render(`- **Pan:** drag empty space (left or middle button), arrows, one finger
- **Zoom:** wheel / pinch / \`+\` \`-\`; \`0\` fits all; double-click zooms to a box
- **Shift+wheel:** scroll sideways
- **Click** a box to open it; **Edit** to change it; \`/\` searches (\`status:active\`)
- **Move:** drag an item or a group header into any phase or group (it drops where the blue bar shows); drag a phase header between phases. Saved to the files (\`roadmap arrange\` / \`mv\`); Esc cancels
- **Resize:** drag the corner of a phase or group (its column count follows the width); double-click the corner for automatic size
- **Links:** each item's outgoing links have their own colour; a dot marks where a link crosses a phase or group border
- **Groups:** the arrow in a group's header collapses it; **+** adds an item to it
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
    case "autosize": mutate("set", { id, fields: { width: "", height: "" } }, `${id}: automatic size`); break;
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

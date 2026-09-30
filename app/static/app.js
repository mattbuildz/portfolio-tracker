"use strict";

/* The screen is prototype 0004, wired to real data: the donut, the drift
   digest, the stacked-pair table and the rebalancing engine are its code,
   with the dummy array replaced by whatever the server last read off disk
   and last fetched from the price source.

   Two things the prototype never had to handle, and this does:
   a position with no price at all, and an edit that has to reach the file. */

const $ = (id) => document.getElementById(id);
const MINUS = "−";
const today = () => new Date().toLocaleDateString("en-CA");   // YYYY-MM-DD, local
const SLICE = ["#3699FF","#1BC5BD","#8950FC","#FFA800","#F64E60","#00AFF5",
               "#6993FF","#0BB783","#B69AFF","#FFD07A","#FF8A9E","#74D9EE"];
const reducedMotion =
  typeof window.matchMedia === "function" &&
  window.matchMedia("(prefers-reduced-motion: reduce)").matches;

let SERVER = null;        // last /api/state payload
let POSITIONS = [];       // that payload in the prototype's shape
let saveTimer = null;
let editingInput = null;
let autoBalance = true;
let previewExpanded = false;
/* Allocation view only — does not change value, profit, holdings or rebalance.
   Equities: weights among companies. With bonds: % of total wealth, bond on the donut. */
let allocWithBonds = localStorage.getItem("allocWithBonds") === "1";

/* ---------- formatting (prototype) ---------- */
function money(n, signed) {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  const abs = Math.abs(n).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const sign = n < 0 ? MINUS : (signed ? "+" : "");
  return sign + "$" + abs;
}
function moneyShort(n) { return "$" + Math.round(n || 0).toLocaleString("en-US"); }
function pct(n, dp) {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  if (Math.abs(n) < 0.005) n = 0;
  return (n < 0 ? MINUS : "+") + Math.abs(n).toFixed(dp === undefined ? 2 : dp) + "%";
}
function shares(n) {
  return Number(n || 0).toLocaleString("en-US", { minimumFractionDigits: 4, maximumFractionDigits: 4 });
}

/* A close is a date, not a moment: "just now" against yesterday's closing
   price is the exact lie the timestamp exists to prevent. */
function stampText(row) {
  if (!row.as_of) return "";
  if (row.as_of_kind === "close") {
    const d = new Date(row.as_of + "T00:00:00");
    return "close " + d.toLocaleDateString([], { day: "2-digit", month: "short" });
  }
  if (row.as_of_kind === "accrual") {
    const d = new Date(row.as_of + "T00:00:00");
    return "rate " + d.toLocaleDateString([], { day: "2-digit", month: "short" });
  }
  return whenText(row.as_of);
}
function whenText(iso) {
  if (!iso) return "never";
  const d = new Date(iso);
  const mins = Math.round((Date.now() - d.getTime()) / 60000);
  const clock = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  if (mins < 1) return "just now, " + clock;
  if (mins < 60) return mins + " min ago, " + clock;
  return d.toLocaleString([], { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
}

/* ---------- server ---------- */
async function api(path, body) {
  const res = await fetch(path, body === undefined
    ? {}
    : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const payload = await res.json();
  if (!res.ok) throw new Error(payload.error || res.statusText);
  return payload;
}

function adopt(payload, opts) {
  SERVER = payload;
  const held = Object.fromEntries(POSITIONS.map(p => [p.t, p]));
  POSITIONS = payload.holdings.map((h, i) => ({
    t: h.ticker,
    name: h.name || h.ticker,
    s: h.shares,
    c: h.avg_price,
    target: h.target,
    parked: Boolean(h.parked),
    price: h.price,
    as_of: h.as_of,
    as_of_kind: h.as_of_kind,
    source: h.source,
    col: SLICE[i % SLICE.length],
    lock: held[h.ticker] ? held[h.ticker].lock : false,
  }));
  render(opts);
}

/* Prices are the server's to know; the typed columns are the page's while a
   caret is in them. Used after a save so a keystroke never loses its field. */
function adoptPricesOnly(payload) {
  SERVER = payload;
  const byT = Object.fromEntries(payload.holdings.map(h => [h.ticker, h]));
  for (const p of POSITIONS) {
    const h = byT[p.t];
    if (!h) continue;
    p.price = h.price; p.as_of = h.as_of; p.as_of_kind = h.as_of_kind; p.source = h.source;
  }
  render({ keepInputs: true });
}

/* ---------- sortable tables ----------
   One click sorts a column the way you would want it first — biggest number or
   A first — a second click reverses it, and a third puts the table back to the
   order it arranged itself in. That third click matters: the Overview table's
   own order is by drift, which is the thing it exists to show, and a sort you
   cannot undo would cost you it. */
const SORT = {};                       // table key → {key, dir}, or nothing

function sortRows(tableKey, rows, columns) {
  const spec = SORT[tableKey];
  const col = spec && columns[spec.key];
  if (!col) return rows;
  const dir = spec.dir === "asc" ? 1 : -1;
  return rows.slice().sort((a, b) => {
    const x = col.get(a), y = col.get(b);
    /* A missing number is not a small one. An unpriced row has no value and no
       profit to compare, so it sits at the bottom whichever way the column
       points, rather than pretending to be worth zero. */
    const xm = x === null || x === undefined || (typeof x === "number" && !Number.isFinite(x));
    const ym = y === null || y === undefined || (typeof y === "number" && !Number.isFinite(y));
    if (xm || ym) return xm && ym ? 0 : (xm ? 1 : -1);
    if (col.text) return String(x).localeCompare(String(y)) * dir;
    return (x - y) * dir;
  });
}

function wireSorting(tableKey, columns, onChange) {
  for (const th of document.querySelectorAll('th[data-sort="' + tableKey + '"]')) {
    const key = th.dataset.key;
    const label = th.textContent.trim();
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "sort-btn";
    btn.innerHTML = "<span></span><i class=\"sort-arrow\"></i>";
    btn.firstChild.textContent = label;
    btn.addEventListener("click", () => {
      const first = columns[key] && columns[key].text ? "asc" : "desc";
      const cur = SORT[tableKey];
      if (!cur || cur.key !== key) SORT[tableKey] = { key, dir: first };
      else if (cur.dir === first) SORT[tableKey] = { key, dir: first === "asc" ? "desc" : "asc" };
      else delete SORT[tableKey];
      onChange();
    });
    th.textContent = "";
    th.appendChild(btn);
  }
}

function paintSortHeaders(tableKey) {
  const spec = SORT[tableKey];
  for (const th of document.querySelectorAll('th[data-sort="' + tableKey + '"]')) {
    const on = spec && spec.key === th.dataset.key;
    th.setAttribute("aria-sort", on ? (spec.dir === "asc" ? "ascending" : "descending") : "none");
    const arrow = th.querySelector(".sort-arrow");
    if (arrow) arrow.textContent = on ? (spec.dir === "asc" ? "▲" : "▼") : "";
  }
}

/* What each sorted column reads, per table. */
const POSITION_COLUMNS = {
  name:   { get: r => r.name, text: true },
  shares: { get: r => r.s },
  avg:    { get: r => r.c },
  price:  { get: r => r.price },
  value:  { get: r => (r.priced ? r.v : null) },
  pl:     { get: r => r.pl },
  weight: { get: r => (r.w !== null && r.w !== undefined ? r.w : null) },
  drift:  { get: r => (r.priced && !r.parked ? r.d : null) },
};
const TX_COLUMNS = {
  date:   { get: t => t.date || "", text: true },
  kind:   { get: t => t.kind, text: true },
  ticker: { get: t => t.ticker, text: true },
  shares: { get: t => (t.kind === "split" ? t.ratio : t.shares) },
  price:  { get: t => (t.kind === "split" ? null : t.price) },
  fee:    { get: t => (t.kind === "split" ? null : t.fee || 0) },
  amount: { get: t => (t.kind === "split" ? null : (t.shares || 0) * (t.price || 0)) },
};
const EDITOR_COLUMNS = {
  ticker: { get: p => p.t, text: true },
  name:   { get: p => p.name, text: true },
  shares: { get: p => p.s },
  avg:    { get: p => p.c },
  price:  { get: p => p.price },
  target: { get: p => p.target },
};

/* ---------- derived state (prototype) ---------- */
function computed() {
  const rows = POSITIONS.map(p => {
    const priced = p.price !== null && p.price !== undefined;
    const v = priced ? p.s * p.price : 0;
    const basis = p.s * p.c;
    return { ...p, priced, v, basis, pl: priced ? v - basis : null };
  });
  const equityTotal = rows.reduce((a, r) => a + (!r.parked && r.priced ? r.v : 0), 0);
  const total = rows.reduce((a, r) => a + r.v, 0);
  const hasParked = rows.some(r => r.parked);
  const wealthMode = allocWithBonds && hasParked;
  const denom = wealthMode ? total : equityTotal;

  rows.forEach(r => {
    /* Equity-only drift always — that is the rebalance signal. Display weight
       follows the allocation toggle: among equities, or of total wealth. */
    if (!r.parked && r.priced && equityTotal) {
      r.wEq = r.v / equityTotal * 100;
      r.d = r.wEq - r.target;
    } else {
      r.wEq = null;
      r.d = null;
    }
    if (!r.priced || !denom) { r.w = null; return; }
    if (r.parked && !wealthMode) { r.w = null; return; }
    r.w = r.v / denom * 100;
  });
  rows.sort((a, b) => {
    if (!wealthMode && a.parked !== b.parked) return a.parked ? 1 : -1;
    return Math.abs(b.d || 0) - Math.abs(a.d || 0);
  });
  return { rows, total, equityTotal, wealthMode, hasParked };
}
const atTarget = (r) => !r.parked && r.d !== null && Math.abs(r.d) < 0.005;
const stateTotal = (rows) => rows.reduce((a, r) => a + r.v, 0);

/* ---------- KPI row ---------- */
function renderKpis(state) {
  const rows = state.rows;
  const priced = rows.filter(r => r.priced);
  const basis = rows.reduce((a, r) => a + r.basis, 0);
  const unrlsd = priced.reduce((a, r) => a + r.pl, 0);
  const pricedBasis = priced.reduce((a, r) => a + r.basis, 0);

  $("kpi-value").textContent = money(state.total);
  const parkedVal = rows.filter(r => r.parked && r.priced).reduce((a, r) => a + r.v, 0);
  $("kpi-value-sub").textContent = rows.length
    ? (parkedVal
        ? money(state.equityTotal) + " in equities · " + money(parkedVal) + " parked (outside weights)"
        : priced.length < rows.length
          ? money(state.total) + " across " + priced.length + " of " + rows.length + " positions — the rest are unpriced"
          : "at " + (rows[0] && rows[0].as_of_kind === "close" ? "the last close" : "current market prices"))
    : "nothing held yet";

  const el = $("kpi-unrealised");
  el.textContent = "";
  el.classList.toggle("gain", unrlsd >= 0);
  el.classList.toggle("loss", unrlsd < 0);
  el.textContent = priced.length ? money(unrlsd, true) : "—";
  if (priced.length && pricedBasis) {
    const p = document.createElement("span");
    p.className = "delta " + (unrlsd >= 0 ? "delta-gain" : "delta-loss");
    p.textContent = (unrlsd >= 0 ? "▲ " : "▼ ") + Math.abs(unrlsd / pricedBasis * 100).toFixed(1) + "%";
    el.appendChild(p);
  }
  /* The cost basis lost its own card to Realised profit and is reported here,
     which is the only place it was ever read as anything but a lone total:
     it is the thing unrealised profit is measured against. */
  $("kpi-unrealised-sub").textContent = basis
    ? "against " + money(basis) + " invested · " +
      shares(rows.reduce((a, r) => a + r.s, 0)) + " shares at the prices you paid for them"
    : "enter an average price to see profit";

  /* Deliberately computed from nothing on this screen: realised profit is a
     stored total, not a fold over the rows, because the sales that made it may
     have closed their positions. It is never added to the figure above it. */
  const realised = SERVER.realised || 0;
  const rel = $("kpi-realised");
  rel.textContent = realised ? money(realised, true) : money(0);
  rel.classList.toggle("gain", realised > 0);
  rel.classList.toggle("loss", realised < 0);
  $("kpi-realised-sub").textContent = realised
    ? "lifetime · locked in by sales"
    : "nothing sold in this portfolio yet";

  const worst = rows.find(r => r.priced && !r.parked) || null;
  if (!worst || !rows.some(r => !r.parked)) {
    $("kpi-drift").textContent = "—";
    $("kpi-drift-sub").textContent = "set target weights to see drift";
  } else {
    $("kpi-drift").textContent = pct(worst.d, 2) + " pp";
    $("kpi-drift-sub").textContent =
      worst.t + " · " + worst.w.toFixed(2) + "% now against a " + worst.target.toFixed(2) + "% target";
  }

  $("btn-sell").disabled = !rows.length;

  $("pf-title").textContent = SERVER.active_name;
  $("pf-name").textContent = SERVER.active_name;
  $("pf-avatar").textContent = (SERVER.active_name.trim()[0] || "P").toUpperCase();
  const nParked = rows.filter(r => r.parked).length;
  $("pf-sub").textContent = (nParked ? "US equities + parked bonds" : "US equities") +
    " · USD · " + rows.length +
    (rows.length === 1 ? " position" : " positions") + " · prices fetched on open";
  $("tab-holdings-count").textContent = rows.length;
  $("data-file").textContent = SERVER.data_file;
}

/* ---------- allocation donut (prototype) ---------- */
const SVGNS = "http://www.w3.org/2000/svg";
const CX = 110, CY = 110;
const R_IN = 58, R_ON = 84, R_MIN = 68, R_MAX = 104;

function sectorPath(a0, a1, ri, ro) {
  const large = (a1 - a0) > Math.PI ? 1 : 0;
  const p = (r, a) => [CX + r * Math.cos(a), CY + r * Math.sin(a)];
  const [x1, y1] = p(ro, a0), [x2, y2] = p(ro, a1);
  const [x3, y3] = p(ri, a1), [x4, y4] = p(ri, a0);
  return "M" + x1 + " " + y1 +
         "A" + ro + " " + ro + " 0 " + large + " 1 " + x2 + " " + y2 +
         "L" + x3 + " " + y3 +
         "A" + ri + " " + ri + " 0 " + large + " 0 " + x4 + " " + y4 + "Z";
}

function renderDonut(state) {
  const svg = $("donut");
  svg.textContent = "";
  const tip = $("donut-tip");
  tip.dataset.on = "0";

  const mode = $("alloc-mode");
  if (mode) {
    mode.hidden = !state.hasParked;
    $("alloc-equities").classList.toggle("active", !state.wealthMode);
    $("alloc-equities").setAttribute("aria-pressed", String(!state.wealthMode));
    $("alloc-wealth").classList.toggle("active", state.wealthMode);
    $("alloc-wealth").setAttribute("aria-pressed", String(state.wealthMode));
  }

  const centre = state.wealthMode ? state.total : state.equityTotal;
  const slices = state.rows.filter(r => r.priced && r.w !== null && r.v > 0);
  $("donut-total").textContent = moneyShort(centre);
  $("donut-count").textContent = state.wealthMode
    ? slices.length + (slices.length === 1 ? " holding" : " holdings")
    : slices.length + (slices.length === 1 ? " equity" : " equities");

  const desc = $("donut-desc");
  if (desc) {
    desc.textContent = state.wealthMode
      ? "Share of total wealth, including parked bonds. Drift rings still measure equities against their targets."
      : "How wide a slice is shows what the position weighs among equities. How far it reaches shows drift from target.";
  }

  const byT = Object.fromEntries(state.rows.map(r => [r.t, r]));
  const ordered = POSITIONS.map(p => byT[p.t]).filter(r => r && r.v > 0 && r.w !== null);

  const GAP = 0.012;
  let a = -Math.PI / 2;
  for (const r of ordered) {
    const span = (r.w / 100) * Math.PI * 2;
    /* Arc width follows the allocation toggle (equities or wealth). Drift
       radius always uses equity-only weight vs target — mixing a bond into
       that ratio compares wealth-% to an equity-only target. Parked bonds
       have no target: keep them on the reference ring. */
    const ratio = r.parked || r.target <= 0 ? 1 : r.wEq / r.target;
    const ro = Math.max(R_MIN, Math.min(R_MAX,
      ratio >= 1 ? R_ON + (ratio - 1) * 46 : R_ON - (1 - ratio) * 60));

    const path = document.createElementNS(SVGNS, "path");
    path.setAttribute("d", sectorPath(a + GAP / 2, a + span - GAP / 2, R_IN, ro));
    path.setAttribute("fill", r.col);
    path.setAttribute("class", "donut-arc");
    path.addEventListener("click", () => jumpToRow(r.t));

    const mid = a + span / 2;
    const tx = CX + (ro + 4) * Math.cos(mid);
    const ty = CY + (ro + 4) * Math.sin(mid);
    const ofWhat = state.wealthMode ? "of wealth" : "of equities";
    const verdict = r.parked ? "parked · no target"
      : r.target <= 0 ? "no target weight set"
      : atTarget(r) ? "at target"
      : pct(r.d, 2) + " pp " + (r.d > 0 ? "over" : "under") + " target";
    path.addEventListener("mouseenter", () => {
      tip.innerHTML = '<i style="background:' + r.col + '"></i>' + r.name + " · " +
        money(r.v) + '<span class="tip-sub">' + r.w.toFixed(2) + "% " + ofWhat + " — " +
        verdict + "</span>";
      tip.style.left = (tx / 220 * 100) + "%";
      tip.style.top = (ty / 220 * 100) + "%";
      tip.dataset.on = "1";
    });
    path.addEventListener("mouseleave", () => { tip.dataset.on = "0"; });

    svg.appendChild(path);
    a += span;
  }

  if (ordered.length) {
    const ref = document.createElementNS(SVGNS, "circle");
    ref.setAttribute("cx", CX); ref.setAttribute("cy", CY); ref.setAttribute("r", R_ON);
    ref.setAttribute("fill", "none");
    ref.setAttribute("stroke", "#5E6278");
    ref.setAttribute("stroke-width", "1");
    ref.setAttribute("stroke-dasharray", "3 4");
    ref.setAttribute("stroke-opacity", ".55");
    ref.setAttribute("pointer-events", "none");
    svg.appendChild(ref);
  }

  const legend = $("donut-legend");
  legend.textContent = "";
  const legendRows = (state.wealthMode ? state.rows.filter(r => r.w !== null) : state.rows.filter(r => !r.parked && r.w !== null))
    .slice()
    .sort((a, b) => (b.w || 0) - (a.w || 0));
  for (const r of legendRows.slice(0, 7)) {
    const row = document.createElement("div");
    row.className = "legend-row";
    row.innerHTML =
      '<span class="legend-swatch" style="background:' + r.col + '"></span>' +
      '<span class="legend-tkr">' + r.t + "</span>" +
      '<span class="legend-cur num">' + r.w.toFixed(2) + "%</span>" +
      '<span class="legend-tgt num">' + (r.parked ? "—" : r.target.toFixed(2) + "%") + "</span>";
    legend.appendChild(row);
  }
  if (legendRows.length > 7) {
    const rest = document.createElement("div");
    rest.className = "legend-row";
    rest.style.color = "var(--faint)";
    rest.innerHTML = '<span></span><span style="grid-column:2/-1">' +
      (legendRows.length - 7) + " more not listed</span>";
    legend.appendChild(rest);
  }
}

/* ---------- drift digest (prototype) ---------- */
function ddChip(r) {
  const b = document.createElement("button");
  b.type = "button";
  b.className = "dd-tkr";
  b.textContent = r.t;
  b.title = r.name + " — jump to row";
  b.setAttribute("aria-label", r.name + ". Jump to row.");
  b.addEventListener("click", () => jumpToRow(r.t));
  return b;
}
function ddPart(box, tickerRow, text) {
  const span = document.createElement("span");
  span.className = "dd-part";
  if (tickerRow) { span.appendChild(ddChip(tickerRow)); span.appendChild(document.createTextNode(" ")); }
  span.appendChild(document.createTextNode(text));
  box.appendChild(span);
}
function renderDigest(state) {
  const box = $("drift-digest");
  box.textContent = "";
  const rows = state.rows.filter(r => r.priced && !r.parked);
  if (!rows.length) { box.hidden = true; return; }
  box.hidden = false;

  const label = document.createElement("span");
  label.className = "dd-label";
  label.textContent = "Drift";
  box.appendChild(label);

  const overs = rows.filter(r => !atTarget(r) && r.d > 0).sort((a, b) => b.d - a.d);
  const unders = rows.filter(r => !atTarget(r) && r.d < 0).sort((a, b) => a.d - b.d);
  const ats = rows.filter(atTarget);

  if (overs.length) ddPart(box, overs[0], " " + pct(overs[0].d, 2) + " pp over target");
  if (unders.length) ddPart(box, unders[0], " " + pct(unders[0].d, 2) + " pp under");
  for (const r of ats) ddPart(box, r, " at target");

  const named = (overs.length ? 1 : 0) + (unders.length ? 1 : 0) + ats.length;
  const rest = rows.filter(r => r !== overs[0] && r !== unders[0] && !ats.includes(r));
  if (rest.length) {
    const band = Math.max(...rest.map(r => Math.abs(r.d)));
    ddPart(box, null, (named ? "· " : "") + rest.length + " more within " + band.toFixed(2) + " pp");
  }
}

/* ---------- holdings table (prototype anatomy, XTB's columns) ---------- */
/* Which companies are showing their transactions. Kept outside the render so
   an expanded row survives a price fetch or a save. */
const EXPANDED = new Set();

/* Oldest first: an average price is a thing that accumulates, and reading how
   it got where it is only works forwards.

   Ordered by the server's `seq` — its position in the fold — and not by date.
   Two trades share a date often enough, and sorting on the date alone would
   pair a running share count with a different row than the one it came from. */
function lotsFor(ticker) {
  return (SERVER.transactions || [])
    .filter(t => t.ticker === ticker)
    .slice()
    .sort((a, b) => (a.seq || 0) - (b.seq || 0));
}

function lotsRow(r) {
  const rows = lotsFor(r.t);
  const tr = document.createElement("tr");
  tr.className = "lots";
  const td = document.createElement("td");
  td.colSpan = 9;

  const body = rows.map((t, i) => {
    const last = i === rows.length - 1;
    const isSplit = t.kind === "split";
    const amount = isSplit ? "—" : money((t.shares || 0) * (t.price || 0));
    const realised = t.kind === "sell" && t.realised !== null && t.realised !== undefined
      ? ' <span class="' + (t.realised >= 0 ? "realised-note" : "realised-note loss") + '">' +
        money(t.realised, true) + "</span>"
      : "";
    return '<tr' + (last ? ' class="final"' : "") + ">" +
      '<td class="date">' + (t.date || "opening balance") + "</td>" +
      '<td><span class="badge badge-' + t.kind + '">' + (KIND_LABEL[t.kind] || t.kind) + "</span></td>" +
      "<td>" + (isSplit ? "×" + shares(t.ratio) : shares(t.shares)) + "</td>" +
      "<td>" + (isSplit ? "—" : money(t.price)) + "</td>" +
      "<td>" + amount + realised + "</td>" +
      '<td class="run">' + shares(t.shares_after) + "</td>" +
      '<td class="run">' + money(t.avg_after) + "</td>" +
      "</tr>";
  }).join("");

  td.innerHTML =
    '<div class="lots-inner">' +
      '<div class="lots-head"><b>' + r.t + "</b> · " + rows.length +
        (rows.length === 1 ? " transaction" : " transactions") +
        ' <span class="hint">oldest first · the last two columns are the position after each one</span></div>' +
      '<table class="lots-table"><thead><tr>' +
        "<th>Date</th><th>Type</th><th>Shares</th><th>Price</th><th>Amount</th>" +
        "<th>Shares held</th><th>Average price</th>" +
      "</tr></thead><tbody>" + body + "</tbody></table>" +
    "</div>";
  tr.appendChild(td);
  return tr;
}

function toggleLots(ticker) {
  if (EXPANDED.has(ticker)) EXPANDED.delete(ticker);
  else EXPANDED.add(ticker);
  render({ keepInputs: true });
}

function renderTable(state) {
  const tbody = $("positions-tbody");
  tbody.textContent = "";
  $("positions-empty").hidden = state.rows.length > 0;
  const maxAbs = Math.max(...state.rows.filter(r => !r.parked).map(r => Math.abs(r.d || 0)), 0.01);
  paintSortHeaders("positions");
  const sorted = sortRows("positions", state.rows, POSITION_COLUMNS);
  const spec = SORT.positions;
  $("positions-hint").textContent = spec
    ? "sorted by " + POSITION_HINT[spec.key] +
      (spec.dir === "asc" ? " — smallest first" : " — largest first")
    : (state.wealthMode
        ? "weights are % of total wealth — drift still measures equities vs their targets"
        : "sorted by absolute drift — the largest gap from target weight first");

  // the freshest stamp on the page is the yardstick: a source that has stopped
  // updating shows up as a row lagging the others, exactly as KO did in 0004.
  // Accrual stamps are an FX publication day, not a venue — leave them out.
  const quoted = state.rows.filter(r => !r.parked);
  const freshest = quoted.reduce((a, r) => (r.as_of && r.as_of > a ? r.as_of : a), "");

  for (const r of sorted) {
    const lagging = r.priced && !r.parked && freshest && r.as_of !== freshest;
    const snapLine = !r.priced
      ? '<span class="chip-unpriced">no quote</span>'
      : r.parked
        ? '<span class="sub faint">' + (r.source || "") + " · " + stampText(r) +
          (state.wealthMode ? "" : " · outside equity weights") + "</span>"
      : lagging
        ? '<span class="chip-stale">stale · ' + stampText(r) + "</span>"
        : '<span class="sub faint">' + (r.source || "") + " · " + stampText(r) + "</span>";

    const barW = Math.abs(r.d || 0) / maxAbs * 48;
    const barStyle = (r.d || 0) >= 0 ? "left:50%;width:" + barW + "%" : "right:50%;width:" + barW + "%";
    const barCls = (r.d || 0) >= 0 ? "d-over" : "d-under";
    const driftCell = !r.priced || r.parked
      ? '<td class="drift-cell"><span class="num d-at">' + (r.parked ? "parked" : "—") +
        '</span><div class="d-bar"></div></td>'
      : atTarget(r)
        ? '<td class="drift-cell"><span class="num d-val d-at">at target</span><div class="d-bar"></div></td>'
        : '<td class="drift-cell"><span class="num d-val">' + pct(r.d, 2) +
          ' pp</span><div class="d-bar"><i class="' + barCls + '" style="' + barStyle + '"></i></div></td>';

    const plCls = r.pl === null ? "" : (r.pl >= 0 ? "gain" : "loss");
    const plCell = r.pl === null
      ? '<td class="num pending">—</td>'
      : '<td class="num ' + plCls + '">' + money(r.pl, true) +
        '<span class="sub num ' + plCls + '">' + (r.pl >= 0 ? "▲" : "▼") + " " +
        (r.basis ? pct(r.pl / r.basis * 100, 2) : "—") + "</span></td>";

    const tr = document.createElement("tr");
    tr.id = "row-" + r.t;
    tr.innerHTML =
      '<td class="rail"><i style="background:' + r.col + '"></i></td>' +
      '<td class="holding"><button type="button" class="co-toggle" aria-expanded="' +
        (EXPANDED.has(r.t) ? "true" : "false") + '"><i class="chev">\u203a</i><div class="co">' +
        '<span class="co-avatar" style="background:' + r.col + '">' + r.t.slice(0, 2) + "</span>" +
        '<span class="co-text"><span class="t">' + r.name + "</span>" +
          '<span class="n">' + r.t + "</span></span>" +
      "</div></button></td>" +
      '<td class="num">' + shares(r.s) + "</td>" +
      '<td class="num">' + money(r.c) + '<span class="sub num">' + money(r.basis) + " paid</span></td>" +
      '<td class="num">' + (r.priced ? money(r.price) : '<span class="pending">no price</span>') +
        snapLine + "</td>" +
      '<td class="num">' + (r.priced ? money(r.v) : "—") + "</td>" +
      plCell +
      '<td class="num" style="font-weight:400">' +
        (r.w !== null && r.w !== undefined ? r.w.toFixed(2) + "%" : "—") +
        '<span class="sub strong num">' +
          (r.parked ? "no target" : r.target.toFixed(2) + "%") +
        "</span></td>" +
      driftCell;
    tr.querySelector(".co-toggle").addEventListener("click", () => toggleLots(r.t));
    if (typeof openCompany === "function") {                    // company.js
      const open = document.createElement("button");
      const profiled = (SERVER.profiles || []).includes(r.t);
      open.type = "button";
      open.className = "co-open" + (profiled ? " has-profile" : "");
      open.textContent = profiled ? "Profile" : "View";
      open.title = profiled ? "Company view — company profile, charts, transcripts"
                            : "Company view — earnings-call transcripts";
      open.addEventListener("click", () => openCompany(r.t));
      tr.querySelector("td.holding").appendChild(open);
    }
    tbody.appendChild(tr);
    if (EXPANDED.has(r.t)) tbody.appendChild(lotsRow(r));
  }
}

const POSITION_HINT = {
  name: "company", shares: "share count", avg: "average price", price: "last price",
  value: "current value", pl: "unrealised profit", weight: "current weight", drift: "drift",
};

function jumpToRow(ticker) {
  showTab("overview");
  const row = $("row-" + ticker);
  if (!row) return;
  if (row.scrollIntoView)
    row.scrollIntoView({ behavior: reducedMotion ? "auto" : "smooth", block: "center" });
  row.classList.add("row-flash");
  setTimeout(() => row.classList.remove("row-flash"), reducedMotion ? 150 : 900);
}

/* ---------- holdings editor ---------- */
function cellInput(p, field, opts) {
  const input = document.createElement("input");
  input.className = "cell " + (opts.cls || "");
  input.type = opts.type || "text";
  if (opts.type === "number") { input.step = "any"; input.min = "0"; input.inputMode = "decimal"; }
  input.value = opts.value;
  input.placeholder = opts.placeholder || "";
  input.setAttribute("aria-label", opts.label);
  const take = () => {
    p[field] = opts.type === "number" ? numberOf(input.value) : input.value;
    if (field === "t") p.t = String(p.t).toUpperCase();
    queueSave();
    render({ keepInputs: true });
  };
  input.addEventListener("input", take);
  input.addEventListener("change", take);   // spinners, autofill, paste
  input.addEventListener("blur", () => { if (saveTimer) { clearTimeout(saveTimer); saveHoldings(); } });
  return input;
}

/* How many ledger rows stand behind a position — said on the row itself, so
   the average price is visibly a result and not a figure somebody typed. */
function txCountOf(ticker) {
  return (SERVER.transactions || []).filter(t => t.ticker === ticker).length;
}
function txCount(ticker) {
  const n = txCountOf(ticker);
  return n + (n === 1 ? " transaction" : " transactions");
}

function numberOf(text) {
  const n = parseFloat(String(text).replace(",", ".").trim());
  return Number.isFinite(n) && n >= 0 ? n : 0;
}

function renderEditor() {
  const tbody = $("editor-tbody");
  tbody.textContent = "";
  paintSortHeaders("editor");
  for (const p of sortRows("editor", POSITIONS, EDITOR_COLUMNS)) {
    const tr = document.createElement("tr");

    const rail = document.createElement("td");
    rail.className = "rail";
    rail.innerHTML = '<i style="background:' + p.col + '"></i>';

    /* A ticker with transactions behind it is no longer a free-text field:
       retyping it would leave every one of those transactions filed under the
       old symbol, and the position would quietly split in two. A blank row
       added for a company not yet bought still needs one. */
    const tdT = document.createElement("td");
    tdT.className = "holding";
    if (txCountOf(p.t) > 0) {
      tdT.classList.add("derived");
      tdT.innerHTML = '<span class="tkr">' + p.t + "</span>";
    } else {
      tdT.appendChild(cellInput(p, "t", { cls: "ticker", value: p.t, label: "Ticker", placeholder: "AAPL" }));
    }

    const tdName = document.createElement("td");
    tdName.className = "holding";
    tdName.appendChild(cellInput(p, "name", { cls: "coname", value: p.name === p.t ? "" : p.name,
                                              label: "Company name", placeholder: "company name" }));

    /* Shares and average price used to be typed here, copied off the broker.
       They are folded out of the transaction ledger now, so they are shown
       rather than offered: typing over them would be typing over history, and
       the number would go back to what the transactions say on the next open
       anyway. Buy, Sell and Record transaction are how they move. */
    const tdS = document.createElement("td");
    tdS.className = "num derived";
    tdS.textContent = p.s ? shares(p.s) : "—";

    const tdC = document.createElement("td");
    tdC.className = "num derived";
    tdC.innerHTML = p.c
      ? money(p.c) + '<span class="sub">from ' + txCount(p.t) + '</span>'
      : '<span class="pending">no transactions</span>';

    const tdPrice = document.createElement("td");
    tdPrice.className = "num price-cell";
    tdPrice.dataset.price = p.t;

    const tdTarget = document.createElement("td");
    tdTarget.className = "num";
    if (p.parked) {
      tdTarget.classList.add("derived");
      tdTarget.innerHTML = '—<span class="sub">outside weights</span>';
    } else {
      tdTarget.appendChild(cellInput(p, "target", { type: "number", value: p.target || "",
                                                    label: "Target weight, percent" }));
    }

    const tdKill = document.createElement("td");
    const kill = document.createElement("button");
    kill.type = "button";
    kill.className = "row-kill";
    kill.textContent = "×";
    kill.title = "Remove " + (p.t || "this row");
    kill.setAttribute("aria-label", "Remove " + (p.t || "row"));
    kill.addEventListener("click", () => removeRow(p));
    tdKill.appendChild(kill);

    tr.append(rail, tdT, tdName, tdS, tdC, tdPrice, tdTarget, tdKill);
    tbody.appendChild(tr);
  }
  refreshEditorPrices();
}

function refreshEditorPrices() {
  for (const td of document.querySelectorAll("[data-price]")) {
    const p = POSITIONS.find(x => x.t === td.dataset.price);
    td.innerHTML = !p || p.price === null || p.price === undefined
      ? '<span class="pending">not priced</span>'
      : money(p.price) + '<span class="sub">' + (p.source || "") + " · " + stampText(p) + "</span>";
  }
}

function addRow() {
  POSITIONS.push({ t: "", name: "", s: 0, c: 0, target: 0, price: null, as_of: null,
                   as_of_kind: null, source: null, col: SLICE[POSITIONS.length % SLICE.length], lock: false });
  showTab("holdings");
  renderEditor();
  buildTargets();
  const inputs = $("editor-tbody").lastElementChild.querySelectorAll("input.ticker");
  if (inputs.length) inputs[0].focus();
}

async function removeRow(p) {
  const label = p.t || "this empty row";
  /* Only the target weight actually lives on this row. If shares are held,
     the transactions behind them stay and the row comes straight back — say
     so rather than letting the click look like it failed. */
  const ok = await confirmDialog("Remove " + label + "?",
    p.s > 0
      ? label + " is still held, so its transactions stay and the row stays with them. " +
        "Sell the position, or strike its transactions in the Transactions tab."
      : "The row and its target weight leave this portfolio. Your other portfolios are untouched.",
    "Remove");
  if (!ok) return;
  POSITIONS = POSITIONS.filter(x => x !== p);
  adopt(await saveRequest());
}

/* ---------- saving ---------- */
function holdingsPayload() {
  return POSITIONS.map(p => ({
    ticker: String(p.t || "").trim().toUpperCase(),
    name: String(p.name || "").trim(),
    target: p.target,
  }));
}
function saveRequest() { return api("/api/holdings", { holdings: holdingsPayload() }); }

function queueSave() {
  clearTimeout(saveTimer);
  $("editor-note").classList.add("saving");
  $("editor-note").textContent = "Saving…";
  saveTimer = setTimeout(saveHoldings, 700);
}

async function saveHoldings() {
  saveTimer = null;
  try {
    const payload = await saveRequest();
    const typing = document.activeElement && document.activeElement.classList &&
                   document.activeElement.classList.contains("cell");
    if (typing) adoptPricesOnly(payload); else adopt(payload);
    $("editor-note").classList.remove("saving");
    $("editor-note").textContent = "Saved to disk.";
  } catch (err) {
    $("editor-note").classList.remove("saving");
    $("editor-note").textContent = "Not saved: " + err.message;
  }
}

/* ---------- target weights (prototype) ---------- */
const round1 = (x) => Math.round(x * 10) / 10;
const targetSum = () => POSITIONS.filter(p => !p.parked).reduce((a, p) => a + p.target, 0);

function absorbResidue(pool) {
  let steps = Math.round((100 - targetSum()) * 10);
  if (!steps || !pool.length) return;
  const order = pool.slice().sort((a, b) => b.target - a.target);
  const dir = steps > 0 ? 0.1 : -0.1;
  let i = 0, guard = 0;
  while (steps !== 0 && guard++ < 10000) {
    const p = order[i % order.length];
    i++;
    if (dir < 0 && p.target < 0.1) continue;
    p.target = round1(p.target + dir);
    steps += dir > 0 ? -1 : 1;
  }
}

function balanceAround(edited) {
  if (!autoBalance || edited.parked) return;
  const others = POSITIONS.filter(p => p !== edited && !p.parked);
  const free = others.filter(p => !p.lock);
  const pinned = others.reduce((a, p) => a + (p.lock ? p.target : 0), 0);
  const room = Math.max(0, 100 - pinned);
  edited.target = round1(Math.max(0, Math.min(edited.target, room)));
  if (!free.length) return;
  const rest = room - edited.target;
  const freeSum = free.reduce((a, p) => a + p.target, 0);
  if (freeSum > 0) free.forEach(p => { p.target = round1(p.target / freeSum * rest); });
  else free.forEach(p => { p.target = round1(rest / free.length); });
  absorbResidue(free);
}

function normaliseTargets() {
  const equity = POSITIONS.filter(p => !p.parked);
  const sum = targetSum();
  if (!equity.length) return;
  if (sum <= 0) {
    const even = round1(100 / equity.length);
    equity.forEach(p => { p.target = even; });
  } else {
    equity.forEach(p => { p.target = round1(p.target / sum * 100); });
  }
  absorbResidue(equity.slice());
}

const LOCK_SVG =
  '<svg viewBox="0 0 24 24" width="12" height="12" aria-hidden="true" focusable="false">' +
  '<path d="M7.5 10.5V7a4.5 4.5 0 0 1 9 0v3.5" fill="none" stroke="currentColor" ' +
  'stroke-width="2.4" stroke-linecap="round"/>' +
  '<rect x="4" y="10.5" width="16" height="10.5" rx="2.5" fill="currentColor"/></svg>';

function buildTargets() {
  const list = $("targets-list");
  list.textContent = "";
  POSITIONS.forEach(p => {
    if (!p.t || p.parked) return;   // parked bonds have no target weight
    const row = document.createElement("div");
    row.className = "tgt-row" + (p.lock ? " locked" : "");
    row.innerHTML =
      '<span class="swatch" style="background:' + p.col + '"></span>' +
      '<span class="tkr">' + p.t + "</span>" +
      '<input type="number" class="tgt-input" step="0.1" min="0" max="100" value="' +
        p.target.toFixed(1) + '" data-for="' + p.t +
        '" aria-label="Target weight for ' + p.name + ', percent">' +
      '<button type="button" class="lock-btn' + (p.lock ? " on" : "") +
        '" data-lock="' + p.t + '" aria-pressed="' + (p.lock ? "true" : "false") +
        '" title="' + (p.lock ? "Unpin " + p.t + " — let it move again"
                              : "Pin " + p.t + " — hold this weight while others adjust") +
        '" aria-label="' + (p.lock ? "Unpin " : "Pin ") + p.name + '">' + LOCK_SVG + "</button>" +
      '<span class="tgt-now" data-now="' + p.t + '"></span>';
    list.appendChild(row);
  });
}

function refreshTargets(state) {
  const byT = Object.fromEntries(state.rows.map(r => [r.t, r]));
  const C = parseMoney($("contrib").value);
  const base = state.equityTotal + (C === null ? 0 : C);
  for (const el of document.querySelectorAll("[data-now]")) {
    const r = byT[el.dataset.now];
    if (!r || r.parked) continue;
    el.classList.toggle("over", r.priced && !atTarget(r) && r.d > 0);
    el.classList.toggle("under", r.priced && !atTarget(r) && r.d < 0);
    const want = r.target / 100 * base;
    el.innerHTML = (!r.priced
        ? "no price"
        : atTarget(r)
          ? "now <b>" + r.w.toFixed(2) + "%</b>"
          : "now <b>" + r.w.toFixed(2) + "%</b> · " + pct(r.d, 2)) +
      '<span class="tgt-val num">' + moneyShort(r.v) + " → " + moneyShort(want) + "</span>";
  }

  for (const input of document.querySelectorAll(".tgt-input")) {
    if (input === editingInput) continue;
    const p = POSITIONS.find(x => x.t === input.dataset.for);
    if (!p) continue;
    const shown = p.target.toFixed(1);
    if (input.value !== shown) input.value = shown;
  }

  const sum = targetSum();
  const el = $("target-sum");
  const ok = Math.abs(sum - 100) < 0.05;
  el.classList.toggle("good", ok);
  el.classList.toggle("bad", !ok);
  el.textContent = "Total " + sum.toFixed(1) + "%" +
    (ok ? " ✓"
        : sum < 100 ? " — " + (100 - sum).toFixed(1) + "% unassigned"
                    : " — " + (sum - 100).toFixed(1) + "% over");
  $("btn-normalise").hidden = ok;
}

$("targets-list").addEventListener("input", (e) => {
  const t = e.target.dataset.for;
  if (!t) return;
  const p = POSITIONS.find(x => x.t === t);
  const v = parseFloat(e.target.value);
  p.target = Number.isFinite(v) && v >= 0 ? Math.min(v, 100) : 0;
  balanceAround(p);
  editingInput = e.target;
  if (Number.isFinite(v) && Math.abs(v - p.target) >= 0.05)
    e.target.value = p.target.toFixed(1);
  render({ keepInputs: true });
  editingInput = null;
  queueSave();
});

$("targets-list").addEventListener("click", (e) => {
  const btn = e.target.closest ? e.target.closest("[data-lock]") : null;
  if (!btn) return;
  const p = POSITIONS.find(x => x.t === btn.dataset.lock);
  if (!p) return;
  p.lock = !p.lock;
  buildTargets();
  render({ keepInputs: true });
});

$("auto-balance").addEventListener("change", (e) => {
  autoBalance = e.target.checked;
  if (autoBalance) normaliseTargets();
  buildTargets();
  render({ keepInputs: true });
  queueSave();
});
$("btn-normalise").addEventListener("click", () => {
  normaliseTargets();
  buildTargets();
  render({ keepInputs: true });
  queueSave();
});
$("btn-even").addEventListener("click", () => {
  const equity = POSITIONS.filter(p => !p.parked);
  if (!equity.length) return;
  const even = round1(100 / equity.length);
  equity.forEach(p => { p.target = even; });
  absorbResidue(equity.slice());
  buildTargets();
  render({ keepInputs: true });
  queueSave();
});

/* ---------- rebalance engine (prototype) ---------- */
const MIN_TRADE = 0;
function setPreviewExpanded(v) { previewExpanded = v; renderPreview(false); }

function parseMoney(text) {
  const s = String(text).replace(/[$,\s]/g, "");
  if (s === "") return 0;
  const n = parseFloat(s);
  return Number.isFinite(n) && n >= 0 ? Math.round(n * 100) / 100 : null;
}
function rebalanceMode() {
  const el = document.querySelector('input[name="mode"]:checked');
  return el ? el.value : "contribute";
}

function planContribute(rows, C) {
  const Vc = stateTotal(rows) + C;
  let alloc = rows.map(r => ({ ...r, g: r.target / 100 * Vc - r.v, x: 0 }));
  const pos = alloc.filter(a => a.g > 0);
  if (!pos.length)
    return { trades: alloc, blocked: "Every position is at or over target — there is nothing to buy without selling. Switch to Full mode." };
  const G = pos.reduce((a, p) => a + p.g, 0);
  if (G >= C) {
    let lo = 0, hi = Math.max(...pos.map(p => p.g));
    for (let i = 0; i < 200; i++) {
      const mid = (lo + hi) / 2;
      if (pos.reduce((a, p) => a + Math.max(0, p.g - mid), 0) > C) lo = mid; else hi = mid;
    }
    alloc.forEach(a => { a.x = a.g > 0 ? Math.max(0, a.g - hi) : 0; });
  } else {
    const E = (C - G) / pos.length;
    pos.forEach(a => { a.x = a.g + E; });
  }
  return { trades: alloc };
}

function planFull(rows, C) {
  const Vc = stateTotal(rows) + C;
  return { trades: rows.map(r => {
    const x = r.target / 100 * Vc - r.v;
    return { ...r, g: x, x };
  }) };
}

function quantise(trades, floorBuys) {
  return trades.map(t => {
    let sh = 0;
    if (t.x !== 0 && t.price) {
      const raw = Math.abs(t.x) / t.price * 10000;
      sh = (floorBuys && t.x > 0 ? Math.floor(raw) : Math.round(raw)) / 10000;
    }
    const amt = Math.round(sh * (t.price || 0) * 100) / 100;
    return { ...t,
      buySh: t.x > 0 ? sh : 0, sellSh: t.x < 0 ? sh : 0,
      buyAmt: t.x > 0 ? amt : 0, sellAmt: t.x < 0 ? amt : 0 };
  });
}

function renderPreview(flash) {
  const box = $("reb-example");
  const C = parseMoney($("contrib").value);
  const kind = rebalanceMode();
  const all = computed().rows;
  const rows = all.filter(r => r.priced && !r.parked);   // parked bonds are outside allocation
  const parkedN = all.filter(r => r.parked).length;

  $("mode-note").textContent = kind === "contribute"
    ? "Contribute distributes the amount across the deepest underweights and sells nothing. No sale, no taxable event."
    : "Full may sell overweights to fund the buys. Every sale is a taxable event and fixes part of your realised profit for the year.";

  const held = stateTotal(rows);
  $("base-existing").textContent = money(held);
  $("base-contrib").textContent = money(C === null ? 0 : C);
  $("base-total").textContent = money(held + (C === null ? 0 : C));

  const head = (t) => '<div class="card-title" style="font-size:14px;margin-bottom:10px;">' + t + "</div>";

  if (!rows.length) {
    box.innerHTML = head("Plan") +
      '<p class="reb-preview-note">Nothing to plan with yet. Add holdings with a share ' +
      "count and an average price, and let the prices land.</p>";
    return;
  }
  if (C === null) {
    box.innerHTML = head("Plan") +
      '<p class="reb-preview-note">That is not an amount. Type a number such as ' +
      '<span class="num">1000</span> or <span class="num">$1,000.00</span>, ' +
      "or clear the field to rebalance only the capital you already hold.</p>";
    return;
  }

  const tgtSum = rows.reduce((a, p) => a + p.target, 0);
  let planRows = rows, scaleNote = "";
  if (tgtSum <= 0) {
    box.innerHTML = head("Plan") +
      '<div class="reb-warn">Every target weight is <strong>0%</strong>, so there is ' +
      "nothing to aim at. Give at least one position a weight — or press " +
      "<strong>Even split</strong>.</div>";
    return;
  }
  if (Math.abs(tgtSum - 100) >= 0.05) {
    planRows = rows.map(r => ({ ...r, target: r.target / tgtSum * 100 }));
    scaleNote = " Targets add up to " + tgtSum.toFixed(1) +
      "%, so they are read as proportions and scaled to 100% for this plan — " +
      "each one counts for " + (100 / tgtSum).toFixed(2) + "× what it says.";
  }
  if (C === 0 && kind === "contribute") {
    box.innerHTML = head("Plan") +
      '<p class="reb-preview-note">Nothing to distribute. Contribute mode only ' +
      "spends new money, and none was entered. To rebalance the " + money(held) +
      " already on the portfolio, switch to Full — closing a gap without new money " +
      "means selling something, and that is a taxable event.</p>";
    return;
  }

  const plan = kind === "contribute" ? planContribute(planRows, C) : planFull(planRows, C);
  const q = quantise(plan.trades, kind === "contribute");
  const buys = q.filter(t => t.buySh > 0).sort((a, b) => b.buyAmt - a.buyAmt);
  const sells = q.filter(t => t.sellSh > 0).sort((a, b) => b.sellAmt - a.sellAmt);
  const totB = buys.reduce((a, t) => a + t.buyAmt, 0);
  const totS = sells.reduce((a, t) => a + t.sellAmt, 0);

  const rowHtml = (t, amt, sh) =>
    "<tr><td>" + t.t + "</td>" +
    '<td class="' + (amt < 0 ? "loss" : "") + '">' + money(amt, amt < 0) + "</td>" +
    '<td class="more">' + shares(sh) + (sh === 0 ? "" : " sh") + "</td></tr>";

  const MAX_ROWS = 8;
  const lines = [];
  for (const t of sells.slice(0, MAX_ROWS)) lines.push(rowHtml(t, -t.sellAmt, t.sellSh));
  for (const t of buys.slice(0, Math.max(0, MAX_ROWS - lines.length)))
    lines.push(rowHtml(t, t.buyAmt, t.buySh));
  const everything = [...sells, ...buys];
  const truncated = everything.length > Math.min(lines.length, everything.length);
  const hidden = everything.slice(Math.min(lines.length, everything.length));
  if (truncated && !previewExpanded) {
    const hAmt = hidden.reduce((a, t) => a + t.buyAmt + t.sellAmt, 0);
    lines.push('<tr><td colspan="3"><button type="button" class="more-btn" id="reb-more-toggle">' +
               "…and " + hidden.length + " more · " + money(hAmt) + " — show all</button></td></tr>");
  } else if (truncated && previewExpanded) {
    for (const t of hidden) {
      if (t.sellSh > 0) lines.push(rowHtml(t, -t.sellAmt, t.sellSh));
      else lines.push(rowHtml(t, t.buyAmt, t.buySh));
    }
    lines.push('<tr><td colspan="3"><button type="button" class="more-btn" id="reb-more-toggle">Show fewer</button></td></tr>');
  }
  lines.push('<tr class="total"><td>Total</td><td>' +
             money(totB - totS, totB - totS < 0) + "</td><td></td></tr>");

  let note;
  if (plan.blocked) note = plan.blocked;
  else if (kind === "contribute")
    note = "Deploys " + money(totB) + " of " + money(C) +
           (C - totB >= 0.005 ? " — " + money(C - totB) + " unspent after rounding to 4 dp" : "") +
           " · sells nothing. Deepest underweights are funded first, ending at an equal shortfall.";
  else
    note = (C === 0
             ? "Rebalances the " + money(held) + " already held, adding nothing. "
             : "Rebalances " + money(held) + " held plus " + money(C) + " added. ") +
           "Buys " + money(totB) + " across " + buys.length + (buys.length === 1 ? " position" : " positions") +
           " · sells " + money(totS) + " across " + sells.length + (sells.length === 1 ? " position" : " positions") +
           " — selling is a taxable event. Every position lands on its target weight.";
  if (all.length > rows.length + parkedN)
    note += " " + (all.length - rows.length - parkedN) + " unpriced position" +
            (all.length - rows.length - parkedN === 1 ? " is" : "s are") + " left out of the plan.";
  if (parkedN)
    note += " " + parkedN + " parked bond" + (parkedN === 1 ? "" : "s") +
            " sit outside these weights and are never traded by a plan.";

  const title = kind === "contribute"
    ? "Plan — contribute " + money(C)
    : (C === 0 ? "Plan — rebalance held capital, nothing added"
               : "Plan — full, contributing " + money(C));

  box.innerHTML = head(title) +
    "<table><tbody>" + lines.join("") + "</tbody></table>" +
    '<p class="reb-preview-note">' + note + scaleNote + "</p>";

  const toggle = $("reb-more-toggle");
  if (toggle) toggle.addEventListener("click", () => setPreviewExpanded(!previewExpanded));

  if (flash) {
    box.classList.add("flash");
    setTimeout(() => box.classList.remove("flash"), reducedMotion ? 150 : 400);
  }
}

/* ---------- price snapshot line ---------- */
function renderSnapshot() {
  const s = SERVER.snapshot;
  const el = $("snapshot-status");
  const banner = $("offline-banner");
  el.classList.remove("stale", "bad");
  banner.classList.remove("bad");

  if (s.status === "fetching") el.textContent = "Fetching prices…";
  else if (s.status === "never") el.textContent = "Price snapshot: none yet";
  else if (s.status === "failed") {
    el.textContent = "Price fetch failed — last known prices";
    el.classList.add("bad");
  } else {
    el.textContent = "Price snapshot: " + whenText(s.fetched_at);
    if (s.status === "partial") el.classList.add("stale");
  }

  const fetchBtn = $("btn-fetch");
  fetchBtn.disabled = s.status === "fetching";
  fetchBtn.textContent = s.status === "fetching" ? "Fetching…" : "Fetch prices";

  if (s.status === "failed") {
    banner.hidden = false;
    banner.classList.add("bad");
    $("offline-banner-text").innerHTML =
      "<strong>No prices.</strong> " + s.notes.join(" · ") +
      ". Totals use the last prices fetched, if any, and nothing is hidden — " +
      "positions with no price at all are marked, never valued at zero silently.";
  } else if (s.unpriced && s.unpriced.length) {
    banner.hidden = false;
    $("offline-banner-text").innerHTML =
      "<strong>No quote for " + s.unpriced.join(", ") + ".</strong> " +
      "Every other ticker priced normally, so this is the symbol, not the network — " +
      "check the spelling in the Holdings tab.";
  } else {
    banner.hidden = true;
  }
}

/* ---------- render ---------- */
function render(opts) {
  if (!SERVER) return;
  const state = computed();
  renderSnapshot();
  renderKpis(state);
  renderDonut(state);
  renderDigest(state);
  renderTable(state);
  renderTransactions();
  if (!opts || !opts.keepInputs) { renderEditor(); buildTargets(); }
  else refreshEditorPrices();
  refreshTargets(state);
  renderPreview(false);
  if (typeof afterRender === "function") afterRender(state);   // company.js
}

/* ---------- tabs ---------- */
const TABS = ["overview", "rebalance", "holdings", "tx"];
/* ---------- transactions ----------
   The ledger is the only thing this app stores. Every figure elsewhere on the
   screen — shares, average price, realised profit — is folded out of this
   table, so it is also where a mistake gets corrected: strike the event and
   the derived numbers follow on their own. */
const KIND_LABEL = { buy: "BUY", sell: "SELL", split: "SPLIT" };

function txAmountCell(t) {
  const td = document.createElement("td");
  if (t.kind === "split") {
    td.innerHTML = "—&nbsp;&nbsp;<span class=\"hint\">" + shares(t.ratio) +
                   " for 1 · no money moves</span>";
    return td;
  }
  const amount = (t.shares || 0) * (t.price || 0);
  td.innerHTML = money(amount);
  if (t.kind === "sell" && t.realised !== null && t.realised !== undefined) {
    const cls = t.realised >= 0 ? "realised-note" : "realised-note loss";
    td.innerHTML += "&nbsp;&nbsp;<span class=\"" + cls + "\">" +
                    money(t.realised, true) + " realised</span>";
  }
  return td;
}

function renderTransactions() {
  const tbody = $("tx-tbody");
  tbody.textContent = "";
  paintSortHeaders("tx");
  const list = sortRows("tx", SERVER.transactions || [], TX_COLUMNS);
  $("tab-tx-count").textContent = list.length;

  if (!list.length) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 8;
    td.className = "tx-empty";
    td.textContent = "No transactions yet. Record a buy and the position appears on its own.";
    tr.appendChild(td);
    tbody.appendChild(tr);
    $("tx-note").textContent = "";
    return;
  }

  for (const t of list) {
    const tr = document.createElement("tr");

    const date = document.createElement("td");
    date.className = "date";
    /* An undated row is one migrated from the old holdings-only file: there is
       no day to show, and inventing one would be the app telling a story. */
    date.textContent = t.date || "opening balance";

    const type = document.createElement("td");
    type.innerHTML = '<span class="badge badge-' + t.kind + '">' +
                     (KIND_LABEL[t.kind] || t.kind.toUpperCase()) + "</span>";

    const tkr = document.createElement("td");
    tkr.className = "tkr";
    tkr.textContent = t.ticker;

    const sh = document.createElement("td");
    sh.textContent = t.kind === "split" ? "×" + shares(t.ratio) : shares(t.shares);

    const price = document.createElement("td");
    price.textContent = t.kind === "split" ? "—" : money(t.price);

    const fee = document.createElement("td");
    fee.textContent = t.kind === "split" ? "—" : money(t.fee || 0);

    const kill = document.createElement("td");
    kill.className = "strike";
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "row-kill";
    btn.textContent = "×";
    btn.title = "Remove this transaction";
    btn.setAttribute("aria-label", "Remove this transaction");
    btn.addEventListener("click", () => removeTransaction(t));
    kill.appendChild(btn);

    tr.append(date, type, tkr, sh, price, fee, txAmountCell(t), kill);
    tbody.appendChild(tr);
  }
  const undated = list.filter(t => !t.date).length;
  $("tx-note").textContent = undated
    ? undated + " opening balance" + (undated === 1 ? "" : "s") +
      " carried over from before transactions were recorded."
    : "";
}

async function removeTransaction(t) {
  const what = t.kind === "split"
    ? "the " + shares(t.ratio) + "-for-1 split of " + t.ticker
    : t.kind + " of " + shares(t.shares) + " " + t.ticker + " at " + money(t.price);
  const ok = await confirmDialog("Remove this transaction?",
    "Striking " + what + " rewrites what follows it: shares, average price and " +
    "realised profit are all recomputed from the transactions that remain.",
    "Remove");
  if (!ok) return;
  try {
    POSITIONS = [];
    adopt(await api("/api/transactions/delete", { id: t.id }));
  } catch (err) {
    $("snapshot-status").textContent = "Could not remove it: " + err.message;
    $("snapshot-status").classList.add("bad");
  }
}

function showTab(name) {
  for (const n of TABS) {
    const tab = $("tab-" + n), panel = $("panel-" + n);
    const on = n === name;
    tab.setAttribute("aria-selected", on ? "true" : "false");
    panel.hidden = !on;
  }
}
TABS.forEach(n => $("tab-" + n).addEventListener("click", () => showTab(n)));

/* ---------- portfolios ---------- */
function renderMenu() {
  const menu = $("pf-menu");
  menu.textContent = "";
  for (const pf of SERVER.portfolios) {
    const item = document.createElement("button");
    item.type = "button";
    item.className = "pf-item" + (pf.id === SERVER.active ? " active" : "");
    item.setAttribute("role", "option");
    item.setAttribute("aria-selected", String(pf.id === SERVER.active));
    const name = document.createElement("span");
    name.textContent = pf.name;
    const count = document.createElement("span");
    count.className = "count";
    count.textContent = pf.count + (pf.count === 1 ? " holding" : " holdings");
    item.append(name, count);
    item.addEventListener("click", async () => {
      closeMenu();
      if (pf.id !== SERVER.active) {
        POSITIONS = [];
        adopt(await api("/api/portfolios/activate", { id: pf.id }));
      }
    });

    if (SERVER.portfolios.length > 1) {
      const kill = document.createElement("button");
      kill.type = "button";
      kill.className = "kill";
      kill.textContent = "×";
      kill.title = "Delete " + pf.name;
      kill.setAttribute("aria-label", "Delete portfolio " + pf.name);
      kill.addEventListener("click", async (e) => {
        e.stopPropagation();
        closeMenu();
        const ok = await confirmDialog("Delete “" + pf.name + "”?",
          pf.count
            ? "Its " + pf.count + " holding" + (pf.count === 1 ? "" : "s") +
              " and the average prices you typed in are deleted for good. This cannot be undone."
            : "The portfolio is empty. This cannot be undone.",
          "Delete portfolio");
        if (ok) { POSITIONS = []; adopt(await api("/api/portfolios/delete", { id: pf.id })); }
      });
      item.appendChild(kill);
    }
    menu.appendChild(item);
  }

  const sep = document.createElement("div");
  sep.className = "pf-sep";
  menu.appendChild(sep);

  const rename = document.createElement("button");
  rename.type = "button";
  rename.className = "pf-item";
  rename.textContent = "Rename this portfolio…";
  rename.addEventListener("click", async () => {
    closeMenu();
    const name = await nameDialog("Rename portfolio", SERVER.active_name);
    if (name) adopt(await api("/api/portfolios/rename", { id: SERVER.active, name }));
  });

  const add = document.createElement("button");
  add.type = "button";
  add.className = "pf-item";
  add.textContent = "+ New portfolio…";
  add.addEventListener("click", async () => {
    closeMenu();
    const name = await nameDialog("New portfolio", "");
    if (name) { POSITIONS = []; adopt(await api("/api/portfolios", { name })); }
  });

  menu.append(rename, add);
}
function openMenu() {
  renderMenu();
  $("pf-menu").hidden = false;
  $("pf-button").setAttribute("aria-expanded", "true");
}
function closeMenu() {
  $("pf-menu").hidden = true;
  $("pf-button").setAttribute("aria-expanded", "false");
}

/* ---------- dialogs ---------- */
function confirmDialog(title, body, okLabel) {
  const dlg = $("dlg-confirm");
  $("dlg-confirm-title").textContent = title;
  $("dlg-confirm-body").textContent = body;
  $("confirm-ok").textContent = okLabel || "Delete";
  return new Promise(resolve => {
    dlg.addEventListener("close", () => resolve(dlg.returnValue === "ok"), { once: true });
    dlg.showModal();
  });
}
$("confirm-cancel").addEventListener("click", () => $("dlg-confirm").close("cancel"));

function nameDialog(title, value) {
  const dlg = $("dlg-name");
  $("dlg-name-title").textContent = title;
  const input = $("name-input");
  input.value = value || "";
  return new Promise(resolve => {
    dlg.addEventListener("close",
      () => resolve(dlg.returnValue === "ok" ? input.value.trim() : null), { once: true });
    dlg.showModal();
    input.focus();
    input.select();
  });
}
$("name-cancel").addEventListener("click", () => $("dlg-name").close("cancel"));
/* Enter must save: inside a dialog form the first submit button is what Enter
   presses, and that is Cancel. */
$("name-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && $("name-input").value.trim()) {
    e.preventDefault();
    $("dlg-name").close("ok");
  }
});

/* ---------- buy ----------
   The dialog does the arithmetic out loud before it is committed: a purchase
   moves the average price, and an average price that moved without you seeing
   it move is how a portfolio quietly stops matching the broker. */
function buyEffect() {
  const box = $("buy-effect");
  const err = $("buy-error");
  err.textContent = "";
  const ticker = $("buy-ticker").value.trim().toUpperCase();
  const qty = parseFloat($("buy-qty").value);
  const price = parseFloat($("buy-price").value);
  const held = POSITIONS.find(p => p.t === ticker);

  $("buy-name").parentElement.hidden = Boolean(held);
  if (!ticker) {
    box.textContent = "Type a ticker. One you already hold adds to it; a new one starts a position.";
    return null;
  }
  if (!Number.isFinite(qty) || qty <= 0 || !Number.isFinite(price) || price <= 0) {
    box.innerHTML = held
      ? "You hold <b>" + shares(held.s) + "</b> " + ticker + " at an average of <b>" +
        money(held.c) + "</b>. Enter a quantity and the price you paid."
      : ticker + " is not in this portfolio yet — this will start a position.";
    return null;
  }

  const cost = qty * price;
  if (held) {
    const newShares = held.s + qty;
    const newAvg = (held.s * held.c + cost) / newShares;
    box.innerHTML =
      "Paying <b>" + money(cost) + "</b> for " + shares(qty) + " " + ticker + ".<br>" +
      "Shares <span class=\"was\">" + shares(held.s) + "</span> → <b>" + shares(newShares) + "</b><br>" +
      "Average price <span class=\"was\">" + money(held.c) + "</span> → <b>" + money(newAvg) + "</b>";
  } else {
    box.innerHTML =
      "Paying <b>" + money(cost) + "</b> for " + shares(qty) + " " + ticker + ".<br>" +
      "New position, average price <b>" + money(price) + "</b>, target weight 0% until you set one.";
  }
  return { ticker, shares: qty, price, date: $("buy-date").value,
           name: $("buy-name").value.trim() };
}

function openBuy(prefillTicker) {
  const dlg = $("dlg-buy");
  const list = $("buy-tickers");
  list.textContent = "";
  for (const p of POSITIONS) {
    if (!p.t) continue;
    const opt = document.createElement("option");
    opt.value = p.t;
    opt.label = p.name;
    list.appendChild(opt);
  }
  $("buy-ticker").value = prefillTicker || "";
  $("buy-qty").value = "";
  $("buy-price").value = "";
  $("buy-name").value = "";
  $("buy-date").value = today();
  buyEffect();
  dlg.showModal();
  $("buy-ticker").focus();
}

/* The last price is a convenience, not the truth: it fills the field only
   while it is empty, and what you actually paid always wins. */
function suggestPrice() {
  const ticker = $("buy-ticker").value.trim().toUpperCase();
  const held = POSITIONS.find(p => p.t === ticker);
  const field = $("buy-price");
  if (held && held.price !== null && held.price !== undefined && !field.value)
    field.value = held.price;
}

["buy-ticker", "buy-qty", "buy-price"].forEach(id =>
  $(id).addEventListener("input", buyEffect));
$("buy-ticker").addEventListener("change", () => { suggestPrice(); buyEffect(); });
$("buy-cancel").addEventListener("click", () => $("dlg-buy").close("cancel"));
$("buy-close").addEventListener("click", () => $("dlg-buy").close("cancel"));
$("btn-buy").addEventListener("click", () => openBuy());

$("dlg-buy").addEventListener("close", async () => {
  if ($("dlg-buy").returnValue !== "ok") return;
  const order = buyEffect();
  if (!order) return;
  try {
    POSITIONS = [];                 // the buy may have started a position
    adopt(await api("/api/buy", order));
    jumpToRow(order.ticker);
  } catch (err) {
    $("snapshot-status").textContent = "Buy failed: " + err.message;
    $("snapshot-status").classList.add("bad");
  }
});

/* Submitting with an incomplete order would close the dialog and do nothing,
   so the form is held open until it describes a real purchase. */
$("buy-ok").addEventListener("click", (e) => {
  if (buyEffect()) return;
  e.preventDefault();
  $("buy-error").textContent = "Ticker, quantity and price are all needed.";
});

/* ---------- sell ----------
   Buying moves the average price; selling does not — it takes shares off the
   position at the price you already paid for them and turns the difference
   into realised profit, which is a separate number for good. The preview says
   all three things, because a seller who cannot see what was locked in is
   guessing at the only figure a sale actually produces. */
let sellBlocked = "";                 // why the order cannot go, for the footer

function sellEffect() {
  const box = $("sell-effect");
  sellBlocked = "";
  $("sell-error").textContent = "";
  const ticker = $("sell-ticker").value.trim().toUpperCase();
  const qty = parseFloat($("sell-qty").value);
  const price = parseFloat($("sell-price").value);
  const held = POSITIONS.find(p => p.t === ticker);

  if (!held) {
    box.textContent = "Nothing is held in this portfolio yet.";
    sellBlocked = "There is nothing to sell.";
    return null;
  }
  if (!Number.isFinite(qty) || qty <= 0 || !Number.isFinite(price) || price <= 0) {
    box.innerHTML = "You hold <b>" + shares(held.s) + "</b> " + ticker +
      " at an average of <b>" + money(held.c) +
      "</b>. Enter a quantity and the price you were paid.";
    sellBlocked = "Ticker, quantity and price are all needed.";
    return null;
  }
  /* The server refuses an oversell too, and says the same thing; this is here
     so the refusal arrives while the number can still be corrected. */
  if (qty > held.s + 1e-9) {
    box.innerHTML = "You hold only <b>" + shares(held.s) + "</b> " + ticker +
      " — " + shares(qty) + " cannot be sold.";
    sellBlocked = "You hold only " + shares(held.s) + " " + ticker + ".";
    return null;
  }

  const proceeds = qty * price;
  const gain = qty * (price - held.c);
  const cls = gain >= 0 ? "gain" : "loss";
  const left = held.s - qty;
  const whole = left < 1e-9;
  const prev = SERVER.realised || 0;
  const total = prev + gain;

  box.innerHTML =
    "Receiving <b>" + money(proceeds) + "</b> for " + shares(qty) + " " + ticker +
      " at " + money(price) + ".<br>" +
    "Realised on this sale <b class=\"" + cls + "\">" + money(gain, true) + "</b> " +
      "<span class=\"was\">(" + pct((price - held.c) / held.c * 100, 2) +
      " against the " + money(held.c) + " average)</span><br>" +
    (whole
      ? "This sells the position whole: " + ticker + " leaves the portfolio, and the " +
        "realised profit it produced stays.<br>"
      : "Shares <span class=\"was\">" + shares(held.s) + "</span> → <b>" + shares(left) +
        "</b> · average price unchanged at <b>" + money(held.c) + "</b><br>") +
    "Realised total <span class=\"was\">" + money(prev, prev !== 0) + "</span> → " +
      "<b class=\"" + (total >= 0 ? "gain" : "loss") + "\">" + money(total, total !== 0) + "</b>";
  return { ticker, shares: qty, price, date: $("sell-date").value };
}

function openSell(prefillTicker) {
  const dlg = $("dlg-sell");
  const list = $("sell-ticker");
  list.textContent = "";
  for (const p of POSITIONS) {
    if (!p.t) continue;
    const opt = document.createElement("option");
    opt.value = p.t;
    opt.textContent = p.t + " — " + p.name + " · " + shares(p.s);
    list.appendChild(opt);
  }
  if (prefillTicker) list.value = prefillTicker;
  $("sell-qty").value = "";
  $("sell-price").value = "";
  $("sell-date").value = today();
  sellEffect();
  dlg.showModal();
  list.focus();
}

/* Same bargain as the buy dialog's suggested price: the last quote is what a
   sale is most likely to have gone off at, and it yields to anything typed. */
function suggestSellPrice() {
  const held = POSITIONS.find(p => p.t === $("sell-ticker").value.trim().toUpperCase());
  const field = $("sell-price");
  if (held && held.price !== null && held.price !== undefined && !field.value)
    field.value = held.price;
}

["sell-qty", "sell-price"].forEach(id => $(id).addEventListener("input", sellEffect));
$("sell-ticker").addEventListener("change", () => { suggestSellPrice(); sellEffect(); });
$("sell-cancel").addEventListener("click", () => $("dlg-sell").close("cancel"));
$("sell-close").addEventListener("click", () => $("dlg-sell").close("cancel"));
$("btn-sell").addEventListener("click", () => openSell());

$("dlg-sell").addEventListener("close", async () => {
  if ($("dlg-sell").returnValue !== "ok") return;
  const order = sellEffect();
  if (!order) return;
  try {
    POSITIONS = [];                 // selling out closes a position for good
    adopt(await api("/api/sell", order));
    jumpToRow(order.ticker);
  } catch (err) {
    $("snapshot-status").textContent = "Sell failed: " + err.message;
    $("snapshot-status").classList.add("bad");
  }
});

/* An order that cannot be filled must not close the dialog, or the click looks
   like it worked and nothing happened — an oversell most of all. */
$("sell-ok").addEventListener("click", (e) => {
  if (sellEffect()) return;
  e.preventDefault();
  $("sell-error").textContent = sellBlocked;
});

/* ---------- record transaction ----------
   The Buy and Sell dialogs are for trades as they happen. This one is for the
   ledger: a trade from last year that was never entered, or a split, which is
   neither a buy nor a sell and has no price at all. Same three endpoints. */
function txFields() {
  const kind = $("tx-type").value;
  const split = kind === "split";
  $("tx-field-shares").hidden = split;
  $("tx-field-price").hidden = split;
  $("tx-field-fee").hidden = split;
  $("tx-field-ratio").hidden = !split;
  $("tx-submit").textContent = split ? "Record split" : "Record " + kind;
  return kind;
}

function txEffect() {
  const box = $("tx-maths");
  const kind = txFields();
  const ticker = $("tx-ticker").value.trim().toUpperCase();
  const held = POSITIONS.find(p => p.t === ticker);
  const date = $("tx-date").value;

  if (!ticker) {
    box.textContent = "Type a ticker.";
    return null;
  }

  if (kind === "split") {
    const ratio = parseFloat($("tx-ratio").value);
    if (!Number.isFinite(ratio) || ratio <= 0) {
      box.textContent = "How many shares each old share becomes — 10 for a 10-for-1 split.";
      return null;
    }
    box.innerHTML = held
      ? "Shares <span class=\"was\">" + shares(held.s) + "</span> → <b>" +
        shares(held.s * ratio) + "</b><br>Average price <span class=\"was\">" +
        money(held.c) + "</span> → <b>" + money(held.c / ratio) +
        "</b><br>No money moves: the position is worth exactly what it was worth."
      : ticker + " is not held — a split will be recorded and will apply to whatever " +
        "transactions you enter before it.";
    return { endpoint: "/api/split", body: { ticker, ratio, date } };
  }

  const qty = parseFloat($("tx-shares").value);
  const price = parseFloat($("tx-price").value);
  const fee = parseFloat($("tx-fee").value) || 0;
  if (!Number.isFinite(qty) || qty <= 0 || !Number.isFinite(price) || price <= 0) {
    box.textContent = "A quantity and the price per share are both needed.";
    return null;
  }

  /* A back-dated trade lands in the middle of the history, so what it does to
     the average price is not something this page can work out from the current
     position alone — the server replays the whole ledger. Promising a figure
     here that the fold might contradict would be worse than promising none. */
  const amount = qty * price + (kind === "buy" ? fee : -fee);
  const late = date && SERVER.transactions.some(t => t.date && t.date > date);
  box.innerHTML =
    (kind === "buy" ? "Paying " : "Receiving ") + "<b>" + money(amount) + "</b> for " +
    shares(qty) + " " + ticker + " at " + money(price) +
    (fee ? " · commission " + money(fee) : "") + ".<br>" +
    (late
      ? "This is dated before transactions already on file, so it is inserted into the " +
        "history and everything after it is recomputed."
      : kind === "sell"
        ? "A sale does not move the average price — the difference against it becomes " +
          "realised profit."
        : "The average price becomes the weighted average of this purchase and the ones " +
          "before it.");
  return {
    endpoint: kind === "buy" ? "/api/buy" : "/api/sell",
    body: { ticker, shares: qty, price, fee, date },
  };
}

function openTx() {
  const list = $("tx-known");
  list.textContent = "";
  for (const p of POSITIONS) {
    if (!p.t) continue;
    const opt = document.createElement("option");
    opt.value = p.t;
    opt.label = p.name;
    list.appendChild(opt);
  }
  $("tx-type").value = "buy";
  $("tx-ticker").value = "";
  $("tx-shares").value = "";
  $("tx-price").value = "";
  $("tx-ratio").value = "";
  $("tx-fee").value = "0";
  $("tx-date").value = today();
  txEffect();
  $("dlg-tx").showModal();
  $("tx-ticker").focus();
}

["tx-type", "tx-ticker", "tx-shares", "tx-price", "tx-fee", "tx-ratio", "tx-date"]
  .forEach(id => $(id).addEventListener("input", txEffect));
$("tx-type").addEventListener("change", txEffect);
$("btn-record-tx").addEventListener("click", openTx);

$("tx-submit").addEventListener("click", (e) => {
  if (txEffect()) return;
  e.preventDefault();                 // an incomplete row must not close the dialog
});

$("dlg-tx").addEventListener("close", async () => {
  if ($("dlg-tx").returnValue !== "ok") return;
  const order = txEffect();
  if (!order) return;
  try {
    POSITIONS = [];                   // the entry may open or close a position
    adopt(await api(order.endpoint, order.body));
    showTab("tx");
  } catch (err) {
    $("snapshot-status").textContent = "Not recorded: " + err.message;
    $("snapshot-status").classList.add("bad");
  }
});

/* ---------- price fetching ---------- */
async function fetchPrices() {
  const btn = $("btn-fetch");
  btn.disabled = true;
  btn.textContent = "Fetching…";
  try {
    adopt(await api("/api/prices/refresh", {}), { keepInputs: true });
  } catch (err) {
    $("snapshot-status").textContent = "Fetch failed: " + err.message;
    $("snapshot-status").classList.add("bad");
  } finally {
    btn.disabled = false;
    btn.textContent = "Fetch prices";
  }
}

/* The startup fetch is already running on the server while this page loads,
   so wait it out rather than making anyone press a button for it. */
async function followStartupFetch() {
  for (let i = 0; i < 20; i++) {
    if (!SERVER || SERVER.snapshot.status !== "fetching") return;
    await new Promise(r => setTimeout(r, 700));
    adopt(await api("/api/state"), { keepInputs: true });
  }
}

/* ---------- wiring ---------- */
wireSorting("positions", POSITION_COLUMNS, () => render({ keepInputs: true }));
wireSorting("tx", TX_COLUMNS, renderTransactions);
wireSorting("editor", EDITOR_COLUMNS, () => { renderEditor(); paintSortHeaders("editor"); });

function setAllocMode(withBonds) {
  allocWithBonds = withBonds;
  localStorage.setItem("allocWithBonds", withBonds ? "1" : "0");
  render({ keepInputs: true });
}
$("alloc-equities").addEventListener("click", () => setAllocMode(false));
$("alloc-wealth").addEventListener("click", () => setAllocMode(true));

$("btn-fetch").addEventListener("click", fetchPrices);
$("btn-add-row").addEventListener("click", addRow);
$("btn-add-company").addEventListener("click", addRow);
$("btn-edit-targets").addEventListener("click", () => showTab("rebalance"));
$("btn-preview").addEventListener("click", () => renderPreview(true));
document.querySelectorAll('input[name="mode"]').forEach(r =>
  r.addEventListener("change", () => { previewExpanded = false; renderPreview(false); }));
$("contrib").addEventListener("input", () => {
  previewExpanded = false;
  render({ keepInputs: true });
});
$("pf-button").addEventListener("click", () => {
  $("pf-menu").hidden ? openMenu() : closeMenu();
});
document.addEventListener("click", (e) => {
  if (!$("pf-menu").hidden && !e.target.closest(".switcher")) closeMenu();
});
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeMenu(); });
window.addEventListener("beforeunload", () => { if (saveTimer) saveHoldings(); });

/* The server cannot see a closed tab, so an open one checks in. Stop checking
   in — close the page — and it shuts itself down a while later. */
setInterval(() => { fetch("/api/ping").catch(() => {}); }, 60000);

(async function boot() {
  adopt(await api("/api/state"));
  followStartupFetch();
})();

"use strict";

/* Performance tab: three line charts in plain SVG — return against indices,
   money paid in against value, and profit over time. The curves come from
   /api/history (app/history.py); this file only draws them. Loaded after
   app.js, which owns TABS and showTab. */

const PERF_RATE = 5;          // the flat yearly-rate line, % — a "did I beat a savings account" reference
const BOX = { W: 1000, H: 420, L: 60, R: 84, T: 14, B: 34 };
const TEAL = "#1BC5BD", RED = "#F64E60";
let perfData = null;
const hidden = { perf: new Set(), val: new Set(), pl: new Set() };   // legend toggles, per chart

TABS.push("performance");
$("tab-performance").addEventListener("click", () => { showTab("performance"); loadPerformance(); });

/* What the charts were drawn from. A fetch, a trade or a portfolio switch changes
   it, and render() asks refreshPerformance() so an open tab never shows old data. */
let perfStamp = "";
const stampNow = () => [SERVER.active, SERVER.transactions.length, SERVER.snapshot.fetched_at].join("|");
function refreshPerformance() {
  if (!$("panel-performance").hidden && stampNow() !== perfStamp) loadPerformance();
}

async function loadPerformance() {
  perfStamp = stampNow();
  $("perf-note").textContent = "Loading price history…";
  try {
    perfData = await api("/api/history");
    renderPerformance();
  } catch (err) {
    $("perf-note").textContent = "Could not load price history: " + err.message;
  }
}

function niceStep(span) {
  const raw = span / 6, mag = 10 ** Math.floor(Math.log10(raw)), n = raw / mag;
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * mag;
}

const plain = (v) => Math.round(v * 100) / 100;
const compact = (v) => v.toLocaleString("en-US", { maximumFractionDigits: 0 });

/* cfg: id prefix, dates, lines [{key,label,color,width,dash,values}], fmt for the
   tooltip, end for the label at the end of a line, and `split` to colour a single
   line by the sign of its values. */
function drawChart(cfg) {
  const { W, H, L, R, T, B } = BOX, id = cfg.id, svg = $(id + "-chart"), n = cfg.dates.length;
  const shown = cfg.lines.filter(l => !hidden[id].has(l.key));
  const all = shown.flatMap(l => l.values).filter(v => v !== null).concat(cfg.floor ?? []);
  const step = niceStep(Math.max(...all) - Math.min(...all) || 1);
  const lo = Math.floor(Math.min(...all) / step) * step, hi = Math.ceil(Math.max(...all) / step) * step;
  const x = (i) => L + (W - L - R) * (n > 1 ? i / (n - 1) : 0);
  const y = (v) => T + (H - T - B) * (1 - (v - lo) / (hi - lo));

  let out = "";
  if (cfg.split) {
    const z = Math.min(1, Math.max(0, (y(0) - T) / (H - T - B)));
    out += `<defs><linearGradient id="${id}-sign" gradientUnits="userSpaceOnUse" x1="0" x2="0" y1="${T}" y2="${H - B}">` +
           `<stop offset="${z}" stop-color="${TEAL}"/><stop offset="${z}" stop-color="${RED}"/></linearGradient></defs>`;
  }
  for (let v = lo; v <= hi + step / 2; v += step) {
    out += `<line class="${Math.abs(v) < step / 2 ? "zero" : "grid"}" x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}"/>` +
           `<text class="tick" x="${L - 8}" y="${y(v) + 4}" text-anchor="end">${compact(plain(v))}</text>`;
  }
  const every = Math.ceil(n / 8);
  for (let i = 0; i < n; i += every) {
    out += `<text class="tick" x="${x(i)}" y="${H - 10}" text-anchor="middle">${cfg.dates[i].slice(2)}</text>`;
  }
  for (const l of shown) {
    let path = "", pen = false;
    l.values.forEach((v, i) => {
      if (v === null) { pen = false; return; }
      path += (pen ? "L" : "M") + x(i).toFixed(1) + " " + y(v).toFixed(1);
      pen = true;
    });
    out += `<path d="${path}" fill="none" stroke="${cfg.split ? `url(#${id}-sign)` : l.color}" stroke-width="${l.width || 1.6}"` +
           (l.dash ? ` stroke-dasharray="${l.dash}"` : "") + ` stroke-linejoin="round"/>`;
  }
  // End-of-line labels, nudged apart so that close finishes stay readable.
  const ends = shown.map(l => ({ l, v: l.values.at(-1), y: y(l.values.at(-1)) })).filter(e => e.v !== null)
    .sort((a, b) => a.y - b.y);
  ends.forEach((e, i) => { if (i && e.y < ends[i - 1].y + 18) e.y = ends[i - 1].y + 18; });
  for (const e of ends) {
    const fill = cfg.split ? (e.v < 0 ? RED : TEAL) : e.l.color;
    out += `<rect x="${W - R + 6}" y="${e.y - 10}" width="${R - 8}" height="20" rx="5" fill="${fill}"/>` +
           `<text x="${W - R + 6 + (R - 8) / 2}" y="${e.y + 4}" text-anchor="middle" fill="#fff" font-size="12" font-weight="700">${cfg.end(e.v)}</text>`;
  }
  out += `<circle id="${id}-dot" r="5" fill="#fff" stroke-width="2" visibility="hidden"/>`;
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.innerHTML = out;

  const legend = $(id + "-legend");
  legend.textContent = "";
  for (const l of cfg.lines) {
    const b = document.createElement("button");
    b.type = "button";
    b.setAttribute("aria-pressed", hidden[id].has(l.key) ? "false" : "true");
    b.innerHTML = `<i style="background:${l.color}"></i>`;
    b.append(l.label);
    b.addEventListener("click", () => { hidden[id].has(l.key) ? hidden[id].delete(l.key) : hidden[id].add(l.key); drawChart(cfg); });
    legend.append(b);
  }

  // The tip appears only while the pointer is on a line, and follows the pointer.
  const tip = $(id + "-tip"), dot = $(id + "-dot"), wrap = svg.parentElement;
  const leave = () => { dot.setAttribute("visibility", "hidden"); tip.dataset.on = "0"; };
  svg.onmousemove = (ev) => {
    const box = svg.getBoundingClientRect();
    const px = (ev.clientX - box.left) / box.width * W, py = (ev.clientY - box.top) / box.height * H;
    const i = Math.max(0, Math.min(n - 1, Math.round((px - L) / (W - L - R) * (n - 1))));
    const near = shown.filter(l => l.values[i] !== null)
      .map(l => ({ l, d: Math.abs(y(l.values[i]) - py) })).sort((a, b) => a.d - b.d)[0];
    if (!near || near.d > 12 || px < L || px > W - R) return leave();
    dot.setAttribute("cx", x(i)); dot.setAttribute("cy", y(near.l.values[i]));
    dot.setAttribute("stroke", cfg.split ? TEAL : near.l.color); dot.setAttribute("visibility", "visible");
    tip.innerHTML = `<b>${cfg.dates[i]}</b>` + shown.filter(l => l.values[i] !== null).map(l =>
      `<div class="perf-tip-row"><span><i style="background:${l.color};display:inline-block"></i> ` +
      `${l === near.l && shown.length > 1 ? "&gt;&gt; " : ""}${l.label}</span><span>${cfg.fmt(l.values[i])}</span></div>`).join("");
    const at = wrap.getBoundingClientRect(), left = ev.clientX - at.left;
    tip.style.left = left + "px";
    tip.style.top = (ev.clientY - at.top) + "px";
    tip.style.transform = left > at.width / 2 ? "translate(calc(-100% - 14px), 14px)" : "translate(14px, 14px)";
    tip.dataset.on = "1";
  };
  svg.onmouseleave = leave;
}

function renderPerformance() {
  const d = perfData, note = [];
  if (!d.dates.length) {
    for (const id of ["perf", "val", "pl"]) { $(id + "-chart").textContent = ""; $(id + "-legend").textContent = ""; }
    $("perf-note").textContent = "Nothing to chart yet: it needs a purchase of a listed company.";
    return;
  }
  const t0 = Date.parse(d.dates[0]);
  const rate = d.dates.map(x => ((1 + PERF_RATE / 100) ** ((Date.parse(x) - t0) / 864e5 / 365) - 1) * 100);
  const returns = [
    { key: "portfolio", label: "Your portfolio", color: "var(--blue)", width: 2.6, values: d.portfolio },
    { key: "S&P 500", label: "S&P 500", color: TEAL },
    { key: "NASDAQ 100", label: "NASDAQ 100", color: RED },
    { key: "WIG20", label: "WIG20", color: "#FFA800" },
    { key: "rate", label: "Flat " + PERF_RATE + "% a year", color: "var(--muted)", dash: "5 4", values: rate },
  ].map(l => ({ ...l, values: l.values || d.benchmarks[l.key] })).filter(l => l.values);

  drawChart({ id: "perf", dates: d.dates, lines: returns, floor: 0, fmt: (v) => pct(v), end: (v) => v.toFixed(2) });
  drawChart({ id: "val", dates: d.dates, floor: 0, fmt: money, end: compact, lines: [
    { key: "value", label: "Portfolio value", color: TEAL, width: 2.2, values: d.value },
    { key: "invested", label: "Money paid in", color: "var(--blue)", width: 2.2, values: d.invested },
  ] });
  drawChart({ id: "pl", dates: d.dates, floor: 0, split: true, fmt: (v) => money(v, true), end: compact, lines: [
    { key: "profit", label: "Profit since first purchase", color: TEAL, width: 2.2, values: d.value.map((v, i) => v - d.invested[i]) },
  ] });

  if (d.parked.length) note.push(`${d.parked.join(", ")} left out: a bond has no price history.`);
  if (d.left_out.length) note.push(`${d.left_out.join(", ")} left out: no price history from the source.`);
  if (d.missing_benchmarks.length) note.push(`${d.missing_benchmarks.join(", ")} unavailable right now.`);
  note.push("Benchmarks start from the close before your first purchase; WIG20 is converted to dollars at the ECB rate. The portfolio line is the price return, without dividends; it is not the CAGR on the Overview.");
  $("perf-note").textContent = note.join(" ");
}

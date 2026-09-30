/* Company view: one company at a time — its position, its company profile if
   there is one, and earnings-call transcripts on request.

   Loaded after app.js and leans on it: $, api, SERVER, computed, money, pct,
   TABS, showTab. The profile is a JSON file the app only displays; it is
   written elsewhere (by hand, or by whatever research workflow you use), which
   is why every value from it is escaped (strings) or coerced with Number()
   (figures) before it touches the page.

   Charts are hand-drawn SVG: no library, nothing fetched, works offline. */

let COMPANY = null;           // ticker on screen
let PROFILE = null;           // its profile, null when it has none, undefined while loading
let TRANSCRIPT = null;        // {overview, doc, query, busy, error}

const esc = (s) => String(s === null || s === undefined ? "" : s)
  .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
  .replace(/"/g, "&quot;").replace(/'/g, "&#39;");

/* A figure from the profile, as a number whatever the file holds. */
const num = (v) => { const n = Number(v); return Number.isFinite(n) ? n : 0; };
const numOrNull = (v) => (v === null || v === undefined || v === "" ? null : num(v));

/* Millions in, "$29.6B" out. */
const billions = (millions, dp) => "$" + (num(millions) / 1000).toFixed(dp === undefined ? 1 : dp) + "B";

TABS.push("company");
$("tab-company").addEventListener("click", () => showTab("company"));

async function openCompany(ticker) {
  COMPANY = ticker;
  PROFILE = undefined;
  TRANSCRIPT = { overview: null, doc: null, query: "", busy: false, error: "" };
  $("tab-company").hidden = false;
  $("tab-company-ticker").textContent = ticker;
  showTab("company");
  window.scrollTo({ top: 0, behavior: reducedMotion ? "auto" : "smooth" });
  renderCompany();
  const [company, overview] = await Promise.all([
    api("/api/company?ticker=" + encodeURIComponent(ticker)).catch(() => ({ profile: null })),
    fetchOverview(ticker),
  ]);
  if (COMPANY !== ticker) return;               // another company was opened meanwhile
  PROFILE = company.profile || null;
  TRANSCRIPT.overview = overview;
  renderCompany();
  // A saved transcript opens by itself — reading the cache costs nothing.
  if (overview && overview.cached.length) loadTranscript(overview.cached[0], false);
}

/* Called by app.js after every render: demo banner, and live figures on an
   open company view (prices move; the rest of the view stays put). */
function afterRender(state) {
  $("demo-banner").hidden = !(SERVER && SERVER.demo);
  if (COMPANY && $("co-position")) $("co-position").innerHTML = positionTiles(state);
}

/* ------------------------------------------------------------------ view */
function renderCompany() {
  const panel = $("panel-company");
  const state = computed();
  const row = state.rows.find(r => r.t === COMPANY);
  const name = PROFILE ? PROFILE.name : (row ? row.name : COMPANY);
  const status = PROFILE && PROFILE.status;
  const profileNote = PROFILE === undefined ? "" :
    PROFILE ? " · company profile as of " + esc(PROFILE.as_of) : " · no company profile";

  panel.innerHTML =
    '<section class="card co-head">' +
      '<div class="co-head-top">' +
        '<span class="co-avatar lg" style="background:' + (row ? esc(row.col) : "var(--blue)") + '">' + esc(COMPANY.slice(0, 2)) + "</span>" +
        '<div><div class="pf-title">' + esc(name) + '</div><div class="pf-sub"><span class="tkr">' + esc(COMPANY) + "</span>" +
          profileNote + "</div></div>" +
        (status ? statusBadge(status.value, status.label) : "") +
        '<button class="btn co-back" type="button" id="co-back">← Overview</button>' +
      "</div>" +
      (PROFILE ? '<p class="co-headline">' + esc(PROFILE.headline) + "</p>" : "") +
    "</section>" +
    '<div class="kpi-row co-kpis" id="co-position">' + positionTiles(state) + "</div>" +
    (PROFILE === undefined ? "" : PROFILE ? profileSections(PROFILE) : noProfile()) +
    '<section class="card co-section" id="co-transcript"></section>';

  $("co-back").addEventListener("click", () => showTab("overview"));
  renderTranscript();
}

/* Price, weight against target, profit — and the thesis status, which is the
   profile's verdict on the same position. */
function positionTiles(state) {
  const r = state.rows.find(x => x.t === COMPANY);
  let tiles;
  if (!r) {
    tiles = tile("Position", "not held", "this company is not in the active portfolio");
  } else {
    const weight = r.w !== null && r.w !== undefined ? r.w.toFixed(2) + "%" : "—";
    const target = r.parked || r.target === null || r.target === undefined ? "no target" : r.target.toFixed(2) + "%";
    const drift = r.d === null || r.d === undefined ? "no drift to show"
      : "drift " + pct(r.d, 2).replace("%", " pp") + (r.d > 0 ? " · over target" : r.d < 0 ? " · under target" : " · on target");
    tiles = tile("Last price", r.priced ? money(r.price) : "no quote", r.priced ? (r.source || "") : "fetch prices to value it") +
      tile("Weight / target", weight + " / " + target, drift) +
      tile("Unrealised profit", r.pl === null ? "—" : money(r.pl, true),
           r.basis ? pct(r.pl / r.basis * 100, 2) + " vs your average price" : "");
  }
  return tiles + statusTile();
}

function statusTile() {
  if (PROFILE === undefined) return tile("Thesis status", "…", "loading the company profile");
  const s = PROFILE && PROFILE.status;
  if (!s) return tile("Thesis status", "—", PROFILE ? "the profile gives no status" : "no company profile");
  const sub = [s.reviewed ? "reviewed " + s.reviewed : "", s.next_review ? "next review " + s.next_review : ""]
    .filter(Boolean).join(" · ");
  return tile("Thesis status", s.label || s.value, sub, "kpi-status " + (STATUS_CLASS[s.value] || ""));
}

/* Every argument is escaped here, so callers pass plain text. */
function tile(label, value, sub, cls) {
  return '<div class="card kpi' + (cls ? " " + esc(cls) : "") + '"><div class="kpi-label">' + esc(label) + "</div>" +
    '<div class="kpi-value num">' + esc(value) + '</div><div class="kpi-sub">' + esc(sub || "") + "</div></div>";
}

const STATUS_CLASS = { confirmed: "ok", weakened: "warn", decide: "bad", broken: "bad" };
function statusBadge(value, label) {
  return '<span class="status-badge ' + (STATUS_CLASS[value] || "") + '">' + esc(label || value) + "</span>";
}

function noProfile() {
  return '<section class="card co-section co-empty"><div class="card-title">No company profile for ' + esc(COMPANY) + "</div>" +
    '<p class="hint">Profiles are optional. A company gets one when a <code>' + esc(COMPANY) +
    '.json</code> file sits in the <code>companies/</code> folder next to the portfolio data — see the demo’s AVGO for the shape. ' +
    "Transcripts below work for every company.</p></section>";
}

function section(title, sub, inner, cls) {
  return '<section class="card co-section' + (cls ? " " + cls : "") + '"><div class="co-section-head"><span class="card-title">' +
    esc(title) + "</span>" + (sub ? '<span class="hint">' + esc(sub) + "</span>" : "") + "</div>" + inner + "</section>";
}

function chartCard(c, svg, wide) {
  return '<section class="card co-chart' + (wide ? " wide" : "") + '"><div class="co-section-head"><span class="card-title">' +
    esc(c.title) + '</span><span class="hint">' + esc(c.subtitle || "") + "</span></div>" + svg +
    (c.note ? '<p class="co-note">' + esc(c.note) + "</p>" : "") +
    '<p class="co-source">Source: ' + esc(c.source || "") + "</p></section>";
}

function profileSections(p) {
  const ch = p.charts || {};
  const thesis = p.thesis || {};
  const kpiList = p.kpis || [];
  // A kpi entry {"from": "scorecard"} marks where the scorecard tile goes;
  // without one, a profile that has a scorecard gets the tile at the end.
  const kpis = kpiList.map(k => k.from === "scorecard" ? (p.scorecard ? scorecardTile(p.scorecard) : "")
                                                       : tile(k.label, k.value, k.sub, "kpi-soft")).join("") +
    (p.scorecard && !kpiList.some(k => k.from === "scorecard") ? scorecardTile(p.scorecard) : "");
  const pillars = (thesis.pillars || []).map((x, i) =>
    '<div class="pillar"><span class="pillar-n">' + (i + 1) + "</span><b>" + esc(x.title) + "</b><p>" + esc(x.text) + "</p></div>").join("");
  const requires = (thesis.requires || []).map(x =>
    '<div class="req"><b>' + esc(x.title) + "</b><p>" + esc(x.text) + "</p></div>").join("");

  return '<div class="kpi-row co-kpis">' + kpis + "</div>" +
    section("Thesis", "why hold it" + (p.status && p.status.reviewed ? " · reviewed " + p.status.reviewed : ""),
      '<p class="co-lede">' + esc(thesis.summary) + '</p><div class="pillars">' + pillars + "</div>" +
      (p.status && p.status.next_checkpoint ? '<div class="checkpoint"><span>Next checkpoint</span> ' + esc(p.status.next_checkpoint) + "</div>" : "")) +
    (p.hidden_assumption ? assumptionCard(p.hidden_assumption) : "") +
    '<div class="co-grid">' +
      (ch.quarterly_revenue ? chartCard(ch.quarterly_revenue, quarterlyChart(ch.quarterly_revenue), true) : "") +
      (ch.segments ? chartCard(ch.segments, segmentsChart(ch.segments)) : "") +
      (ch.operating_margin ? chartCard(ch.operating_margin, simpleBars(ch.operating_margin, "period", v => v.toFixed(1) + "%", { highlightLast: true, dip: true })) : "") +
      (ch.annual_revenue ? chartCard(ch.annual_revenue, annualChart(ch.annual_revenue)) : "") +
      (ch.debt_maturity ? chartCard(ch.debt_maturity, simpleBars(ch.debt_maturity, "year", v => v ? billions(v) : "$0", { tone: "purple" })) : "") +
    "</div>" +
    (p.scorecard ? section("Scorecard", p.scorecard.method, scorecard(p.scorecard)) : "") +
    section("What has to stay true", "the conditions the thesis rests on",
      '<div class="reqs">' + requires + "</div>" + invalidationChecklist(p.invalidation || [], p.as_of)) +
    section("Risks", "what could make this wrong",
      '<div class="risks">' + (p.risks || []).map(riskCard).join("") + "</div>" + disputes(p.disputes || [])) +
    (p.call ? callCard(p.call) : "") +
    '<p class="co-disclaimer">' + esc(p.disclaimer || "") + " " +
      (p.sources || []).map(s => esc(s.doc) + " (" + esc(s.id) + ", " + esc(s.date) + ")").join(" · ") + "</p>";
}

/* -------------------------------------------------------------- sections */

/* The headline layer's result as the value, the other layers as the sub-line:
   "W2 92/100" over "W1 9/9 · W4 17/25 · W3 0.15". Built from the layers, so
   it cannot disagree with the scorecard section below. */
function scorecardTile(s) {
  const layers = s.layers || [];
  const isRatio = l => l.ratio !== undefined;
  const result = l => l.id + " " + (isRatio(l) ? num(l.ratio).toFixed(2) : num(l.score) + "/" + num(l.max));
  const scored = layers.filter(l => !isRatio(l));
  const head = layers.find(l => l.id === s.headline) ||
    scored.slice().sort((a, b) => num(b.max) - num(a.max))[0] || layers[0];
  if (!head) return "";
  const rest = scored.filter(l => l !== head).concat(layers.filter(l => isRatio(l) && l !== head));
  return tile("Scorecard", result(head), rest.map(result).join(" · "), "kpi-soft");
}

function assumptionCard(a) {
  const gw = num(a.gw), perLow = num(a.per_gw_low), perHigh = num(a.per_gw_high), guided = num(a.guided);
  const unit = a.capacity_unit || "units";
  const low = gw * perLow, high = gw * perHigh;
  const coversLow = perHigh ? guided / perHigh : 0, coversHigh = perLow ? guided / perLow : 0;
  const max = niceMax(Math.max(high, guided, 1) * 1.05);
  const W = 640, L = 150, R = 24, x = v => L + (W - L - R) * v / max;
  const rows = [
    { label: "Guided", sub: a.guided_label || "company guidance", from: 0, to: guided, cls: "g-guided", text: "$" + guided + "B" },
    { label: gw + " " + unit + " × content", sub: "$" + perLow + "–" + perHigh + "B per " + unit, from: low, to: high, cls: "g-implied", text: "$" + low + "–" + high + "B" },
  ];
  let svg = '<svg class="chart" viewBox="0 0 ' + W + ' 150" role="img" aria-label="Guided revenue compared with the capacity management described">';
  svg += '<defs><pattern id="hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="6" height="6" class="hatch-bg"/><line x1="0" y1="0" x2="0" y2="6" class="hatch-line"/></pattern></defs>';
  for (let i = 0; i <= 4; i++) {
    const v = max * i / 4;
    svg += '<line class="grid" x1="' + x(v) + '" x2="' + x(v) + '" y1="10" y2="112"/><text class="axis" x="' + x(v) + '" y="128" text-anchor="middle">$' + v.toFixed(0) + "B</text>";
  }
  rows.forEach((r, i) => {
    const y = 20 + i * 48;
    svg += '<text class="lbl strong" x="0" y="' + (y + 13) + '">' + esc(r.label) + '</text><text class="lbl" x="0" y="' + (y + 29) + '">' + esc(r.sub) + "</text>";
    if (r.from > 0) svg += '<rect class="g-reach" x="' + x(0) + '" y="' + y + '" width="' + (x(r.from) - x(0)) + '" height="32" rx="4"/>';
    svg += '<rect class="' + r.cls + '" x="' + x(r.from) + '" y="' + y + '" width="' + Math.max(0, x(r.to) - x(r.from)) + '" height="32" rx="4"><title>' + esc(r.text) + "</title></rect>";
    const inside = x(r.to) + 6 > W - 70;
    svg += '<text class="val" x="' + (inside ? x(r.to) - 6 : x(r.to) + 6) + '" y="' + (y + 21) + '" text-anchor="' + (inside ? "end" : "start") + '">' + esc(r.text) + "</text>";
  });
  svg += '<text class="axis" x="' + L + '" y="146">' + esc(a.unit) + "</text></svg>";

  // One square per unit of capacity: how many the guidance pays for.
  const count = Math.max(0, Math.min(Math.round(gw), 120));
  let cells = "";
  for (let i = 1; i <= count; i++) {
    const cls = i <= Math.floor(coversLow) ? "on" : i <= Math.ceil(coversHigh) ? "half" : "";
    cells += '<i class="' + cls + '" title="' + esc(unit + " " + i) + '"></i>';
  }
  return '<section class="card co-section co-insight">' +
    '<div class="co-section-head"><span class="insight-tag">Reading between the lines</span><span class="card-title">' + esc(a.title) + "</span></div>" +
    '<div class="insight-body"><div>' + svg +
      '<div class="gw-strip" style="grid-template-columns:repeat(' + Math.max(count, 1) + ',1fr)" aria-label="' + esc("Capacity (" + unit + ") covered by guidance") + '">' + cells + "</div>" +
      '<div class="gw-key"><span><i class="on"></i>' + esc("paid for by guidance at $" + perHigh + "B/" + unit + " (≈" + Math.floor(coversLow) + " " + unit + ")") +
      '</span><span><i class="half"></i>' + esc("at $" + perLow + "B/" + unit + " (≈" + Math.round(coversHigh) + " " + unit + ")") +
      "</span><span><i></i>line of sight, not in guidance</span></div></div>" +
    '<p class="co-lede">' + esc(a.text) + "</p></div>" +
    '<p class="co-source">Source: ' + esc(a.source) + "</p></section>";
}

function scorecard(s) {
  return '<div class="layers">' + (s.layers || []).map(l => {
    if (l.ratio !== undefined) {
      const ratio = num(l.ratio), bench = num(l.benchmark);
      const scale = Math.max(bench * 1.2, ratio * 1.2) || 1;
      return '<div class="layer"><div class="layer-top"><b>' + esc(l.id) + " · " + esc(l.name) + '</b><span class="num">' + ratio.toFixed(2) +
        '</span></div><div class="layer-what">' + esc(l.what) + "</div>" +
        '<div class="ratio-track"><i class="ratio-good" style="width:' + (bench / scale * 100) + '%"></i>' +
        '<span class="ratio-mark" style="left:' + (ratio / scale * 100) + '%" title="' + ratio + '"></span>' +
        '<span class="ratio-bench" style="left:' + (bench / scale * 100) + '%">benchmark ' + bench + "</span></div></div>";
    }
    const score = num(l.score), max = num(l.max);
    const share = max ? Math.max(0, Math.min(score / max, 1)) : 0;
    return '<div class="layer"><div class="layer-top"><b>' + esc(l.id) + " · " + esc(l.name) + '</b><span class="num">' + score + " / " + max +
      '</span></div><div class="layer-what">' + esc(l.what) + "</div>" +
      '<div class="layer-track"><i class="' + (share >= 0.85 ? "ok" : share >= 0.6 ? "mid" : "low") + '" style="width:' + (share * 100) + '%"></i></div></div>';
  }).join("") + "</div>";
}

/* One box per condition. Unchecked means not triggered as of the profile
   date; a condition marked "triggered": true in the file shows ticked. */
function invalidationChecklist(rows, asOf) {
  if (!rows.length) return "";
  const hit = rows.filter(r => r.triggered === true).length;
  const summary = hit ? hit + " of " + rows.length + " triggered as of " + asOf + "." : "None triggered as of " + asOf + ".";
  return '<div class="co-subhead">It stops being true if…</div>' +
    '<ul class="inval-list">' + rows.map(r => {
      const on = r.triggered === true;
      return '<li class="inval-item' + (on ? " triggered" : "") + '">' +
        '<span class="inval-box" role="img" aria-label="' + (on ? "triggered" : "not triggered") + '">' + (on ? "✓" : "") + "</span>" +
        '<div class="inval-text"><div>' + esc(r.condition) + '</div><div class="hint">Check: ' + esc(r.where) + "</div></div>" +
        '<span class="inval-effect"><span class="hint">thesis</span> ' + statusBadge(r.effect, r.effect) + "</span></li>";
    }).join("") + "</ul>" +
    '<p class="inval-summary">' + esc(summary) + "</p>";
}

function riskCard(r) {
  return '<div class="risk"><div class="risk-metric num">' + esc(r.metric) + '</div><div class="risk-metric-label">' + esc(r.metric_label) +
    "</div><b>" + esc(r.title) + "</b><p>" + esc(r.text) + "</p></div>";
}

/* Questions the facts do not settle: two readings side by side, and what
   would have to be known to decide between them. */
function disputes(list) {
  if (!list.length) return "";
  const reading = (r, tag) => '<div class="reading"><span class="reading-tag">' + tag + "</span><b>" + esc(r && r.label) + "</b><p>" + esc(r && r.text) + "</p></div>";
  return '<div class="co-subhead disputes-head">Open disputes — two readings of the same facts</div><div class="disputes">' +
    list.map(d => '<div class="dispute"><div class="dispute-q">' + esc(d.question) + "</div>" +
      '<div class="readings">' + reading(d.reading_a, "Reading A") + reading(d.reading_b, "Reading B") + "</div>" +
      (d.unknown ? '<p class="dispute-unknown"><span>What is not known</span> ' + esc(d.unknown) + "</p>" : "") + "</div>").join("") +
    "</div>";
}

function callCard(c) {
  const quotes = c.quotes || [];
  return section("Earnings call — " + c.quarter, "held " + c.date + " · what was said, in short",
    '<div class="call"><ol class="takeaways">' + (c.takeaways || []).map(t => "<li>" + esc(t) + "</li>").join("") + "</ol>" +
    (quotes.length ? '<div class="call-quotes">' + quotes.map(q =>
      '<blockquote class="call-quote">“' + esc(q.text) + '”<cite>' + esc(q.who) + "</cite></blockquote>").join("") + "</div>" : "") +
    "</div>");
}

/* ---------------------------------------------------------------- charts */
function niceMax(v) {
  if (!(v > 0)) return 1;
  const p = Math.pow(10, Math.floor(Math.log10(v)));
  // Every step divides by four cleanly, so the gridlines land on round numbers.
  for (const m of [1, 1.2, 1.6, 2, 2.4, 3.2, 4, 4.8, 6, 8, 10]) if (m * p >= v) return m * p;
  return 10 * p;
}

function quarterlyChart(c) {
  const pts = (c.points || []).map(p => ({ q: String(p.q || ""), rev: num(p.rev), gm: numOrNull(p.gm),
                                           guidance: p.guidance === true, computed: p.computed === true }));
  if (!pts.length) return "";
  const W = 1120, H = 300, L = 52, R = 44, T = 26, B = 46;
  const ymax = niceMax(Math.max(...pts.map(p => p.rev)) * 1.08);
  const gms = pts.map(p => p.gm).filter(v => v !== null);
  const gmin = gms.length ? Math.floor(Math.min(...gms) / 5) * 5 - 5 : 0;
  const gmax = gms.length ? Math.ceil(Math.max(...gms) / 5) * 5 + 5 : 100;
  const step = (W - L - R) / pts.length, bw = step * 0.62;
  const y = v => T + (H - T - B) * (1 - v / ymax);
  const yMargin = v => T + (H - T - B) * (1 - (v - gmin) / (gmax - gmin));
  let s = '<svg class="chart" viewBox="0 0 ' + W + " " + H + '" role="img" aria-label="' + esc(c.title) + '">';
  for (let i = 0; i <= 4; i++) {
    const v = ymax * i / 4;
    s += '<line class="grid" x1="' + L + '" x2="' + (W - R) + '" y1="' + y(v) + '" y2="' + y(v) + '"/>' +
      '<text class="axis" x="' + (L - 8) + '" y="' + (y(v) + 4) + '" text-anchor="end">' + billions(v, 0) + "</text>";
    const g = gmin + (gmax - gmin) * i / 4;
    s += '<text class="axis gm" x="' + (W - R + 8) + '" y="' + (yMargin(g) + 4) + '">' + g.toFixed(0) + "%</text>";
  }
  let line = "", shownYear = "";
  pts.forEach((p, i) => {
    const cx = L + step * i + step / 2, x0 = cx - bw / 2;
    const cls = p.guidance ? "bar forecast" : p.computed ? "bar computed" : "bar";
    const tip = p.q + ": " + billions(p.rev) + (p.gm !== null ? " · gross margin " + p.gm + "%" : "") +
      (p.guidance ? " · company guidance" : p.computed ? " · computed: year minus three quarters" : "");
    s += '<rect class="' + cls + '" x="' + x0 + '" y="' + y(p.rev) + '" width="' + bw + '" height="' + (y(0) - y(p.rev)) + '" rx="3"><title>' + esc(tip) + "</title></rect>";
    // The last two bars get their figure: above a guidance bar (it has no dot
    // to collide with), inside a reported one (the margin line runs above it).
    if (i >= pts.length - 2) s += p.guidance
      ? '<text class="val" x="' + cx + '" y="' + (y(p.rev) - 7) + '" text-anchor="middle">' + billions(p.rev) + " guided</text>"
      : '<text class="inbar" x="' + cx + '" y="' + (y(p.rev) + 18) + '" text-anchor="middle">' + billions(p.rev) + "</text>";
    const [q, year] = p.q.split(" ");
    s += '<text class="axis" x="' + cx + '" y="' + (H - B + 16) + '" text-anchor="middle">' + esc(q) + "</text>";
    if (year !== shownYear) { shownYear = year; s += '<text class="axis strong" x="' + x0 + '" y="' + (H - B + 32) + '">' + esc(year) + "</text>"; }
    if (p.gm !== null) {
      line += (line && pts[i - 1] && pts[i - 1].gm !== null ? " L" : " M") + cx.toFixed(1) + " " + yMargin(p.gm).toFixed(1);
    }
  });
  s += '<path class="gm-line" d="' + line.trim() + '"/>';
  pts.forEach((p, i) => {
    if (p.gm === null) return;
    const cx = L + step * i + step / 2;
    s += '<circle class="gm-dot" cx="' + cx + '" cy="' + yMargin(p.gm) + '" r="3.5"><title>' + esc(p.q + ": gross margin " + p.gm + "%") + "</title></circle>";
  });
  s += "</svg>" +
    '<div class="chart-key"><span><i class="k-bar"></i>revenue</span><span><i class="k-computed"></i>computed Q4</span>' +
    '<span><i class="k-forecast"></i>guidance</span><span><i class="k-line"></i>gross margin, right axis</span></div>';
  return s;
}

function segmentsChart(c) {
  const series = (c.series || []).map(String);
  const pts = (c.points || []).map(p => ({ period: String(p.period || ""), values: (p.values || []).map(num) }));
  if (!pts.length) return "";
  const W = 520, L = 62, R = 16, rowH = 38, gap = 22, T = 10;
  const totals = pts.map(p => p.values.reduce((a, b) => a + b, 0));
  const max = niceMax(Math.max(...totals));
  const x = v => L + (W - L - R) * v / max;
  const H = T + pts.length * (rowH + gap) + 16;
  const tones = ["seg-a", "seg-b", "seg-c"];
  let s = '<svg class="chart" viewBox="0 0 ' + W + " " + H + '" role="img" aria-label="' + esc(c.title) + '">';
  pts.forEach((p, i) => {
    const y0 = T + i * (rowH + gap);
    let acc = 0;
    s += '<text class="lbl strong" x="0" y="' + (y0 + rowH / 2 + 4) + '">' + esc(p.period) + "</text>";
    p.values.forEach((v, j) => {
      const w = x(acc + v) - x(acc);
      s += '<rect class="' + (tones[j] || "") + '" x="' + x(acc) + '" y="' + y0 + '" width="' + w + '" height="' + rowH + '"><title>' +
        esc((series[j] || "") + ", " + p.period + ": " + billions(v)) + "</title></rect>";
      if (w > 56) s += '<text class="inbar" x="' + (x(acc) + 8) + '" y="' + (y0 + rowH / 2 + 4) + '">' + billions(v) + "</text>";
      acc += v;
    });
    s += '<text class="val" x="' + (x(acc) + 6) + '" y="' + (y0 + rowH / 2 + 4) + '">' + billions(acc) + "</text>";
  });
  s += "</svg>";
  s += '<div class="chart-key">' + series.map((n, j) => '<span><i class="k-' + (tones[j] || "") + '"></i>' + esc(n) + "</span>").join("") + "</div>";
  return s;
}

function annualChart(c) {
  const pts = (c.points || []).map(p => ({ year: String(p.year || ""), value: num(p.value), forecast: p.forecast === true,
                                           low: numOrNull(p.low), high: numOrNull(p.high) }));
  if (!pts.length) return "";
  const W = 520, H = 250, L = 44, R = 12, T = 22, B = 30;
  const ymax = niceMax(Math.max(...pts.map(p => p.high || p.value)) * 1.05);
  const step = (W - L - R) / pts.length, bw = step * 0.6;
  const y = v => T + (H - T - B) * (1 - v / ymax);
  let s = '<svg class="chart" viewBox="0 0 ' + W + " " + H + '" role="img" aria-label="' + esc(c.title) + '">';
  for (let i = 0; i <= 4; i++) {
    const v = ymax * i / 4;
    s += '<line class="grid" x1="' + L + '" x2="' + (W - R) + '" y1="' + y(v) + '" y2="' + y(v) + '"/><text class="axis" x="' + (L - 8) + '" y="' + (y(v) + 4) + '" text-anchor="end">$' + v.toFixed(0) + "B</text>";
  }
  pts.forEach((p, i) => {
    const cx = L + step * i + step / 2, x0 = cx - bw / 2;
    const range = p.low !== null && p.high !== null;
    s += '<rect class="' + (p.forecast ? "bar forecast" : "bar") + '" x="' + x0 + '" y="' + y(p.value) + '" width="' + bw + '" height="' + (y(0) - y(p.value)) + '" rx="3"><title>' +
      esc(p.year + ": $" + p.value + "B" + (p.forecast ? " (consensus)" : "") + (range ? " · range $" + p.low + "–" + p.high + "B" : "")) + "</title></rect>";
    if (range) {
      s += '<line class="whisker" x1="' + cx + '" x2="' + cx + '" y1="' + y(p.high) + '" y2="' + y(p.low) + '"/>' +
        '<line class="whisker" x1="' + (cx - 7) + '" x2="' + (cx + 7) + '" y1="' + y(p.high) + '" y2="' + y(p.high) + '"/>' +
        '<line class="whisker" x1="' + (cx - 7) + '" x2="' + (cx + 7) + '" y1="' + y(p.low) + '" y2="' + y(p.low) + '"/>';
    }
    s += '<text class="val" x="' + cx + '" y="' + (y(range ? p.high : p.value) - 6) + '" text-anchor="middle">' + p.value.toFixed(0) + "</text>" +
      '<text class="axis" x="' + cx + '" y="' + (H - B + 17) + '" text-anchor="middle">' + esc(p.year) + "</text>";
  });
  return s + "</svg>" + '<div class="chart-key"><span><i class="k-bar"></i>reported</span><span><i class="k-forecast"></i>consensus</span><span><i class="k-whisker"></i>low–high forecast</span></div>';
}

function simpleBars(c, key, label, opts) {
  const pts = (c.points || []).map(p => ({ name: String(p[key] === undefined ? "" : p[key]), value: num(p.value) }));
  if (!pts.length) return "";
  const W = 520, H = 220, L = 12, R = 12, T = 24, B = 30;
  const ymax = niceMax(Math.max(...pts.map(p => p.value)) * 1.05);
  const step = (W - L - R) / pts.length, bw = Math.min(step * 0.6, 70);
  const y = v => T + (H - T - B) * (1 - v / ymax);
  const minV = Math.min(...pts.map(p => p.value));
  let s = '<svg class="chart" viewBox="0 0 ' + W + " " + H + '" role="img" aria-label="' + esc(c.title) + '">';
  s += '<line class="grid" x1="' + L + '" x2="' + (W - R) + '" y1="' + y(0) + '" y2="' + y(0) + '"/>';
  pts.forEach((p, i) => {
    const cx = L + step * i + step / 2;
    let cls = "bar" + (opts.tone ? " " + opts.tone : "");
    if (opts.highlightLast && i === pts.length - 1) cls += " hl";
    if (opts.dip && p.value === minV) cls += " dip";
    s += '<rect class="' + cls + '" x="' + (cx - bw / 2) + '" y="' + y(p.value) + '" width="' + bw + '" height="' + Math.max(0, y(0) - y(p.value)) + '" rx="3"><title>' +
      esc(p.name + ": " + label(p.value)) + "</title></rect>" +
      '<text class="val" x="' + cx + '" y="' + (y(p.value) - 6) + '" text-anchor="middle">' + esc(label(p.value)) + "</text>" +
      '<text class="axis" x="' + cx + '" y="' + (H - B + 17) + '" text-anchor="middle">' + esc(p.name) + "</text>";
  });
  return s + "</svg>";
}

/* ------------------------------------------------------------ transcript */

/* Which quarters are saved, which were checked and had nothing, which to
   offer. Normalised so a missing list never breaks the view. */
async function fetchOverview(ticker) {
  try {
    const o = await api("/api/transcript?ticker=" + encodeURIComponent(ticker));
    return Object.assign({}, o, { cached: o.cached || [], missed: o.missed || {}, quarters: o.quarters || [] });
  } catch (e) {
    return null;
  }
}

function renderTranscript() {
  const box = $("co-transcript");
  if (!box) return;
  const t = TRANSCRIPT, overview = t.overview;
  let head = '<div class="co-section-head"><span class="card-title">Earnings-call transcript</span>' +
    '<span class="hint">fetched on request from Alpha Vantage · kept only on this computer</span></div>';
  if (!overview) { box.innerHTML = head + '<p class="hint">Loading…</p>'; return; }

  const selected = t.doc ? t.doc.quarter : overview.default;
  const options = overview.quarters.map(q => {
    const note = overview.cached.includes(q) ? " · saved"
      : overview.missed[q] ? " · no transcript (checked " + overview.missed[q] + ")" : "";
    return '<option value="' + esc(q) + '"' + (q === selected ? " selected" : "") + ">" + esc(q + note) + "</option>";
  }).join("");
  const controls = '<div class="tr-controls"><label class="hint" for="tr-quarter">Quarter</label>' +
    '<select id="tr-quarter">' + options + "</select>" +
    '<button class="btn btn-primary" type="button" id="tr-load"' + (t.busy ? " disabled" : "") + "></button>" +
    '<span class="hint" id="tr-status">' + esc(t.busy ? "Fetching…" : "") + "</span></div>";
  const keyForm = overview.has_key ? "" :
    '<form class="tr-key" id="tr-key-form"><div><b>Add a free Alpha Vantage key</b><p class="hint">Get one at alphavantage.co (free tier, a few calls a day). ' +
    "It is saved in <code>secrets.json</code> next to your data, never shown again and never committed.</p></div>" +
    '<input type="password" id="tr-key" autocomplete="off" placeholder="API key" aria-label="Alpha Vantage API key">' +
    '<button class="btn" type="submit">Save key</button></form>';
  const error = t.error ? '<div class="banner tr-error" role="alert">' + esc(t.error) + "</div>" : "";

  let reader = "";
  if (t.doc) {
    const q = t.query.trim().toLowerCase();
    const turns = (t.doc.turns || []).filter(x => !q || (x.speaker + " " + x.content).toLowerCase().includes(q));
    reader = '<div class="tr-bar"><input type="search" id="tr-search" placeholder="Search the call" aria-label="Search the call" value="' + esc(t.query) + '">' +
      '<span class="hint">' + (q ? turns.length + " of " + t.doc.turns.length + " turns match" : t.doc.turns.length + " turns") +
      " · " + esc(t.doc.source) + ", fetched " + esc((t.doc.fetched_at || "").slice(0, 10)) +
      (t.doc.from_cache ? " · opened from the saved copy" : "") + "</span></div>" +
      '<div class="tr-turns">' + turns.map(x => '<div class="turn"><div class="turn-who"><b>' + esc(x.speaker) + "</b><span>" + esc(x.title) + "</span></div>" +
        "<p>" + highlight(x.content, q) + "</p></div>").join("") + "</div>";
  }
  box.innerHTML = head + controls + keyForm + error + reader;

  const sel = $("tr-quarter"), btn = $("tr-load");
  const label = () => {
    btn.textContent = overview.cached.includes(sel.value) ? "Open saved"
      : overview.missed[sel.value] ? "Check again" : "Fetch transcript";
  };
  label();
  sel.addEventListener("change", label);
  btn.addEventListener("click", () => loadTranscript(sel.value, !overview.cached.includes(sel.value)));
  if ($("tr-key-form")) $("tr-key-form").addEventListener("submit", saveKey);
  if ($("tr-search")) {
    const input = $("tr-search");
    input.addEventListener("input", () => {
      t.query = input.value;
      const at = input.selectionStart;
      renderTranscript();
      const again = $("tr-search"); again.focus(); again.setSelectionRange(at, at);
    });
  }
}

/* Matches are found in the raw text, then every piece is escaped on its own,
   so a search for "amp" or "quot" never lands inside an entity. */
function highlight(text, q) {
  const raw = String(text === null || text === undefined ? "" : text);
  if (!q) return esc(raw);
  const pattern = new RegExp("(" + q.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + ")", "gi");
  // split() with a capturing group puts the matches at the odd indexes.
  return raw.split(pattern).map((part, i) => i % 2 ? "<mark>" + esc(part) + "</mark>" : esc(part)).join("");
}

async function loadTranscript(quarter, fromNetwork) {
  const t = TRANSCRIPT, ticker = COMPANY;
  t.busy = true; t.error = "";
  renderTranscript();
  let failedFetch = false;
  try {
    const res = fromNetwork
      ? await api("/api/transcript/fetch", { ticker, quarter })
      : await api("/api/transcript?ticker=" + encodeURIComponent(ticker) + "&quarter=" + encodeURIComponent(quarter));
    if (COMPANY !== ticker) return;
    if (!res.doc) throw new Error("that quarter is not saved yet — fetch it first");
    t.doc = res.doc;
    if (!t.overview.cached.includes(quarter)) t.overview.cached.unshift(quarter);
    delete t.overview.missed[quarter];
  } catch (e) {
    t.error = e.message;
    failedFetch = fromNetwork;
  }
  // A failed fetch may have been recorded as "no transcript for this quarter";
  // ask again so the quarter list shows it.
  if (failedFetch && COMPANY === ticker) {
    const fresh = await fetchOverview(ticker);
    if (fresh && COMPANY === ticker) t.overview = fresh;
  }
  t.busy = false;
  if (COMPANY === ticker) renderTranscript();
}

async function saveKey(e) {
  e.preventDefault();
  const input = $("tr-key");
  try {
    await api("/api/key", { key: input.value });
    TRANSCRIPT.overview.has_key = true;
    TRANSCRIPT.error = "";
  } catch (err) {
    TRANSCRIPT.error = err.message;
  }
  input.value = "";
  renderTranscript();
}

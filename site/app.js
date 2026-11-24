"use strict";
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const pct = (v, d = 2) => (v == null ? "—" : `${v.toFixed(d)}%`);
const val = (o, d = 2) => (o ? pct(o.value, d) : "—");
const fmtDate = (iso, opts = { day: "numeric", month: "short", year: "numeric" }) =>
  iso ? new Date(iso + "T12:00:00").toLocaleDateString("en-GB", opts) : "—";
const daysUntil = (iso, today) => Math.round((new Date(iso + "T12:00:00") - new Date(today + "T12:00:00")) / 864e5);

fetch("data/rates.json", { cache: "no-store" })
  .then((r) => r.json())
  .then(render)
  .catch((e) => { $("updated").textContent = "Couldn't load data: " + e; });

function render(D) {
  $("updated").innerHTML = `Updated ${esc(new Date(D.generated).toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short" }))}`;
  if (D.warnings.length) {
    $("warnings").innerHTML = `<div class="warn-box" role="status"><b>⚠ ${D.warnings.length} data warning${D.warnings.length > 1 ? "s" : ""}</b><ul>${D.warnings.map((w) => `<li>${esc(w)}</li>`).join("")}</ul></div>`;
  }
  renderKpis(D);
  $("fed").innerHTML = bankPanel("🇺🇸 Federal Reserve (Fed)", D.fed, D, {
    rate: D.fed.upper ? `${val(D.fed.lower)} – ${val(D.fed.upper)}` : "—",
    rateNote: `Target range · effective rate ${val(D.fed.effr)}`,
    instrument: (o) => `${o.instrument_days === 30 ? "1-month" : o.instrument_days === 91 ? "3-month" : "6-month"} T-bill ${pct(o.instrument_yield)} vs effective fed funds ${pct(o.policy_rate)}`,
    meetings: D.fed.meetings.map((m) => ({ date: m.date, note: m.sep ? "with projections" : "" })),
  });
  $("banxico").innerHTML = bankPanel("🇲🇽 Banco de México (Banxico)", D.banxico, D, {
    rate: val(D.banxico.target),
    rateNote: "Tasa objetivo de Banxico (overnight interbank target)",
    instrument: (o) => `CETES ${o.instrument_days}d ${pct(o.instrument_yield)} vs target ${pct(o.policy_rate)}`,
    meetings: D.banxico.meetings.map((m) => ({ date: m.date, note: "" })),
  });
  renderChart(D);
  let lastW = $("chart").clientWidth;
  addEventListener("resize", () => { const w = $("chart").clientWidth; if (Math.abs(w - lastW) > 30) { lastW = w; renderChart(D); } });
  renderInvestments(D);
}

function renderKpis(D) {
  const pol = D.investments[0];
  const g = D.gbm_smart_cash;
  const kpi = (l, v, s, cls = "") => `<div class="kpi ${cls}"><div class="l">${l}</div><div class="v">${v}</div><div class="s">${s}</div></div>`;
  $("kpis").innerHTML = [
    kpi("Tasa objetivo", pct(D.tasa_objetivo, 2),
      g.rate == null ? "Set the GBM Smart Cash rate to compute" :
        `Mean of GBM Smart Cash ${pct(g.rate)} and Banxico ${val(D.banxico.target)}`, "hero"),
    kpi("Fed funds target", D.fed.upper ? `${D.fed.lower.value.toFixed(2)}–${D.fed.upper.value.toFixed(2)}%` : "—",
      `Next decision ${fmtDate(D.fed.next_meeting)}`),
    kpi("Banxico target", val(D.banxico.target), `Next decision ${fmtDate(D.banxico.next_meeting)}`),
    kpi("Policy rate spread", pol.spread_pp == null ? "—" : `${pol.spread_pp >= 0 ? "+" : ""}${pol.spread_pp.toFixed(2)} pp`,
      pol.relative_pct == null ? "Mexico − US" : `Mexico's rate is ${pol.relative_pct.toFixed(0)}% higher`),
    kpi("GBM Smart Cash", pct(g.rate), g.as_of ? `Entered ${fmtDate(g.as_of)}` : "Not set yet"),
    kpi("USD/MXN (FIX)", D.banxico.usdmxn ? D.banxico.usdmxn.value.toFixed(4) : "—",
      D.banxico.usdmxn ? fmtDate(D.banxico.usdmxn.date) : ""),
  ].join("");
}

function oddsBlock(o, instrumentText) {
  if (!o) return `<p class="muted">Not enough market data to estimate odds.</p>`;
  const segs = [["cut", o.p_cut], ["hold", o.p_hold], ["hike", o.p_hike]].filter(([, p]) => p > 0.005);
  const bar = segs.map(([k, p]) => `<span class="${k}" style="flex:${p}" title="${k} ${(p * 100).toFixed(0)}%"></span>`).join("");
  const leg = [["cut", o.p_cut], ["hold", o.p_hold], ["hike", o.p_hike]]
    .map(([k, p]) => `<span><span class="dot ${k}"></span>${{ cut: "Cut 25 bp", hold: "Hold", hike: "Hike 25 bp" }[k]} <b>${(p * 100).toFixed(0)}%</b></span>`).join("");
  const extra = o.p_50bp > 0.005 ? ` More than one 25 bp step is priced in, so a 50 bp move is possible.` : "";
  return `<div class="odds-bar" role="img" aria-label="${segs.map(([k, p]) => `${k} ${(p * 100).toFixed(0)}%`).join(", ")}">${bar}</div>
    <div class="odds-legend">${leg}</div>
    <p class="muted" style="margin:6px 0 0">Implied move ${o.implied_move_bp >= 0 ? "+" : ""}${o.implied_move_bp.toFixed(0)} bp, from the ${esc(instrumentText(o))}
    (usual spread ${o.basis >= 0 ? "+" : ""}${o.basis.toFixed(2)} pp, as of ${fmtDate(o.instrument_date)}).${extra}</p>`;
}

function bankPanel(title, B, D, cfg) {
  const decided = Object.fromEntries(B.decisions.map((d) => [d.date, d]));
  const year = D.today.slice(0, 4);
  const shown = cfg.meetings.filter((m) => m.date.slice(0, 4) >= year || decided[m.date]).slice(-16);
  const thisYear = shown.filter((m) => m.date.slice(0, 4) === year || m.date >= D.today);
  const list = thisYear.map((m) => {
    const d = decided[m.date];
    const isNext = m.date === B.next_meeting;
    const a = d ? `<div class="a ${d.action}">${d.action === "hold" ? "Held" : `${d.action === "cut" ? "Cut" : "Hiked"} ${Math.abs(d.bp)} bp`} → ${d.rate.toFixed(2)}%</div>`
      : m.date >= D.today ? `<div class="a">${isNext ? `Next · in ${daysUntil(m.date, D.today)} days` : "Upcoming"}${m.note ? " · " + m.note : ""}</div>` : `<div class="a">—</div>`;
    return `<div class="meet ${isNext ? "next-one" : ""}"><div class="d">${fmtDate(m.date, { day: "numeric", month: "short", year: "2-digit" })}</div>${a}</div>`;
  }).join("");
  const last = B.decisions.at(-1);
  return `<div class="panel-head"><h2>${title}</h2>${last ? `<span class="pill">Last: ${last.action === "hold" ? "hold" : `${last.action} ${Math.abs(last.bp)} bp`} · ${fmtDate(last.date)}</span>` : ""}</div>
    <div class="bank-rate">${cfg.rate}</div>
    <div class="muted">${cfg.rateNote}</div>
    <h3>Next decision · ${fmtDate(B.next_meeting)}${B.next_meeting ? ` (in ${daysUntil(B.next_meeting, D.today)} days)` : ""}</h3>
    ${oddsBlock(B.odds, cfg.instrument)}
    <h3>Meeting calendar</h3>
    <div class="meet-list">${list || '<p class="muted">No calendar data.</p>'}</div>`;
}

// ---------------------------------------------------------------- chart ----
function renderChart(D) {
  const H = D.history;
  const start = new Date(D.today + "T12:00:00"); start.setFullYear(start.getFullYear() - 3);
  const end = new Date(D.today + "T12:00:00");
  const s0 = start.toISOString().slice(0, 10);
  const series = [
    { key: "banxico_target", name: "Banxico target", cls: "mx", pts: H.banxico_target },
    { key: "fed_upper", name: "Fed funds (upper bound)", cls: "us", pts: H.fed_upper },
  ].filter((s) => s.pts.length);
  if (!series.length) { $("chart").innerHTML = '<p class="muted">No history yet.</p>'; return; }

  // Clip each step series to the window, starting with the value in force on s0.
  const clip = (pts) => {
    let before = null; const out = [];
    for (const [d, v] of pts) { if (d < s0) before = v; else out.push([d, v]); }
    if (before != null) out.unshift([s0, before]);
    if (out.length) out.push([D.today, out.at(-1)[1]]);
    return out;
  };
  series.forEach((s) => (s.c = clip(s.pts)));
  const lower = clip(H.fed_lower || []);

  // Draw at the container's real width so text stays legible on phones.
  const W = Math.max(320, $("chart").clientWidth || 1100);
  const narrow = W < 600;
  const Ht = narrow ? 240 : 300, m = { t: 16, r: narrow ? 96 : 150, b: 28, l: 36 };
  const all = series.flatMap((s) => s.c.map((p) => p[1])).concat(lower.map((p) => p[1]));
  const yMin = Math.floor(Math.min(...all) - 0.5), yMax = Math.ceil(Math.max(...all) + 0.5);
  const x = (d) => m.l + ((new Date(d + "T12:00:00") - start) / (end - start)) * (W - m.l - m.r);
  const y = (v) => m.t + (1 - (v - yMin) / (yMax - yMin)) * (Ht - m.t - m.b);
  const stepPath = (pts) => pts.map(([d, v], i) => (i ? `H${x(d).toFixed(1)}V${y(v).toFixed(1)}` : `M${x(d).toFixed(1)},${y(v).toFixed(1)}`)).join("");

  let g = "";
  const yStep = yMax - yMin > 8 ? 2 : 1;
  for (let v = yMin; v <= yMax; v += yStep) {
    g += `<line class="gridline" x1="${m.l}" x2="${W - m.r}" y1="${y(v)}" y2="${y(v)}"/><text x="${m.l - 8}" y="${y(v) + 4}" text-anchor="end">${v}%</text>`;
  }
  for (let yr = start.getFullYear() + 1; yr <= end.getFullYear(); yr++) {
    const d = `${yr}-01-01`;
    g += `<text x="${x(d)}" y="${Ht - 8}" text-anchor="middle">${yr}</text>`;
  }
  // Fed target band (lower..upper) behind the upper-bound line.
  let band = "";
  const fedU = series.find((s) => s.key === "fed_upper");
  if (fedU && lower.length) {
    const top = stepPath(fedU.c);
    const verts = [];
    lower.forEach(([d, v], i) => { if (i) verts.push([d, lower[i - 1][1]]); verts.push([d, v]); });
    const bottom = verts.reverse().map(([d, v]) => `L${x(d).toFixed(1)},${y(v).toFixed(1)}`).join("");
    band = `<path class="band" fill="var(--us)" d="${top}${bottom}Z"/>`;
  }
  const lines = series.map((s) => `<path class="line" stroke="var(--${s.cls})" d="${stepPath(s.c)}"/>`).join("");
  // Direct labels at the right edge, nudged apart if they collide.
  const ends = series.map((s) => ({ s, v: s.c.at(-1)[1], yy: y(s.c.at(-1)[1]) })).sort((a, b) => a.yy - b.yy);
  for (let i = 1; i < ends.length; i++) if (ends[i].yy - ends[i - 1].yy < 16) ends[i].yy = ends[i - 1].yy + 16;
  const labels = ends.map((e) => `<circle cx="${W - m.r}" cy="${y(e.v)}" r="4" fill="var(--${e.s.cls})" stroke="var(--surface)" stroke-width="2"/>
    <text class="lbl" x="${W - m.r + 10}" y="${e.yy + 4}">${narrow ? (e.s.cls === "mx" ? "Banxico" : "Fed") : e.s.name.split(" (")[0]} ${e.v.toFixed(2)}%</text>`).join("");

  $("chart").innerHTML = `<svg viewBox="0 0 ${W} ${Ht}" role="img" aria-label="Step chart of the Banxico target and Fed funds rate over the last 3 years">
    <g class="axis">${g}</g>${band}${lines}${labels}
    <line class="cross" id="cross" y1="${m.t}" y2="${Ht - m.b}" visibility="hidden"/>
    <rect id="hit" x="${m.l}" y="0" width="${W - m.l - m.r}" height="${Ht}" fill="transparent"/></svg>`;
  $("chart-legend").innerHTML = series.map((s) => `<span><span class="dot ${s.cls}"></span>${s.name}</span>`).join("") +
    (lower.length ? `<span>Shaded: Fed target range</span>` : "");

  // Hover: crosshair + tooltip with every series' value on that date.
  const svg = $("chart").querySelector("svg"), cross = $("cross"), tip = $("tip");
  const at = (pts, d) => { let v = null; for (const [pd, pv] of pts) { if (pd > d) break; v = pv; } return v; };
  const move = (ev) => {
    const r = svg.getBoundingClientRect();
    const px = ((ev.clientX - r.left) / r.width) * W;
    const t = new Date(start.getTime() + ((px - m.l) / (W - m.l - m.r)) * (end - start));
    const d = t.toISOString().slice(0, 10);
    cross.setAttribute("x1", px); cross.setAttribute("x2", px); cross.setAttribute("visibility", "visible");
    const rows = series.map((s) => { const v = at(s.c, d); return v == null ? "" : `<div><span class="dot ${s.cls}"></span>${s.name}: <b>${v.toFixed(2)}%</b></div>`; }).join("");
    const bx = at(series.find((s) => s.key === "banxico_target")?.c || [], d), fu = at(fedU?.c || [], d);
    const spread = bx != null && fu != null ? `<div class="t">Spread ${(bx - fu >= 0 ? "+" : "") + (bx - fu).toFixed(2)} pp</div>` : "";
    tip.innerHTML = `<div class="t">${fmtDate(d)}</div>${rows}${spread}`;
    tip.hidden = false;
    const tx = ev.clientX + 14 + tip.offsetWidth > innerWidth ? ev.clientX - tip.offsetWidth - 14 : ev.clientX + 14;
    tip.style.left = Math.max(8, Math.min(tx, innerWidth - tip.offsetWidth - 8)) + "px"; tip.style.top = ev.clientY + 14 + "px";
  };
  const hit = $("hit");
  hit.addEventListener("pointermove", move);
  hit.addEventListener("pointerleave", () => { tip.hidden = true; cross.setAttribute("visibility", "hidden"); });

  // Table view of the same data: every rate change.
  const ch = [...D.fed.changes.map((c) => ({ ...c, bank: "Fed (upper)" })), ...D.banxico.changes.map((c) => ({ ...c, bank: "Banxico" }))]
    .sort((a, b) => (a.date < b.date ? 1 : -1));
  $("chart-table").innerHTML = `<div class="scroll"><table class="grid"><thead><tr><th>Effective</th><th>Bank</th><th class="r">From</th><th class="r">To</th><th class="r">Change</th></tr></thead><tbody>${
    ch.map((c) => `<tr><td>${fmtDate(c.date)}</td><td>${c.bank}</td><td class="r">${pct(c.from)}</td><td class="r">${pct(c.to)}</td><td class="r">${c.bp > 0 ? "+" : ""}${c.bp} bp</td></tr>`).join("")
  }</tbody></table></div>`;
}

function renderInvestments(D) {
  const g = D.gbm_smart_cash;
  const cell = (o) => (o ? `<td class="r">${pct(o.value)}<span class="nm">${fmtDate(o.date, { day: "numeric", month: "short" })}</span></td>` : `<td class="r">—</td>`);
  const rows = D.investments.map((r) => `<tr><td>${esc(r.label)}<span class="nm">${esc(r.us_name)} · ${esc(r.mx_name)}</span></td>
    ${cell(r.us)}${cell(r.mx)}
    <td class="r">${r.spread_pp == null ? "—" : `${r.spread_pp >= 0 ? "+" : ""}${r.spread_pp.toFixed(2)} pp`}</td>
    <td class="r">${r.relative_pct == null ? "—" : `${r.relative_pct >= 0 ? "+" : ""}${r.relative_pct.toFixed(1)}%`}</td></tr>`).join("");
  const gbmRow = `<tr><td>GBM Smart Cash<span class="nm">Entered by hand</span></td><td class="r">—</td>${g.rate == null ? '<td class="r">—</td>' : `<td class="r">${pct(g.rate)}<span class="nm">${fmtDate(g.as_of, { day: "numeric", month: "short" })}</span></td>`}<td class="r">—</td><td class="r">—</td></tr>
    <tr class="hl"><td>Tasa objetivo<span class="nm">(GBM Smart Cash + Banxico target) ÷ 2</span></td><td class="r">—</td><td class="r">${pct(D.tasa_objetivo)}</td><td class="r">—</td><td class="r">—</td></tr>`;
  $("inv").innerHTML = `<thead><tr><th>Maturity</th><th class="r">🇺🇸 US</th><th class="r">🇲🇽 Mexico</th><th class="r">Spread</th><th class="r">Relative</th></tr></thead><tbody>${rows}${gbmRow}</tbody>`;
}

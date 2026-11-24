"use strict";
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const pct = (v, d = 2) => (v == null ? "—" : `${v.toFixed(d)}%`);
const val = (o, d = 2) => (o ? pct(o.value, d) : "—");
const P = (v) => `${Math.round(v * 100)}%`;
const fmtDate = (iso, opts = { day: "numeric", month: "short", year: "numeric" }) =>
  iso ? new Date(iso + "T12:00:00").toLocaleDateString("es-MX", opts).replace(/\./g, "") : "—";
const dayLabel = (iso) => {
  const d = new Date(iso + "T12:00:00");
  return `${d.toLocaleDateString("es-MX", { weekday: "short" }).replace(/\./g, "")} ${d.getDate()} ${d.toLocaleDateString("es-MX", { month: "short" }).replace(/\./g, "")}`;
};
const short = (iso) => fmtDate(iso, { day: "numeric", month: "short" });
const daysUntil = (iso, today) => Math.round((new Date(iso + "T12:00:00") - new Date(today + "T12:00:00")) / 864e5);
const cuando = (d) => d === 0 ? "hoy" : d === 1 ? "mañana" : d < 60 ? `en ${d} días` : d < 365 ? `en ${Math.round(d / 30.4)} meses` : `en ${(d / 365).toFixed(1)} años`;
const ACT = { cut: "Baja", hold: "Se mantiene", hike: "Sube" };

fetch("data/rates.json", { cache: "no-store" })
  .then((r) => r.json())
  .then(render)
  .catch((e) => { $("updated").textContent = "No se pudieron cargar los datos: " + e; });

function render(D) {
  $("updated").textContent = `Actualizado: ${new Date(D.generated).toLocaleString("es-MX", { dateStyle: "medium", timeStyle: "short" })}`;
  if (D.warnings.length) {
    $("warnings").innerHTML = `<div class="warn-box" role="status"><b>⚠ ${D.warnings.length} aviso${D.warnings.length > 1 ? "s" : ""} sobre los datos</b><ul>${D.warnings.map((w) => `<li>${esc(w)}</li>`).join("")}</ul></div>`;
  }
  const banks = [
    { key: "fed", name: "Fed", full: "Reserva Federal (EE. UU.)", flag: "🇺🇸", B: D.fed,
      meetings: D.fed.meetings.map((m) => ({ date: m.date, note: m.sep ? "con proyecciones" : "" })),
      instrument: (o) => `T-bill a ${o.instrument_days === 30 ? "1 mes" : o.instrument_days === 91 ? "3 meses" : "6 meses"}` },
    { key: "banxico", name: "Banxico", full: "Banco de México", flag: "🇲🇽", B: D.banxico,
      meetings: D.banxico.meetings.map((m) => ({ date: m.date, note: "" })),
      instrument: (o) => `CETES a ${o.instrument_days} días` },
  ];
  renderDates(D, banks);
  renderOdds(D, banks);
  renderTasa(D);
  renderFxElections(D);
  renderFx(D);
  renderChart(D);
  let lastW = $("chart").clientWidth;
  addEventListener("resize", () => { const w = $("chart").clientWidth; if (Math.abs(w - lastW) > 30) { lastW = w; renderChart(D); } });
  renderOtras(D);
}

const likeliest = (o) => (o ? [["cut", o.p_cut], ["hold", o.p_hold], ["hike", o.p_hike]].sort((a, b) => b[1] - a[1])[0] : null);
const lastTxt = (d) => !d ? "—" : d.action === "hold" ? `se mantuvo, ${short(d.date)}` : `${d.action === "cut" ? "bajó" : "subió"} ${(Math.abs(d.bp) / 100).toFixed(2)}, ${short(d.date)}`;

// ------------------------------------------------- 1. Posible fecha de cambio ----
function renderDates(D, banks) {
  $("dates").innerHTML = banks.map((b) => {
    const n = b.B.next_meeting, top = likeliest(b.B.odds);
    const days = n ? daysUntil(n, D.today) : null;
    return `<div class="card date-card">
      <div class="who">${b.flag} ${b.name}</div>
      <div class="big">${n ? dayLabel(n) : "—"}</div>
      <div class="cd">${n ? `${cuando(days)} · ${fmtDate(n, { year: "numeric" })}` : ""}</div>
      ${top ? `<div class="chip ${top[0]}">${ACT[top[0]]} · ${P(top[1])}</div>` : ""}
      <div class="muted small">Última: ${lastTxt(b.B.decisions.at(-1))}</div>
    </div>`;
  }).join("");

  $("calendars").innerHTML = banks.map((b) => {
    const decided = Object.fromEntries(b.B.decisions.map((d) => [d.date, d]));
    const year = D.today.slice(0, 4);
    const list = b.meetings.filter((m) => m.date.slice(0, 4) >= year).map((m) => {
      const d = decided[m.date], isNext = m.date === b.B.next_meeting;
      const a = d ? `<span class="a ${d.action}">${d.action === "hold" ? "Se mantuvo" : `${d.action === "cut" ? "Bajó" : "Subió"} ${(Math.abs(d.bp) / 100).toFixed(2)}`}</span>`
        : m.date >= D.today ? `<span class="a">${isNext ? "Próxima" : "Pendiente"}${m.note ? " · " + m.note : ""}</span>` : `<span class="a">—</span>`;
      return `<li class="${isNext ? "next-one" : ""}"><span class="d">${fmtDate(m.date, { day: "numeric", month: "short", year: "2-digit" })}</span>${a}</li>`;
    }).join("");
    return `<h3>${b.flag} ${b.full}</h3><ul class="meet-list">${list}</ul>`;
  }).join("");
}

// --------------------------------------------- 2. Posibilidad de cambio de tasa ----
function renderOdds(D, banks) {
  const SIG = {
    clear: "Señal clara: todos los plazos coinciden",
    mixed: "Señal mixta: los plazos largos no coinciden; confianza baja",
    weak: "Señal débil: el movimiento está dentro del ruido normal; léelo como «se mantiene»",
  };
  $("odds").innerHTML = banks.map((b) => {
    const o = b.B.odds;
    if (!o) return `<div class="card"><div class="who">${b.flag} ${b.name}</div><p class="muted">Sin datos suficientes.</p></div>`;
    const rows = [["cut", o.p_cut], ["hold", o.p_hold], ["hike", o.p_hike]];
    const bar = rows.filter(([, p]) => p > 0.005).map(([k, p]) => `<span class="${k}" style="flex:${p}"></span>`).join("");
    return `<div class="card">
      <div class="row-between"><div class="who">${b.flag} ${b.name}</div><div class="muted small">${fmtDate(o.meeting, { day: "numeric", month: "short" })} · ${cuando(o.days_to_meeting)}</div></div>
      <div class="odds-bar ${o.signal !== "clear" ? "dim" : ""}" role="img" aria-label="${rows.map(([k, p]) => `${ACT[k]} ${P(p)}`).join(", ")}">${bar}</div>
      <div class="odds3">${rows.map(([k, p]) => `<div><span class="dot ${k}"></span>${ACT[k]}<b>${P(p)}</b></div>`).join("")}</div>
      <p class="signal ${o.signal}">${SIG[o.signal] || ""}</p>
      <p class="muted small">Movimiento implícito ${o.implied_move_bp >= 0 ? "+" : ""}${(o.implied_move_bp / 100).toFixed(2)} puntos según el ${b.instrument(o)} (${pct(o.instrument_yield)}, ${short(o.instrument_date)}).${o.p_50bp > 0.005 ? " El mercado descuenta más de un movimiento de 0.25." : ""}</p>
    </div>`;
  }).join("");
}

// ------------------------------------------------------------- 3. Tasa actual ----
function renderTasa(D) {
  const nm = (n) => n.replace("CETES ", "CETES ").replace(" días", "d").replace("Treasury ", "Tesoro ").replace(" meses", "m").replace(" mes", "m").replace(" años", "a").replace(" año", "a");
  const dates = (side) => [...new Set(D.tasa_actual.map((t) => t[side] && short(t[side].date)).filter(Boolean))].join(", ");
  $("tasa").innerHTML = `<thead><tr><th>Plazo</th><th class="r">🇲🇽 México</th><th class="r">🇺🇸 EE. UU.</th></tr></thead><tbody>${
    D.tasa_actual.map((t) => `<tr>
      <td class="term">${t.label}</td>
      <td class="r"><b>${val(t.mx)}</b><span class="nm">${nm(t.mx_name)}</span></td>
      <td class="r"><b>${val(t.us)}</b><span class="nm">${nm(t.us_name)}</span></td>
    </tr>`).join("")}</tbody>
    <caption>Datos al: México ${dates("mx")} (subastas de Banxico) · EE. UU. ${dates("us")}</caption>`;
}

// ------------------------------------------------ 4. Probabilidad tipo de cambio ----
function renderFxElections(D) {
  const F = D.fx, E = D.election_fx;
  if (!F) { $("fixnow").innerHTML = '<p class="muted">Faltan datos de Banxico.</p>'; return; }
  $("fixnow").innerHTML = `<div class="card fix">
      <div><div class="muted small">Dólar hoy (FIX)</div><div class="big">$${F.spot.toFixed(2)}</div><div class="muted small">${fmtDate(F.spot_date)}</div></div>
      ${E ? `<div class="hist"><div class="muted small">Movimiento típico del peso en la semana de la elección*</div>
        <div><b>±${E.normal_sd_pct.toFixed(1)}%</b> normal</div>
        ${E.history.MX ? `<div><b>±${E.history.MX.election_sd_pct.toFixed(1)}%</b> con elección en 🇲🇽</div>` : ""}
        ${E.history.US ? `<div><b>±${E.history.US.election_sd_pct.toFixed(1)}%</b> con elección en 🇺🇸</div>` : ""}</div>` : ""}
    </div>`;
  if (!E || !E.elections.length) { $("elecfx").innerHTML = ""; return; }
  const flag = { MX: "🇲🇽", US: "🇺🇸" };
  $("elecfx").innerHTML = E.elections.map((e) => {
    const w = e.week, h = E.history[e.country];
    const past = h ? h.moves.map((m) => `${m.date.slice(0, 4)}: ${m.move_pct >= 0 ? "+" : ""}${m.move_pct.toFixed(1)}%`).join(" · ") : "";
    return `<div class="card elec">
      <div class="who">${flag[e.country]} ${esc(e.name)}</div>
      <div class="muted small">${fmtDate(e.date)} · ${cuando(e.days_until)}</div>
      <div class="tiles3">
        <div><b>${P(w.p_big5)}</b><span>movimiento de 5% o más</span><em>normal: ${P(w.p_big5_normal)}</em></div>
        <div><b>${P(w.p_weaker3)}</b><span>dólar sube 3% o más</span><em>peso más débil</em></div>
        <div><b>${P(w.p_stronger3)}</b><span>dólar baja 3% o más</span><em>peso más fuerte</em></div>
      </div>
      <p class="small fwdline">Mercado para esa fecha: <b>$${e.forward.toFixed(2)}</b> <span class="muted">(80%: $${e.with_election.range80[0].toFixed(2)}–$${e.with_election.range80[1].toFixed(2)})</span></p>
      ${past ? `<p class="muted small">Antes: ${past}</p>` : ""}
    </div>`;
  }).join("");
}

function renderFx(D) {
  const F = D.fx;
  if (!F || !F.horizons.length) { $("fx").innerHTML = '<p class="muted">Faltan datos de Banxico.</p>'; return; }
  $("fx").innerHTML = `<p class="muted small">Volatilidad del último año: ${F.vol_annual.toFixed(1)}% anual. El centro es el forward (lo que descuenta el mercado), no un pronóstico.</p>` +
    F.horizons.map((h) => `<div class="hz">
      <div class="row-between"><b>${h.label}</b><span class="muted small">forward $${h.forward.toFixed(2)} (${h.forward_pct >= 0 ? "+" : ""}${h.forward_pct.toFixed(1)}%)</span></div>
      <div class="kv tight">
        <div><span>Peso más débil (dólar más alto)</span><b>${P(h.p_usd_up)}</b></div>
        <div><span>Dólar sube 5% o más</span><b>${P(h.p_up_5)}</b></div>
        <div><span>Dólar baja 5% o más</span><b>${P(h.p_down_5)}</b></div>
        <div><span>Rango probable (80%)</span><b>$${h.range80[0].toFixed(2)}–$${h.range80[1].toFixed(2)}</b></div>
      </div>
      ${h.events.length ? `<div class="muted small">Incluye: ${h.events.map(esc).join(", ")}</div>` : ""}
    </div>`).join("");
}

// ------------------------------------------------------------- historial ----
function renderChart(D) {
  const H = D.history;
  const start = new Date(D.today + "T12:00:00"); start.setFullYear(start.getFullYear() - 3);
  const end = new Date(D.today + "T12:00:00");
  const s0 = start.toISOString().slice(0, 10);
  const series = [
    { key: "banxico_target", name: "Banxico", cls: "mx", pts: H.banxico_target },
    { key: "fed_upper", name: "Fed (límite superior)", cls: "us", pts: H.fed_upper },
  ].filter((s) => s.pts.length);
  if (!series.length) { $("chart").innerHTML = '<p class="muted">Sin historial.</p>'; return; }
  const clip = (pts) => {
    let before = null; const out = [];
    for (const [d, v] of pts) { if (d < s0) before = v; else out.push([d, v]); }
    if (before != null) out.unshift([s0, before]);
    if (out.length) out.push([D.today, out.at(-1)[1]]);
    return out;
  };
  series.forEach((s) => (s.c = clip(s.pts)));
  const lower = clip(H.fed_lower || []);
  const W = Math.max(300, $("chart").clientWidth || 700);
  const narrow = W < 600;
  const Ht = narrow ? 220 : 280, m = { t: 14, r: narrow ? 92 : 130, b: 26, l: 34 };
  const all = series.flatMap((s) => s.c.map((p) => p[1])).concat(lower.map((p) => p[1]));
  const yMin = Math.floor(Math.min(...all) - 0.5), yMax = Math.ceil(Math.max(...all) + 0.5);
  const x = (d) => m.l + ((new Date(d + "T12:00:00") - start) / (end - start)) * (W - m.l - m.r);
  const y = (v) => m.t + (1 - (v - yMin) / (yMax - yMin)) * (Ht - m.t - m.b);
  const stepPath = (pts) => pts.map(([d, v], i) => (i ? `H${x(d).toFixed(1)}V${y(v).toFixed(1)}` : `M${x(d).toFixed(1)},${y(v).toFixed(1)}`)).join("");
  let g = "";
  const yStep = yMax - yMin > 8 ? 2 : 1;
  for (let v = yMin; v <= yMax; v += yStep) g += `<line class="gridline" x1="${m.l}" x2="${W - m.r}" y1="${y(v)}" y2="${y(v)}"/><text x="${m.l - 6}" y="${y(v) + 4}" text-anchor="end">${v}%</text>`;
  for (let yr = start.getFullYear() + 1; yr <= end.getFullYear(); yr++) g += `<text x="${x(`${yr}-01-01`)}" y="${Ht - 6}" text-anchor="middle">${yr}</text>`;
  let band = "";
  const fedU = series.find((s) => s.key === "fed_upper");
  if (fedU && lower.length) {
    const verts = [];
    lower.forEach(([d, v], i) => { if (i) verts.push([d, lower[i - 1][1]]); verts.push([d, v]); });
    band = `<path class="band" fill="var(--us)" d="${stepPath(fedU.c)}${verts.reverse().map(([d, v]) => `L${x(d).toFixed(1)},${y(v).toFixed(1)}`).join("")}Z"/>`;
  }
  const lines = series.map((s) => `<path class="line" stroke="var(--${s.cls})" d="${stepPath(s.c)}"/>`).join("");
  const ends = series.map((s) => ({ s, v: s.c.at(-1)[1], yy: y(s.c.at(-1)[1]) })).sort((a, b) => a.yy - b.yy);
  for (let i = 1; i < ends.length; i++) if (ends[i].yy - ends[i - 1].yy < 16) ends[i].yy = ends[i - 1].yy + 16;
  const labels = ends.map((e) => `<circle cx="${W - m.r}" cy="${y(e.v)}" r="4" fill="var(--${e.s.cls})" stroke="var(--surface)" stroke-width="2"/>
    <text class="lbl" x="${W - m.r + 9}" y="${e.yy + 4}">${e.s.cls === "mx" ? "Banxico" : "Fed"} ${e.v.toFixed(2)}%</text>`).join("");
  $("chart").innerHTML = `<svg viewBox="0 0 ${W} ${Ht}" role="img" aria-label="Tasa de Banxico y de la Fed en los últimos 3 años">
    <g class="axis">${g}</g>${band}${lines}${labels}
    <line class="cross" id="cross" y1="${m.t}" y2="${Ht - m.b}" visibility="hidden"/>
    <rect id="hit" x="${m.l}" y="0" width="${W - m.l - m.r}" height="${Ht}" fill="transparent"/></svg>`;
  $("chart-legend").innerHTML = series.map((s) => `<span><span class="dot ${s.cls}"></span>${s.name}</span>`).join("") + (lower.length ? `<span>Sombreado: rango de la Fed</span>` : "");
  const svg = $("chart").querySelector("svg"), cross = $("cross"), tip = $("tip");
  const at = (pts, d) => { let v = null; for (const [pd, pv] of pts) { if (pd > d) break; v = pv; } return v; };
  const move = (ev) => {
    const r = svg.getBoundingClientRect();
    const px = ((ev.clientX - r.left) / r.width) * W;
    const d = new Date(start.getTime() + ((px - m.l) / (W - m.l - m.r)) * (end - start)).toISOString().slice(0, 10);
    cross.setAttribute("x1", px); cross.setAttribute("x2", px); cross.setAttribute("visibility", "visible");
    tip.innerHTML = `<div class="t">${fmtDate(d)}</div>` + series.map((s) => { const v = at(s.c, d); return v == null ? "" : `<div><span class="dot ${s.cls}"></span>${s.name}: <b>${v.toFixed(2)}%</b></div>`; }).join("");
    tip.hidden = false;
    const tx = ev.clientX + 14 + tip.offsetWidth > innerWidth ? ev.clientX - tip.offsetWidth - 14 : ev.clientX + 14;
    tip.style.left = Math.max(8, Math.min(tx, innerWidth - tip.offsetWidth - 8)) + "px"; tip.style.top = ev.clientY + 14 + "px";
  };
  const hit = $("hit");
  hit.addEventListener("pointermove", move);
  hit.addEventListener("pointerdown", move);
  hit.addEventListener("pointerleave", () => { tip.hidden = true; cross.setAttribute("visibility", "hidden"); });
  const ch = [...D.fed.changes.map((c) => ({ ...c, bank: "Fed" })), ...D.banxico.changes.map((c) => ({ ...c, bank: "Banxico" }))].sort((a, b) => (a.date < b.date ? 1 : -1));
  $("chart-table").innerHTML = `<table class="rates"><thead><tr><th>Fecha</th><th>Banco</th><th class="r">De</th><th class="r">A</th></tr></thead><tbody>${
    ch.map((c) => `<tr><td>${fmtDate(c.date)}</td><td>${c.bank}</td><td class="r">${pct(c.from)}</td><td class="r">${pct(c.to)}</td></tr>`).join("")}</tbody></table>`;
}

// ------------------------------------------------------------- otras tasas ----
function renderOtras(D) {
  const want = { "Overnight": "Un día", "Overnight (secured)": "Un día (con garantía)", "~1 month (interbank)": "28 días interbancaria" };
  const rows = D.investments.filter((r) => want[r.label]);
  $("otras").innerHTML = `<thead><tr><th>Plazo</th><th class="r">🇲🇽 México</th><th class="r">🇺🇸 EE. UU.</th></tr></thead><tbody>${
    rows.map((r) => `<tr><td class="term">${want[r.label]}</td>
      <td class="r"><b>${val(r.mx)}</b><span class="nm">${esc(r.mx_name)}</span></td>
      <td class="r"><b>${val(r.us)}</b><span class="nm">${esc({ "Effective fed funds": "Fed funds efectiva", "SOFR": "SOFR", "T-bill 1M": "T-bill 1 mes" }[r.us_name] || r.us_name)}</span></td></tr>`).join("")}</tbody>`;
}

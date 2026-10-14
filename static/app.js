"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (x, d = 1) => (x == null ? "–" : Number(x).toFixed(d));

const store = {
  get(k, d) { try { const v = localStorage.getItem("fplsim:" + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("fplsim:" + k, JSON.stringify(v)); } catch {} },
};

const state = {
  proj: null,
  overrides: store.get("overrides", {}),
  team: null,
  scratch: false,
  locked: [],
  banned: [],
  plan: null,
  planGw: 0,
  sort: { key: "gw0", dir: -1 },
  limit: 100,
  pos: "",
  fxMode: "attack",
};

const CHIPS = [
  { key: "wildcard", label: "Wildcard" },
  { key: "freehit", label: "Free Hit" },
  { key: "bboost", label: "Bench Boost" },
  { key: "3xc", label: "Triple Captain" },
];
const CHIP_SHORT = { wildcard: "WC", freehit: "FH", bboost: "BB", "3xc": "TC" };

// ---------------------------------------------------------------- api

async function api(path, body) {
  const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || "Request failed");
  return data;
}

function busy(msg) {
  $("#loading-msg").textContent = msg;
  $("#loading").hidden = !msg;
}

let toastTimer;
function toast(msg, ok = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = ok ? "ok" : "";
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.hidden = true), 4500);
}

async function loadProjections() {
  state.proj = await api("/api/projections", { minutes_override: state.overrides });
  state.byId = Object.fromEntries(state.proj.players.map((p) => [p.id, p]));
  const d = state.proj.next_deadline ? new Date(state.proj.next_deadline) : null;
  $("#deadline").textContent = d
    ? `GW${state.proj.gws[0]} deadline: ${d.toLocaleString(undefined, { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}`
    : "Season complete";
  initSelectors();
  renderProjections();
  renderFixtures();
  renderChips();
}

// ---------------------------------------------------------------- tabs

$$(".tab").forEach((b) =>
  b.addEventListener("click", () => {
    $$(".tab").forEach((x) => x.classList.toggle("active", x === b));
    $$(".panel").forEach((p) => p.classList.toggle("active", p.id === b.dataset.tab));
    store.set("tab", b.dataset.tab);
  })
);

function initSelectors() {
  const gws = state.proj.gws;
  const opts = (sel, def) => gws.map((g, i) => `<option value="${i + 1}" ${i + 1 === def ? "selected" : ""}>${i + 1} GW${i ? "s" : ""}</option>`).join("");
  if (!$("#horizon").options.length) {
    $("#horizon").innerHTML = opts("#horizon", Math.min(5, gws.length));
    $("#total-n").innerHTML = opts("#total-n", Math.min(5, gws.length));
    $("#fx-n").innerHTML = opts("#fx-n", Math.min(5, gws.length));
    const teams = [...state.proj.teams].sort((a, b) => a.name.localeCompare(b.name));
    $("#team-filter").innerHTML += teams.map((t) => `<option value="${t.id}">${esc(t.name)}</option>`).join("");
  }
  $("#player-list").innerHTML = [...state.proj.players]
    .sort((a, b) => b.total_xpts - a.total_xpts)
    .map((p) => `<option value="${esc(p.name)} (${p.team_short}, ${p.pos})"></option>`)
    .join("");
  $("#horizon").onchange = renderChips;
}

// ---------------------------------------------------------------- projections table

function heat(x, max = 9) {
  const a = Math.max(0, Math.min(1, x / max)) * 0.55;
  return `background: rgba(var(--heat), ${a.toFixed(3)})`;
}

function bdTitle(p, i) {
  if (i !== 0 || !p.breakdown) return "";
  const names = { app: "Appearance", goals: "Goals", assists: "Assists", cs: "Clean sheet", bonus: "Bonus", cards: "Cards", conceded: "Conceded", saves: "Saves", dc: "Def. contribution" };
  return Object.entries(p.breakdown).map(([k, v]) => `${names[k] || k}: ${v.toFixed(2)}`).join("\n");
}

function renderProjections() {
  if (!state.proj) return;
  const { gws } = state.proj;
  const n = +$("#total-n").value || gws.length;
  const q = $("#search").value.trim().toLowerCase();
  const team = $("#team-filter").value;
  const maxPrice = +$("#max-price").value || 99;
  const total = (p) => p.xpts.slice(0, n).reduce((a, b) => a + b, 0);

  const cols = [
    { key: "name", label: "Player", l: true, v: (p) => p.name },
    { key: "team", label: "Team", l: true, v: (p) => p.team_short },
    { key: "price", label: "£", v: (p) => p.price },
    { key: "own", label: "Own %", v: (p) => p.selected_by },
    { key: "pts", label: "Pts", v: (p) => p.total_points },
    { key: "mins", label: "xMins", v: (p) => p.xmins[0] },
    ...gws.map((g, i) => ({ key: "gw" + i, label: "GW" + g, v: (p) => p.xpts[i] })),
    { key: "total", label: `Next ${n}`, v: total },
    { key: "value", label: "Pts/£", v: (p) => total(p) / p.price },
  ];
  const col = cols.find((c) => c.key === state.sort.key) || cols[6];

  let rows = state.proj.players.filter(
    (p) =>
      (!state.pos || p.pos === state.pos) &&
      (!team || p.team === +team) &&
      p.price <= maxPrice &&
      (!q || p.name.toLowerCase().includes(q) || p.full_name.toLowerCase().includes(q) || p.team_short.toLowerCase() === q)
  );
  rows.sort((a, b) => {
    const x = col.v(a), y = col.v(b);
    return (typeof x === "string" ? x.localeCompare(y) : x - y) * state.sort.dir;
  });

  $("#proj-table thead").innerHTML =
    "<tr><th class='l'>#</th>" +
    cols.map((c) => `<th data-k="${c.key}" class="${c.l ? "l" : ""} ${c.key === col.key ? "sorted" : ""}">${c.label}${c.key === col.key ? (state.sort.dir < 0 ? " ↓" : " ↑") : ""}</th>`).join("") +
    "</tr>";

  const shown = rows.slice(0, state.limit);
  $("#proj-table tbody").innerHTML = shown
    .map((p, idx) => {
      const flag = p.status === "a" ? "" : `<span class="flag ${p.status === "d" ? "d" : "x"}" title="${esc(p.news)}"></span>`;
      const ov = state.overrides[p.id] != null;
      return `<tr>
        <td class="l muted">${idx + 1}</td>
        <td class="l"><span class="pname">${esc(p.name)}</span>${flag} <span class="pos-tag">${p.pos}</span></td>
        <td class="l">${p.team_short}</td>
        <td>${fmt(p.price)}</td>
        <td>${fmt(p.selected_by)}</td>
        <td>${p.total_points}</td>
        <td><button class="mins-btn ${ov ? "ov" : ""}" data-id="${p.id}" title="${ov ? "Your override. Click to change, clear the box to reset" : "Model estimate. Click to override"}">${ov ? state.overrides[p.id] : p.xmins[0]}</button></td>
        ${p.xpts.map((x, i) => `<td class="heat" style="${heat(x)}" title="${esc(bdTitle(p, i))}">${x ? fmt(x) : "<span class='muted'>–</span>"}</td>`).join("")}
        <td class="heat" style="${heat(total(p), 9 * n)}"><b>${fmt(total(p))}</b></td>
        <td>${fmt(total(p) / p.price, 2)}</td>
      </tr>`;
    })
    .join("");
  $("#more").hidden = rows.length <= state.limit;
  $("#clear-overrides").hidden = !Object.keys(state.overrides).length;
}

$("#proj-table thead").addEventListener("click", (e) => {
  const k = e.target.closest("th")?.dataset.k;
  if (!k) return;
  state.sort = { key: k, dir: state.sort.key === k ? -state.sort.dir : ["name", "team"].includes(k) ? 1 : -1 };
  renderProjections();
});
$("#proj-table tbody").addEventListener("click", (e) => {
  const btn = e.target.closest(".mins-btn");
  if (!btn) return;
  const id = btn.dataset.id;
  const input = document.createElement("input");
  input.type = "number"; input.min = 0; input.max = 90; input.className = "mins-input";
  input.value = state.overrides[id] ?? "";
  input.placeholder = btn.textContent;
  btn.replaceWith(input);
  input.focus();
  const commit = async () => {
    const v = input.value.trim();
    if (v === "") delete state.overrides[id];
    else state.overrides[id] = Math.max(0, Math.min(90, +v));
    store.set("overrides", state.overrides);
    busy("Recalculating projections…");
    try { await loadProjections(); } catch (err) { toast(err.message); } finally { busy(); }
  };
  input.addEventListener("keydown", (ev) => { if (ev.key === "Enter") input.blur(); if (ev.key === "Escape") { input.value = state.overrides[id] ?? ""; input.blur(); } });
  input.addEventListener("blur", commit, { once: true });
});
["#search", "#team-filter", "#max-price", "#total-n"].forEach((s) => $(s).addEventListener("input", () => { state.limit = 100; renderProjections(); }));
$("#pos-filter").addEventListener("click", (e) => {
  const b = e.target.closest("button"); if (!b) return;
  $$("#pos-filter button").forEach((x) => x.classList.toggle("on", x === b));
  state.pos = b.dataset.pos; state.limit = 100;
  renderProjections();
});
$("#more").onclick = () => { state.limit += 100; renderProjections(); };
$("#clear-overrides").onclick = async () => {
  state.overrides = {}; store.set("overrides", {});
  busy("Recalculating projections…");
  try { await loadProjections(); } finally { busy(); }
};

// ---------------------------------------------------------------- fixtures

function renderFixtures() {
  if (!state.proj) return;
  const { gws, teams } = state.proj;
  const n = +$("#fx-n").value || gws.length;
  const attack = state.fxMode === "attack";
  const val = (f) => (attack ? f.xg : Math.exp(-f.xga));
  const cellVal = (fs) => fs.reduce((a, f) => a + val(f), 0);
  const score = (t) => t.fixtures.slice(0, n).reduce((a, fs) => a + cellVal(fs), 0);
  const rows = [...teams].sort((a, b) => score(b) - score(a));
  $("#fx-table thead").innerHTML = `<tr><th class="l">Team</th>${gws.map((g) => `<th style="text-align:center">GW${g}</th>`).join("")}<th>Next ${n}</th></tr>`;
  $("#fx-table tbody").innerHTML = rows
    .map((t) => `<tr><td class="l pname">${esc(t.name)}</td>${t.fixtures
      .map((fs) => {
        if (!fs.length) return `<td class="fx-cell muted">blank</td>`;
        const v = cellVal(fs);
        const style = attack ? heat((v / fs.length) - 0.8, 1.6) : heat((v / fs.length) - 0.1, 0.4);
        return `<td class="fx-cell" style="${style}">${fs.map((f) => `${f.home ? f.opp : f.opp.toLowerCase()}`).join(" + ")}<small>${attack ? fmt(v, 2) : Math.round(v * 100) + "%"}</small></td>`;
      })
      .join("")}<td><b>${attack ? fmt(score(t), 1) : fmt(score(t), 2)}</b></td></tr>`)
    .join("");

  const maxA = Math.max(...teams.map((t) => t.attack)), maxD = Math.max(...teams.map((t) => t.defence));
  $("#ratings").innerHTML = `<div class="bars">${[...teams].sort((a, b) => b.attack / b.defence - a.attack / a.defence)
    .map((t) => `<div class="bar-row"><b>${t.short}</b>
      <div class="bar" title="Attack ${t.attack}"><i style="width:${(t.attack / maxA) * 100}%;background:var(--good)"></i></div>
      <div class="bar" title="Defence ${t.defence} (lower is better)"><i style="width:${(t.defence / maxD) * 100}%;background:var(--bad)"></i></div></div>`)
    .join("")}</div><p class="hint" style="margin-top:8px">Green: attack. Red: goals conceded (shorter is better). Home opponents are in CAPS, away in lowercase.</p>`;
}
$("#fx-mode").addEventListener("click", (e) => {
  const b = e.target.closest("button"); if (!b) return;
  $$("#fx-mode button").forEach((x) => x.classList.toggle("on", x === b));
  state.fxMode = b.dataset.mode;
  renderFixtures();
});
$("#fx-n").addEventListener("input", renderFixtures);

// ---------------------------------------------------------------- planner: team

function renderTeamSummary() {
  const el = $("#team-summary");
  if (state.scratch) {
    el.innerHTML = `<div class="team-card"><div class="name">New squad</div><div class="muted">£100m budget. The first gameweek is treated as a wildcard.</div></div>`;
    $("#ft-field").hidden = true; $("#bank-field").hidden = false;
    return;
  }
  const t = state.team;
  if (!t) { el.innerHTML = ""; $("#ft-field").hidden = false; return; }
  $("#ft-field").hidden = false; $("#bank-field").hidden = false;
  el.innerHTML = `<div class="team-card">
    <div class="name">${esc(t.name)}</div>
    <div class="muted">${esc(t.manager)} · ${t.total_points} pts · rank ${t.overall_rank ? t.overall_rank.toLocaleString() : "–"}</div>
    <div class="stats">
      <div class="stat"><b>£${fmt(t.bank)}m</b><span>Bank</span></div>
      <div class="stat"><b>${t.free_transfers}</b><span>Free transfers</span></div>
      <div class="stat"><b>£${fmt(t.squad_value)}m</b><span>Sell value</span></div>
    </div>
    <p class="hint">Free transfers are worked out from your history. Change them below if you have already made transfers this week.</p>
  </div>`;
}

$("#load-team").onclick = async () => {
  const id = $("#entry-id").value.trim();
  if (!/^\d+$/.test(id)) return toast("Enter your numeric FPL team ID.");
  busy("Loading your team…");
  try {
    state.team = await api("/api/team", { entry_id: id });
    state.scratch = false;
    store.set("entry", id);
    $("#ft").value = state.team.free_transfers;
    $("#bank").value = state.team.bank;
    renderTeamSummary();
    renderChips();
    toast(`Loaded ${state.team.name}`, true);
  } catch (e) { toast(e.message); } finally { busy(); }
};
$("#entry-id").addEventListener("keydown", (e) => e.key === "Enter" && $("#load-team").click());
$("#scratch").onclick = () => {
  state.scratch = true; state.team = null;
  $("#bank").value = 100;
  renderTeamSummary(); renderChips();
};

// ---------------------------------------------------------------- planner: chips

function chipWindow(gw) { return gw <= 19 ? 1 : 2; }

function renderChips() {
  if (!state.proj) return;
  const h = +$("#horizon").value || 5;
  const gws = state.proj.gws.slice(0, h);
  const used = (state.team?.chips_used || []);
  const prev = Object.fromEntries($$("#chips select").map((s) => [s.dataset.chip, s.value]));
  $("#chips").innerHTML = CHIPS.map((c) => {
    const avail = gws.filter((g) => !used.some((u) => u.name === c.key && chipWindow(u.event) === chipWindow(g)) && !(c.key === "wildcard" && state.scratch && g === gws[0]));
    const usedAll = !avail.length;
    return `<div class="chip-row ${usedAll ? "used" : ""}"><span>${c.label}</span>
      <select data-chip="${c.key}" ${usedAll ? "disabled" : ""}><option value="">${usedAll ? "Used / n/a" : "Don't plan"}</option>
      ${avail.map((g) => `<option value="${g}" ${prev[c.key] == g ? "selected" : ""}>GW${g}</option>`).join("")}</select></div>`;
  }).join("");
}

function chipPlan() {
  const out = {};
  for (const s of $$("#chips select")) {
    if (!s.value) continue;
    if (out[s.value]) throw new Error(`Only one chip can be played per gameweek (GW${s.value}).`);
    out[s.value] = s.dataset.chip;
  }
  return out;
}

// ---------------------------------------------------------------- planner: lock/ban

function findPlayer(text) {
  const m = text.match(/^(.*) \((\w+), (\w+)\)$/);
  if (m) return state.proj.players.find((p) => p.name === m[1] && p.team_short === m[2] && p.pos === m[3]);
  const t = text.toLowerCase();
  return state.proj.players.find((p) => p.name.toLowerCase() === t);
}
function addLB(kind) {
  const p = findPlayer($("#player-search").value.trim());
  if (!p) return toast("Pick a player from the list.");
  state.locked = state.locked.filter((x) => x !== p.id);
  state.banned = state.banned.filter((x) => x !== p.id);
  state[kind].push(p.id);
  $("#player-search").value = "";
  renderLB();
}
function renderLB() {
  const pill = (id, kind) => `<span class="pill ${kind === "locked" ? "lock" : "ban"}">${kind === "locked" ? "🔒" : "🚫"} ${esc(state.byId[id]?.name)}<button data-kind="${kind}" data-id="${id}">×</button></span>`;
  $("#lock-ban").innerHTML = state.locked.map((i) => pill(i, "locked")).join("") + state.banned.map((i) => pill(i, "banned")).join("");
}
$("#add-lock").onclick = () => addLB("locked");
$("#add-ban").onclick = () => addLB("banned");
$("#lock-ban").addEventListener("click", (e) => {
  const b = e.target.closest("button"); if (!b) return;
  state[b.dataset.kind] = state[b.dataset.kind].filter((x) => x !== +b.dataset.id);
  renderLB();
});

// ---------------------------------------------------------------- planner: run

$("#run").onclick = async () => {
  if (!state.team && !state.scratch) return toast("Load your team first, or build a squad from scratch.");
  let chips;
  try { chips = chipPlan(); } catch (e) { return toast(e.message); }
  const body = {
    squad: state.scratch ? null : state.team.squad,
    bank: +$("#bank").value,
    free_transfers: +$("#ft").value,
    horizon: +$("#horizon").value,
    max_hits: +$("#max-hits").value,
    transfer_penalty: +$("#tr-penalty").value,
    decay: +$("#decay").value,
    chips,
    locked: state.locked,
    banned: state.banned,
    minutes_override: state.overrides,
  };
  busy("Optimising, which can take up to a minute…");
  try {
    state.plan = await api("/api/optimise", body);
    state.planGw = 0;
    renderPlan();
  } catch (e) { toast(e.message); } finally { busy(); }
};

function renderPlan() {
  const r = state.plan;
  const plan = r.plan;
  const nTransfers = plan.filter((g) => !["wildcard", "freehit"].includes(g.chip)).reduce((a, g) => a + g.transfers_in.length, 0);
  const nHits = plan.reduce((a, g) => a + g.hits, 0);
  const gain = r.baseline_xpts != null ? r.total_xpts - r.baseline_xpts : null;

  const summary = `<div class="summary">
    <div class="card"><div class="label">Projected points, GW${r.gws[0]}–${r.gws[r.gws.length - 1]}</div><div class="big">${fmt(r.total_xpts)}</div><div class="hint">after hits</div></div>
    ${gain != null ? `<div class="card"><div class="label">Gain vs no transfers</div><div class="big ${gain > 0 ? "good" : ""}">${gain >= 0 ? "+" : ""}${fmt(gain)}</div><div class="hint">no transfers: ${fmt(r.baseline_xpts)}</div></div>` : ""}
    <div class="card"><div class="label">Transfers planned</div><div class="big">${nTransfers}</div><div class="hint">${nHits ? `${nHits} hit${nHits > 1 ? "s" : ""} (−${nHits * 4})` : "no hits"}</div></div>
    <div class="card"><div class="label">GW${plan[0].gw} captain</div><div class="big" style="font-size:20px">${esc(state.byId[plan[0].captain]?.name)}</div><div class="hint">vice: ${esc(state.byId[plan[0].vice]?.name)}</div></div>
  </div>`;

  const strip = `<div class="gw-strip">${plan.map((g, i) => {
    const tags = [];
    if (g.chip) tags.push(`<span class="badge">${CHIP_SHORT[g.chip]}</span>`);
    if (g.hits) tags.push(`<span class="badge hit">−${g.hits * 4}</span>`);
    const moves = g.chip === "freehit" ? "Free Hit squad" : g.transfers_in.length ? `${g.transfers_in.length} transfer${g.transfers_in.length > 1 ? "s" : ""}` : "Roll transfer";
    return `<button class="gw-btn card ${i === state.planGw ? "on" : ""}" data-i="${i}">
      <div class="t"><span>GW${g.gw}</span><span>${tags.join(" ")}</span></div>
      <div class="s">${moves}</div><div class="s"><b style="color:var(--text)">${fmt(g.net_xpts)}</b> xPts${r.baseline_by_gw ? ` · ${fmt(r.baseline_by_gw[i])} without transfers` : ""}</div></button>`;
  }).join("")}</div>`;

  $("#results").innerHTML = summary + strip + `<div id="gw-detail"></div>`;
  $$(".gw-btn").forEach((b) => (b.onclick = () => { state.planGw = +b.dataset.i; $$(".gw-btn").forEach((x) => x.classList.toggle("on", x === b)); renderGw(); }));
  renderGw();
}

function renderGw() {
  const g = state.plan.plan[state.planGw];
  // squad going into this week (skipping back over a free hit, which reverts)
  let k = state.planGw - 1;
  while (k >= 0 && state.plan.plan[k].chip === "freehit") k--;
  const prevIds = new Set(k < 0
    ? (state.scratch ? [] : state.team.squad.map((s) => s.id))
    : [...state.plan.plan[k].xi, ...state.plan.plan[k].bench].map((p) => p.id));
  const isNew = (id) => !prevIds.has(id) && !(state.scratch && state.planGw === 0);

  let tr;
  if (g.chip === "freehit") {
    tr = `<p class="muted">Free Hit: this squad is for one week only. Your squad goes back to how it was afterwards.</p>`;
  } else if (state.scratch && state.planGw === 0) {
    tr = `<p class="muted">Your starting squad. £${fmt(g.bank)}m left in the bank.</p>`;
  } else if (!g.transfers_in.length) {
    tr = `<p class="muted">No transfers. Roll the free transfer${g.free_transfers ? ` (${g.free_transfers} available → ${Math.min(5, g.free_transfers + 1)} next week)` : ""}.</p>`;
  } else {
    const outs = [...g.transfers_out].sort((a, b) => a.pos_id - b.pos_id);
    const ins = [...g.transfers_in].sort((a, b) => a.pos_id - b.pos_id);
    tr = outs.map((o, i) => {
      const n = ins[i];
      return `<div class="tr"><div class="out"><b>${esc(o.name)}</b><small>${o.team} · ${o.pos} · sell £${fmt(o.sell)}m · ${fmt(o.xpts)} xPts</small></div>
        <div class="arrow">→</div>
        <div class="in"><b>${esc(n.name)}</b><small>${n.team} · ${n.pos} · £${fmt(n.price)}m · ${fmt(n.xpts)} xPts</small></div></div>`;
    }).join("");
  }
  const ft = g.chip === "wildcard" || g.chip === "freehit" ? (g.chip === "wildcard" ? "Wildcard: unlimited free transfers" : "") :
    `${g.free_transfers} free transfer${g.free_transfers === 1 ? "" : "s"} available${g.hits ? ` · ${g.hits} hit${g.hits > 1 ? "s" : ""} (−${g.hits * 4})` : ""}`;

  const shirt = (p, bench = false) => `<div class="pl">
      <div class="shirt ${p.pos_id === 1 ? "gk" : ""}">${p.team}${p.id === g.captain ? `<span class="c">${g.chip === "3xc" ? "T" : "C"}</span>` : p.id === g.vice ? `<span class="c">V</span>` : ""}${isNew(p.id) ? `<span class="new">+</span>` : ""}</div>
      <div class="n">${esc(p.name)}</div><div class="x">${fmt(p.id === g.captain && !bench ? p.xpts * (g.chip === "3xc" ? 3 : 2) : p.xpts)}</div></div>`;
  const lines = [1, 2, 3, 4].map((pos) => `<div class="line">${g.xi.filter((p) => p.pos_id === pos).map((p) => shirt(p)).join("")}</div>`).join("");
  const benchHtml = g.bench.length ? `<div class="line bench">${g.bench.map((p) => shirt(p, true)).join("")}</div><div class="bench-label">Bench (in order)</div>` : "";

  $("#gw-detail").innerHTML = `<div class="gw-detail">
    <div class="pitch">${lines}${benchHtml}</div>
    <div class="card transfers">
      <h3>GW${g.gw} transfers</h3>
      <p class="hint">${ft}</p>
      ${tr}
      <h3>After this week</h3>
      <p>Bank: <b>£${fmt(g.bank)}m</b></p>
      <p>Expected points: <b>${fmt(g.xpts)}</b>${g.hits ? ` − ${g.hits * 4} = <b>${fmt(g.net_xpts)}</b>` : ""}</p>
      <p class="hint">${g.chip === "bboost" ? "Bench Boost: all 15 players score." : "Captain points are doubled on the pitch view. + marks a new signing."}</p>
    </div>
  </div>`;
}

// ---------------------------------------------------------------- refresh / boot

$("#refresh").onclick = async () => {
  busy("Re-downloading FPL data, which takes about 15s…");
  try { await api("/api/refresh"); await loadProjections(); toast("Data refreshed", true); }
  catch (e) { toast(e.message); } finally { busy(); }
};

(async function boot() {
  const tab = store.get("tab", "planner");
  $(`.tab[data-tab="${tab}"]`)?.click();
  $("#entry-id").value = store.get("entry", "");
  busy("Loading projections…");
  try {
    await loadProjections();
    if ($("#entry-id").value) $("#load-team").click();
  } catch (e) { toast(e.message); } finally { if (!$("#entry-id").value) busy(); }
})();

/* WareBot dashboard (UI layer): only shows results and sends actions to the Flask API. */
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const ALGOS = ["BFS", "DFS", "A*"];
const COLORS = { "BFS": "#6cc4ff", "DFS": "#ffb84d", "A*": "#b9f23c" };
const STATUSES = ["Pending", "Assigned", "In Transit", "Delivered"];
const AGENT_KEY = { SimpleReflexAgent: "reflex", GoalBasedAgent: "goal", LearningAgent: "learning" };
const AGENT_NAME = { reflex: "Simple reflex", goal: "Goal-based", learning: "Learning" };
const REDUCED = matchMedia("(prefers-reduced-motion: reduce)").matches;

const S = {
  state: null, mode: "path", algo: "A*", start: null, goal: null,
  overlay: { explored: [], path: [], ex: 0, px: 0 },
  analytics: null, metric: "avg_nodes", cell: 30,
};

/* ---------- helpers ---------- */
const esc = (v) => String(v).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const fmtCell = (c) => (c ? `[${c[0]}, ${c[1]}]` : "-");
const same = (a, b) => a && b && a[0] === b[0] && a[1] === b[1];

async function api(path, method = "GET", data) {
  const opt = { method, headers: {} };
  if (data !== undefined) { opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(data); }
  const res = await fetch(path, opt);
  const json = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(json.error || `Request failed (${res.status})`);
  return json;
}

let toastTimer;
function toast(msg, bad = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.toggle("bad", bad);
  t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), 3600);
}

async function attempt(fn, btn) {
  if (btn) btn.disabled = true;
  try { return await fn(); }
  catch (e) { toast(e.message, true); }
  finally { if (btn) btn.disabled = false; }
}

async function loadState() {
  S.state = await api("/api/state");
  renderAll();
}

/* ---------- tabs ---------- */
function showTab(name) {
  $$(".tabs button").forEach((b) => b.setAttribute("aria-selected", b.dataset.tab === name));
  $$(".tab").forEach((t) => (t.hidden = t.id !== "tab-" + name));
  if (name === "warehouse") drawMap();
  if (name === "analytics" && !S.analytics) loadAnalytics();
}
$$(".tabs button").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));

/* ---------- overview ---------- */
function renderOverview() {
  const k = S.state.kpis, orders = S.state.orders;
  $("#kpis").innerHTML = [
    ["Total orders", k.total_orders, ""],
    ["Delivered", k.delivered, "hot"],
    ["Robots active", `${k.robots_active} / ${k.robots_total}`, ""],
    ["Average path length", k.avg_path_length, ""],
  ].map(([l, v, c]) => `<div class="kpi ${c}"><b>${esc(v)}</b><span>${l}</span></div>`).join("");

  const counts = Object.fromEntries(STATUSES.map((s) => [s, orders.filter((o) => o.status === s).length]));
  const max = Math.max(1, ...Object.values(counts));
  $("#statusChart").innerHTML = `<div class="bars">${STATUSES.map((s) =>
    `<div class="bar"><span>${s}</span><div class="track"><div class="fill s-${s}" style="width:${(counts[s] / max) * 100}%"></div></div><em>${counts[s]}</em></div>`
  ).join("")}</div>`;

  const log = [...S.state.log].reverse();
  $("#log").innerHTML = log.length ? log.map((l) => `<li>${esc(l)}</li>`).join("") : `<li class="empty">Nothing has happened yet. Press Run all robots.</li>`;
}

/* ---------- orders ---------- */
function renderOrders() {
  $("#pipeline").innerHTML = STATUSES.map((s) => {
    const list = S.state.orders.filter((o) => o.status === s).sort((a, b) => b.priority - a.priority);
    const cards = list.map((o) =>
      `<div class="ocard"><b>#${o.order_id}</b> priority ${o.priority}<small>${fmtCell(o.source)} to ${fmtCell(o.destination)}</small></div>`).join("");
    return `<div class="col c-${s}"><h2>${s}<span>${list.length}</span></h2>${cards || `<p class="empty">No orders</p>`}</div>`;
  }).join("");
}

$("#orderForm").addEventListener("submit", (e) => {
  e.preventDefault();
  const f = new FormData(e.target), n = (k) => Number(f.get(k));
  attempt(async () => {
    const o = await api("/api/orders", "POST", { source: [n("sr"), n("sc")], destination: [n("dr"), n("dc")], priority: n("pr") });
    toast(`Order #${o.order_id} added`);
    await loadState();
  }, $("#orderForm button[type=submit]"));
});
$("#btnRandomOrder").addEventListener("click", (e) => attempt(async () => {
  const o = await api("/api/orders", "POST", { random: true });
  toast(`Order #${o.order_id} added`);
  await loadState();
}, e.currentTarget));

/* ---------- robots ---------- */
function renderRobots() {
  $("#robots").innerHTML = S.state.robots.map((r) => {
    const a = r.agent, key = AGENT_KEY[a.type] || "goal", batt = Math.round(r.battery);
    const level = batt < 20 ? "low" : batt < 50 ? "mid" : "";
    const t = r.type === "TransportRobot";
    const extra = key === "learning" ? `<span>Routes learned: ${a.routes_learned ?? 0}</span><span>Busy cells: ${(a.busy_cells || []).length}</span>` : "";
    const delivered = a.delivered !== undefined ? `<span>Jobs done: ${a.delivered}</span>` : "";
    return `<div class="panel rcard" data-id="${r.id}">
      <header><div class="avatar ${t ? "t" : ""}">${r.id}</div>
        <div><strong>Robot ${r.id}</strong><small>${t ? "Transport robot" : "Picker robot"}</small></div>
        <span class="badge ${esc(r.status)}">${esc(r.status)}</span></header>
      <div class="batt"><span>Battery</span><div class="track"><div class="fill ${level}" style="width:${batt}%"></div></div><b>${batt}%</b></div>
      <div class="form-grid">
        <label>Agent<select data-f="agent">${Object.entries(AGENT_NAME).map(([k, v]) => `<option value="${k}" ${k === key ? "selected" : ""}>${v}</option>`).join("")}</select></label>
        <label>Algorithm<select data-f="algorithm" ${key === "reflex" ? "disabled" : ""}>${ALGOS.map((x) => `<option ${x === a.algorithm ? "selected" : ""}>${x}</option>`).join("")}</select></label>
      </div>
      <div class="row">
        <button class="btn primary" data-do="step">Step</button>
        <button class="btn" data-do="charge">Charge</button>
        <button class="btn ghost" data-do="rescue">Rescue to station</button>
      </div>
      <div class="rmeta"><span>Position ${fmtCell(r.position)}</span>${delivered}${extra}</div>
      ${key === "reflex" ? `<p class="hint" style="margin-top:8px">A reflex agent only reacts (stops at obstacles, charges when low). It does not plan routes.</p>` : ""}
    </div>`;
  }).join("");
}

$("#robots").addEventListener("change", (e) => {
  const sel = e.target.closest("select[data-f]");
  if (!sel) return;
  const id = sel.closest(".rcard").dataset.id;
  attempt(async () => {
    await api(`/api/robots/${id}/config`, "POST", { [sel.dataset.f]: sel.value });
    await loadState();
  });
});

$("#robots").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-do]");
  if (!btn) return;
  const id = btn.closest(".rcard").dataset.id, what = btn.dataset.do;
  attempt(async () => {
    if (what === "step") {
      const { result, state } = await api(`/api/robots/${id}/step`, "POST");
      S.state = state;
      if (result.path && result.path.length) { playOverlay([], result.path); }
      if (result.action === "process_order") {
        toast(result.success ? `Robot ${id}: order #${result.order_id} is now ${result.status}`
                             : `Robot ${id}: order #${result.order_id} failed (${result.reason})`, !result.success);
      } else {
        toast(`Robot ${id}: ${result.action.replace("_", " ")}${result.reason ? " (" + String(result.reason).replace("_", " ") + ")" : ""}`);
      }
      renderAll();
    } else {
      const out = await api(`/api/robots/${id}/charge`, "POST", what === "rescue" ? { rescue: true } : {});
      toast(out.ok ? `Robot ${id} charged` : `Robot ${id} cannot charge: ${String(out.reason).replace("_", " ")}`, !out.ok);
      await loadState();
    }
  }, btn);
});

$("#runAll").addEventListener("click", (e) => attempt(async () => {
  const { results, state } = await api("/api/run", "POST");
  S.state = state;
  const last = [...results].reverse().find((r) => r.path && r.path.length);
  if (last) playOverlay([], last.path);
  const ok = results.filter((r) => r.success).length;
  toast(results.length ? `${ok} of ${results.length} jobs finished` : "Nothing to do: no open orders for the robots");
  renderAll();
}, e.currentTarget));

/* ---------- warehouse map ---------- */
const canvas = $("#map"), ctx = canvas.getContext("2d");
let timer = null;

function sizeCanvas() {
  const wh = S.state.warehouse, wrap = $("#mapwrap");
  const avail = Math.max(240, Math.min(wrap.clientWidth || 600, 680));
  S.cell = Math.max(14, Math.floor(avail / Math.max(wh.rows, wh.cols)));
  const dpr = window.devicePixelRatio || 1;
  canvas.style.width = wh.cols * S.cell + "px";
  canvas.style.height = wh.rows * S.cell + "px";
  canvas.width = wh.cols * S.cell * dpr;
  canvas.height = wh.rows * S.cell * dpr;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
}

function drawMap() {
  if (!S.state || $("#tab-warehouse").hidden) return;
  sizeCanvas();
  const wh = S.state.warehouse, c = S.cell, o = S.overlay;
  const mid = (cell) => [cell[1] * c + c / 2, cell[0] * c + c / 2];
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = "#14171d"; ctx.fillRect(0, 0, wh.cols * c, wh.rows * c);

  for (let r = 0; r < wh.rows; r++) for (let q = 0; q < wh.cols; q++) {
    const x = q * c, y = r * c;
    if (wh.grid[r][q] === 1) {
      ctx.fillStyle = "#3a4151"; ctx.fillRect(x + 1, y + 1, c - 2, c - 2);
      ctx.fillStyle = "#4a5266"; ctx.fillRect(x + 3, y + 3, c - 6, Math.max(2, c * 0.16));
    } else {
      ctx.fillStyle = "#1b1f27"; ctx.fillRect(x + 1, y + 1, c - 2, c - 2);
      const tc = wh.traffic_cost[`${r},${q}`];
      if (tc) { ctx.fillStyle = `rgba(255,184,77,${Math.min(0.6, 0.15 + tc * 0.1)})`; ctx.fillRect(x + 1, y + 1, c - 2, c - 2); }
    }
  }
  // explored cells
  ctx.fillStyle = "rgba(108,196,255,.38)";
  for (let i = 0; i < o.ex; i++) { const [r, q] = o.explored[i]; ctx.fillRect(q * c + 1, r * c + 1, c - 2, c - 2); }
  // path line
  if (o.px > 0) {
    ctx.strokeStyle = "#b9f23c"; ctx.lineWidth = Math.max(3, c * 0.2); ctx.lineJoin = "round"; ctx.lineCap = "round";
    ctx.beginPath();
    o.path.slice(0, o.px).forEach((cell, i) => { const [x, y] = mid(cell); i ? ctx.lineTo(x, y) : ctx.moveTo(x, y); });
    ctx.stroke();
  }
  // charging station
  const cs = wh.charging_station;
  ctx.strokeStyle = "#b9f23c"; ctx.lineWidth = 2; ctx.strokeRect(cs[1] * c + 3, cs[0] * c + 3, c - 6, c - 6);
  ctx.fillStyle = "#b9f23c"; ctx.font = `600 ${Math.floor(c * 0.42)}px sans-serif`; ctx.textAlign = "center"; ctx.textBaseline = "middle";
  ctx.fillText("C", cs[1] * c + c / 2, cs[0] * c + c / 2 + 1);
  // orders (open ones)
  S.state.orders.filter((od) => od.status !== "Delivered").forEach((od) => {
    const [sx, sy] = mid(od.source), [dx, dy] = mid(od.destination), m = c * 0.2;
    ctx.fillStyle = "#ffb84d"; ctx.fillRect(sx - m, sy - m, m * 2, m * 2);
    ctx.strokeStyle = "#ffb84d"; ctx.lineWidth = 2; ctx.beginPath(); ctx.arc(dx, dy, c * 0.26, 0, 7); ctx.stroke();
  });
  // start / goal picks
  [[S.start, "#ffffff", "S"], [S.goal, "#ff6f61", "G"]].forEach(([cell, col, ch]) => {
    if (!cell) return;
    const [x, y] = mid(cell);
    ctx.fillStyle = col; ctx.beginPath(); ctx.arc(x, y, c * 0.34, 0, 7); ctx.fill();
    ctx.fillStyle = "#13151a"; ctx.font = `700 ${Math.floor(c * 0.4)}px sans-serif`; ctx.fillText(ch, x, y + 1);
  });
  // robots
  S.state.robots.forEach((r) => {
    const [x, y] = mid(r.position), t = r.type === "TransportRobot";
    ctx.fillStyle = t ? "#6cc4ff" : "#b9f23c"; ctx.strokeStyle = "#13151a"; ctx.lineWidth = 2;
    ctx.beginPath(); ctx.arc(x, y, c * 0.3, 0, 7); ctx.fill(); ctx.stroke();
    ctx.fillStyle = "#10140a"; ctx.font = `700 ${Math.floor(c * 0.36)}px sans-serif`; ctx.fillText(r.id, x, y + 1);
  });
  // grid hint for large cells
  $("#pickHint").textContent = S.mode === "edit" ? "Click a cell to add or remove a shelf"
    : !S.start ? "Click a start cell" : !S.goal ? "Click a goal cell" : "Click again to pick a new start";
}

function playOverlay(explored, path) {
  clearInterval(timer);
  const o = S.overlay;
  Object.assign(o, { explored: explored || [], path: path || [], ex: 0, px: 0 });
  if (REDUCED) { o.ex = o.explored.length; o.px = o.path.length; drawMap(); return; }
  const per = Math.max(1, Math.ceil(o.explored.length / 80));
  timer = setInterval(() => {
    if (o.ex < o.explored.length) o.ex = Math.min(o.explored.length, o.ex + per);
    else if (o.px < o.path.length) o.px++;
    else clearInterval(timer);
    drawMap();
  }, 22);
  drawMap();
}

function clearOverlay() { clearInterval(timer); Object.assign(S.overlay, { explored: [], path: [], ex: 0, px: 0 }); }

function cellAt(ev) {
  const rect = canvas.getBoundingClientRect(), wh = S.state.warehouse;
  const col = Math.floor((ev.clientX - rect.left) / S.cell), row = Math.floor((ev.clientY - rect.top) / S.cell);
  return row >= 0 && col >= 0 && row < wh.rows && col < wh.cols ? [row, col] : null;
}

canvas.addEventListener("mousemove", (e) => { const c = cellAt(e); $("#hoverCell").textContent = c ? `Cell ${fmtCell(c)}${S.state.warehouse.grid[c[0]][c[1]] ? " (shelf)" : ""}` : "Hover a cell"; });
canvas.addEventListener("mouseleave", () => ($("#hoverCell").textContent = "Hover a cell"));

canvas.addEventListener("click", (e) => {
  const cell = cellAt(e);
  if (!cell) return;
  if (S.mode === "edit") {
    attempt(async () => {
      const out = await api("/api/obstacles/toggle", "POST", { cell });
      S.state.warehouse = out.warehouse;
      clearOverlay(); S.start = S.goal = null;
      drawMap();
    });
    return;
  }
  if (S.state.warehouse.grid[cell[0]][cell[1]] === 1) { toast("That cell is a shelf. Pick a free cell.", true); return; }
  if (!S.start || (S.start && S.goal)) { S.start = cell; S.goal = null; clearOverlay(); drawMap(); return; }
  S.goal = cell; drawMap();
  runPath();
});

function runPath() {
  attempt(async () => {
    const res = await api("/api/path", "POST", { algorithm: S.algo, start: S.start, goal: S.goal });
    showResult(res);
    playOverlay(res.explored_order, res.path);
  });
}

function showResult(res) {
  const box = $("#result");
  if (!res.found) {
    box.innerHTML = `<p><b>${esc(res.algorithm)}</b> found no path.</p><p class="hint">The goal may be walled in by shelves. Try Edit shelves, or pick another goal.</p>`;
    return;
  }
  box.innerHTML = `<p><b>${esc(res.algorithm)}</b> from ${fmtCell(S.start)} to ${fmtCell(S.goal)}</p>
    <div class="stats">
      <div><b>${res.nodes_explored}</b><span>Nodes explored</span></div>
      <div><b>${res.path_length}</b><span>Path length</span></div>
      <div><b>${res.cost}</b><span>Path cost</span></div>
      <div><b>${Number(res.time_ms).toFixed(3)}</b><span>Time (ms)</span></div>
    </div>`;
}

function needPoints() {
  if (S.start && S.goal) return true;
  toast("Pick a start and a goal on the map first.", true);
  return false;
}

$$(".seg button").forEach((b) => b.addEventListener("click", () => {
  S.mode = b.dataset.mode;
  $$(".seg button").forEach((x) => x.setAttribute("aria-pressed", x === b));
  drawMap();
}));

$("#algoSel").addEventListener("change", (e) => attempt(async () => {
  S.algo = e.target.value;
  await api("/api/algorithm", "POST", { algorithm: S.algo });
  toast(`All robots now use ${S.algo}`);
  await loadState();
  if (S.start && S.goal) runPath();
}));

$("#btnCompare").addEventListener("click", (e) => {
  if (!needPoints()) return;
  attempt(async () => {
    const res = await api("/api/compare", "POST", { start: S.start, goal: S.goal });
    const rows = ALGOS.map((a) => res[a]);
    const best = (k) => Math.min(...rows.filter((r) => r.found).map((r) => r[k]));
    $("#result").innerHTML = `<p>Same start and goal, three searches. Click a row to replay it.</p>
      <table class="cmp"><tr><th>Algorithm</th><th>Nodes</th><th>Length</th><th>Cost</th><th>ms</th></tr>
      ${rows.map((r) => `<tr class="pick-row" data-a="${esc(r.algorithm)}"><td>${esc(r.algorithm)}</td>
        <td class="${r.nodes_explored === best("nodes_explored") ? "best" : ""}">${r.nodes_explored}</td>
        <td class="${r.path_length === best("path_length") ? "best" : ""}">${r.found ? r.path_length : "-"}</td>
        <td class="${r.cost === best("cost") ? "best" : ""}">${r.found ? r.cost : "-"}</td>
        <td>${Number(r.time_ms).toFixed(3)}</td></tr>`).join("")}</table>`;
    $$("#result .pick-row").forEach((tr) => tr.addEventListener("click", () => playOverlay(res[tr.dataset.a].explored_order, res[tr.dataset.a].path)));
    playOverlay(res["A*"].explored_order, res["A*"].path);
  }, e.currentTarget);
});

$("#btnScan").addEventListener("click", (e) => attempt(async () => {
  const res = await api("/api/scan", "POST", { start: S.start || S.state.warehouse.charging_station });
  playOverlay(res.explored_order, []);
  $("#result").innerHTML = `<p><b>DFS scan</b> from ${fmtCell(res.start)}</p><div class="stats"><div><b>${res.cells_visited}</b><span>Cells visited</span></div></div>`;
}, e.currentTarget));

$("#btnNearest").addEventListener("click", (e) => attempt(async () => {
  const res = await api("/api/nearest", "POST", { start: S.start || S.state.warehouse.charging_station });
  if (!res.found) { toast("No pending order can be reached from there.", true); return; }
  playOverlay([], res.path);
  $("#result").innerHTML = `<p><b>BFS nearest order</b></p><div class="stats"><div><b>#${res.order_id}</b><span>Closest pickup</span></div><div><b>${res.path.length - 1}</b><span>Steps away</span></div></div>`;
}, e.currentTarget));

$("#btnClear").addEventListener("click", () => { clearOverlay(); S.start = S.goal = null; $("#result").innerHTML = `<p class="empty">No search yet. Click a start cell and a goal cell.</p>`; drawMap(); });

$("#density").addEventListener("input", (e) => ($("#densOut").textContent = Number(e.target.value).toFixed(2)));
$("#btnShelves").addEventListener("click", (e) => attempt(async () => {
  S.state = { ...S.state, ...(await api("/api/obstacles/generate", "POST", { density: Number($("#density").value) })) };
  clearOverlay(); S.start = S.goal = null; renderAll();
}, e.currentTarget));
$("#btnReset").addEventListener("click", (e) => attempt(async () => {
  const n = Number($("#sizeSel").value);
  S.state = await api("/api/reset", "POST", { rows: n, cols: n, density: Number($("#density").value), seed: Math.floor(Math.random() * 100000) });
  clearOverlay(); S.start = S.goal = null; S.analytics = null; renderAll();
  toast("New warehouse created");
}, e.currentTarget));
window.addEventListener("resize", drawMap);

/* ---------- analytics ---------- */
async function loadAnalytics(btn) {
  $("#benchChart").innerHTML = `<p class="empty">Running the benchmark on random grids...</p>`;
  await attempt(async () => {
    S.analytics = await api("/api/analytics");
    renderAnalytics();
  }, btn);
}

function renderAnalytics() {
  const a = S.analytics, m = S.metric;
  const dens = Object.keys(a.benchmark).sort((x, y) => x - y);
  const label = { avg_nodes: "Nodes explored", avg_length: "Path length", avg_time_ms: "Time (ms)" }[m];
  const val = (d, al) => Number(((a.benchmark[d] || {})[al] || {})[m] ?? 0);
  const maxV = Math.max(0.0001, ...dens.flatMap((d) => ALGOS.map((al) => val(d, al))));
  const W = 760, H = 300, L = 48, B = 36, T = 14, gw = (W - L - 10) / dens.length, bw = Math.min(34, (gw - 22) / 3);
  const y = (v) => T + (H - T - B) * (1 - v / maxV);
  const fmt = (v) => (m === "avg_time_ms" ? v.toFixed(2) : v >= 100 ? Math.round(v) : v.toFixed(1));
  let svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${label} by obstacle density">`;
  for (let i = 0; i <= 4; i++) {
    const v = (maxV * i) / 4, yy = y(v);
    svg += `<line x1="${L}" x2="${W}" y1="${yy}" y2="${yy}" stroke="#2d323d"/><text x="${L - 8}" y="${yy + 4}" text-anchor="end">${fmt(v)}</text>`;
  }
  dens.forEach((d, i) => {
    const x0 = L + i * gw + (gw - bw * 3 - 8) / 2;
    ALGOS.forEach((al, j) => {
      const v = val(d, al), x = x0 + j * (bw + 4);
      svg += `<rect x="${x}" y="${y(v)}" width="${bw}" height="${Math.max(1, H - B - y(v))}" rx="3" fill="${COLORS[al]}"><title>${al}: ${fmt(v)}</title></rect>`;
      svg += `<text x="${x + bw / 2}" y="${y(v) - 5}" text-anchor="middle">${fmt(v)}</text>`;
    });
    svg += `<text x="${L + i * gw + gw / 2}" y="${H - 12}" text-anchor="middle">Density ${d}</text>`;
  });
  svg += `</svg>`;
  $("#benchChart").innerHTML = `<div class="legend">${ALGOS.map((al) => `<span><i style="background:${COLORS[al]}"></i>${al}</span>`).join("")}</div>${svg}`;
  $("#benchNote").textContent = `${label}, averaged over random 15 x 15 grids. Lower is better. A* and BFS find the shortest path; DFS may take a much longer route.`;

  const live = Object.entries(a.live);
  $("#liveTable").innerHTML = live.length
    ? `<table class="tbl"><tr><th>Algorithm</th><th>Jobs</th><th>Avg steps</th><th>Avg nodes</th></tr>${live.map(([n, r]) => `<tr><td>${esc(n)}</td><td>${r.runs}</td><td>${r.avg_steps}</td><td>${r.avg_nodes}</td></tr>`).join("")}</table>`
    : `<p class="empty">No jobs finished yet. Press Run all robots, then reopen this tab.</p>`;
}

$("#metricSel").addEventListener("change", (e) => { S.metric = e.target.value; if (S.analytics) renderAnalytics(); });
$("#btnBench").addEventListener("click", (e) => loadAnalytics(e.currentTarget));
$$(".tabs button").forEach((b) => b.addEventListener("click", () => { if (b.dataset.tab === "analytics" && S.analytics) attempt(async () => { S.analytics = await api("/api/analytics"); renderAnalytics(); }); }));

/* ---------- boot ---------- */
function renderAll() { renderOverview(); renderOrders(); renderRobots(); drawMap(); }
attempt(loadState);

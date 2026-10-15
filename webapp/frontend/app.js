"use strict";

const $ = (sel) => document.querySelector(sel);
const fmt = new Intl.NumberFormat("en-US");
const charts = {};
const PAGE = 100; // table rows rendered per scroll step

const state = {
  result: null,
  rows: [],          // trial rows after the state/text filters and sort
  shown: 0,
  stateFilter: null, // full state name
  textFilter: "",
  sort: { key: null, dir: 1 },
};

// ---------- search ----------

const form = $("#search-form");
const conditionInput = $("#condition");

form.addEventListener("submit", (e) => {
  e.preventDefault();
  runSearch(conditionInput.value.trim());
});
$("#examples").addEventListener("click", (e) => {
  const q = e.target.closest("button")?.dataset.q;
  if (q) { conditionInput.value = q; runSearch(q); }
});
window.addEventListener("popstate", () => {
  const q = new URLSearchParams(location.search).get("condition");
  if (q) { conditionInput.value = q; runSearch(q, { push: false }); }
});

async function runSearch(condition, { push = true } = {}) {
  if (condition.length < 2) { conditionInput.reportValidity(); return; }
  if (push) history.pushState(null, "", `?condition=${encodeURIComponent(condition)}`);
  document.title = `${condition} · Trial Search`;
  showStatus("loading", condition);
  $("#search-button").disabled = true;
  try {
    const params = new URLSearchParams({ condition, source: $("#source").value, country: $("#country").value });
    const res = await fetch(`/api/search?${params}`);
    const body = await res.json();
    if (!res.ok) throw new Error(errorText(body));
    if (body.kpis.open_trials === 0) { showStatus("empty", condition); return; }
    render(body);
  } catch (err) {
    showStatus("error", condition, err.message);
  } finally {
    $("#search-button").disabled = false;
  }
}

function errorText(body) {
  if (typeof body.detail === "string") return body.detail;
  if (Array.isArray(body.detail)) return body.detail.map((d) => d.msg).join(". ");
  return "The search didn't complete.";
}

function showStatus(kind, condition, message = "") {
  const el = $("#status");
  $("#dashboard").hidden = true;
  el.hidden = false;
  el.className = `status-msg ${kind}`;
  const safe = escapeHtml(condition);
  if (kind === "loading") {
    el.innerHTML = `<div class="spinner" role="presentation"></div>
      <h2>Searching ClinicalTrials.gov for open US trials on “${safe}”</h2>
      <p>Broad conditions like cancer return thousands of trials and can take up to 10 seconds.</p>`;
  } else if (kind === "empty") {
    el.innerHTML = `<h2>No open US trials match “${safe}”</h2>
      <p>Check the spelling, or try a broader term such as “diabetes” instead of a specific drug name.</p>`;
  } else {
    el.innerHTML = `<h2>The search didn't complete</h2><p>${escapeHtml(message)}</p>
      <p>Try again in a moment.</p>`;
  }
}

// ---------- render ----------

function render(r) {
  state.result = r;
  state.stateFilter = null;
  state.textFilter = "";
  state.sort = { key: null, dir: 1 };
  $("#table-filter").value = "";
  $("#status").hidden = true;
  $("#dashboard").hidden = false;

  $("#result-title").textContent = `${fmt.format(r.kpis.open_trials)} open trials for “${r.condition}”`;
  const when = new Date(r.fetched_at).toLocaleString("en-US", { dateStyle: "medium", timeStyle: "short" });
  $("#result-note").textContent = r.capped
    ? `Showing the ${fmt.format(r.shown)} most relevant of ${fmt.format(r.total_matching)} matching trials. Live from ClinicalTrials.gov, ${when}.`
    : `Recruiting or not yet recruiting, with at least one US site. Live from ClinicalTrials.gov, ${when}.`;

  renderKpis(r.kpis);
  renderMap(r.by_state);
  applyTable();
  document.fonts.ready.then(() => renderCharts(r));
}

function renderKpis(k) {
  const pct = (x) => (x == null ? "–" : `${Math.round(x * 100)}%`);
  const tiles = [
    [fmt.format(k.open_trials), "Open trials", `${fmt.format(k.recruiting)} recruiting, ${fmt.format(k.not_yet_recruiting)} not yet`],
    [fmt.format(k.open_us_sites), "Open US sites", "Sites listed as recruiting, or with no status"],
    [k.states, "States", "With at least one open site"],
    [fmt.format(k.cities), "Cities", "With at least one open site"],
    [k.median_enrollment == null ? "–" : fmt.format(k.median_enrollment), "Median enrollment", "Participants each trial aims to enroll"],
    [pct(k.industry_share), "Industry sponsored", "Led by a company rather than an academic or government sponsor"],
  ];
  $("#kpis").innerHTML = tiles.map(([v, l, s]) =>
    `<div class="kpi"><span class="kpi-value">${v}</span><span class="kpi-label">${l}</span><span class="kpi-sub">${s}</span></div>`
  ).join("");
}

// ---------- tile map ----------

function renderMap(byState) {
  const counts = new Map(byState.map((s) => [s.state, s]));
  const max = Math.max(1, ...byState.map((s) => s.trials));
  // six buckets on a square-root scale, so mid-sized states still get distinct colors
  const bucket = (n) => (n ? Math.min(6, 1 + Math.floor(Math.sqrt(n / max) * 5.999)) : 0);

  const el = $("#tile-map");
  el.innerHTML = "";
  for (const [code, name, col, row] of window.US_TILES) {
    const s = counts.get(name);
    const n = s ? s.trials : 0;
    const b = document.createElement("button");
    b.type = "button";
    b.className = `tile${n ? ` s${bucket(n)}` : ""}`;
    b.style.gridColumn = col + 1;
    b.style.gridRow = row + 1;
    b.dataset.state = name;
    b.disabled = !n;
    b.title = n ? `${name}: ${fmt.format(n)} trials, ${fmt.format(s.open_sites)} open sites` : `${name}: no open trials`;
    b.setAttribute("aria-label", b.title);
    b.setAttribute("aria-pressed", "false");
    b.innerHTML = `<span class="code">${code}</span><span class="n">${n ? fmt.format(n) : ""}</span>`;
    b.addEventListener("click", () => setStateFilter(state.stateFilter === name ? null : name));
    el.appendChild(b);
  }

  const steps = [1, 2, 3, 4, 5, 6].map((i) => {
    const lo = Math.ceil(max * Math.pow((i - 1) / 6, 2)) || 1;
    return `<span><span class="swatch tile s${i}"></span>${fmt.format(lo)}+</span>`;
  });
  const unmapped = byState.filter((s) => !window.US_TILES.some((t) => t[1] === s.state));
  $("#map-legend").innerHTML = `<span>Trials:</span>${steps.join("")}` +
    (unmapped.length ? `<span>Also: ${unmapped.map((s) => `${escapeHtml(s.state)} (${s.trials})`).join(", ")}</span>` : "");
}

function setStateFilter(name) {
  state.stateFilter = name;
  document.querySelectorAll(".tile").forEach((t) => {
    const on = t.dataset.state === name;
    t.classList.toggle("selected", on);
    t.setAttribute("aria-pressed", String(on));
  });
  const chip = $("#state-chip");
  chip.hidden = !name;
  if (name) {
    chip.innerHTML = `${escapeHtml(name)} <button type="button" aria-label="Show all states">×</button>`;
    chip.querySelector("button").addEventListener("click", () => setStateFilter(null));
  }
  applyTable();
  if (name) $("#table-title").scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
}

// ---------- charts ----------

function css(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }

function chart(id) {
  if (!charts[id]) charts[id] = echarts.init(document.getElementById(id), null, { renderer: "svg" });
  return charts[id];
}

function baseOption() {
  return {
    textStyle: { fontFamily: css("--font"), color: css("--ink") },
    grid: { left: 8, right: 36, top: 8, bottom: 8, containLabel: true },
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" }, backgroundColor: css("--surface"),
               borderColor: css("--line"), textStyle: { color: css("--ink") } },
    animationDuration: 500,
  };
}

function hbar(id, items, { color = css("--accent"), labelWidth = 150 } = {}) {
  const data = [...items].reverse(); // largest on top
  chart(id).setOption({
    ...baseOption(),
    grid: { left: 14, right: 44, top: 4, bottom: 4, containLabel: true },
    xAxis: { type: "value", show: false },
    yAxis: { type: "category", data: data.map((d) => d.label), axisTick: { show: false },
             axisLine: { lineStyle: { color: css("--line") } },
             axisLabel: { color: css("--ink"), width: labelWidth, overflow: "truncate" } },
    series: [{ type: "bar", data: data.map((d) => d.count), barMaxWidth: 18,
               itemStyle: { color, borderRadius: [0, 4, 4, 0] },
               label: { show: true, position: "right", color: css("--muted"), formatter: (p) => fmt.format(p.value) } }],
  }, true);
}


function renderCharts(r) {
  hbar("chart-cities", r.top_cities.map((c) => ({ label: `${c.city}, ${stateCode(c.state)}`, count: c.trials })), { labelWidth: 130 });
  hbar("chart-conditions", r.top_conditions, { labelWidth: 220 });
  hbar("chart-phases", r.phases, { labelWidth: 100 });
  hbar("chart-sex", r.sex, { color: css("--second"), labelWidth: 90 });
  hbar("chart-ages", r.age_groups, { color: css("--second"), labelWidth: 120 });
  hbar("chart-interventions", r.intervention_types.slice(0, 5), { labelWidth: 120 });
  hbar("chart-sponsors", r.sponsor_classes.slice(0, 4), { color: css("--second"), labelWidth: 150 });
  renderTiming(r);
}

function renderTiming(r) {
  const now = new Date();
  const thisMonth = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
  const monthLabel = (m) => new Date(`${m}-01T00:00:00`).toLocaleString("en-US", { month: "short", year: "2-digit" });
  const starts = r.starts_by_month;
  const comps = r.completions_by_month;
  chart("chart-timing").setOption({
    ...baseOption(),
    legend: { top: 0, left: 0, textStyle: { color: css("--muted") }, itemWidth: 12, itemHeight: 12 },
    grid: [{ left: 8, right: "40%", top: 36, bottom: 8, containLabel: true },
           { left: "64%", right: 8, top: 36, bottom: 8, containLabel: true }],
    xAxis: [
      { gridIndex: 0, type: "category", data: starts.map((d) => monthLabel(d.label)), axisTick: { show: false },
        axisLine: { lineStyle: { color: css("--line") } }, axisLabel: { color: css("--muted"), interval: 5 } },
      { gridIndex: 1, type: "category", data: comps.map((d) => monthLabel(d.label)), axisTick: { show: false },
        axisLine: { lineStyle: { color: css("--line") } }, axisLabel: { color: css("--muted"), interval: 2 } },
    ],
    yAxis: [
      { gridIndex: 0, type: "value", splitLine: { lineStyle: { color: css("--line") } }, axisLabel: { color: css("--muted") } },
      { gridIndex: 1, type: "value", splitLine: { lineStyle: { color: css("--line") } }, axisLabel: { color: css("--muted") } },
    ],
    series: [
      { name: "Started", type: "bar", xAxisIndex: 0, yAxisIndex: 0, stack: "s", barMaxWidth: 14,
        data: starts.map((d) => (d.label <= thisMonth ? d.count : null)), itemStyle: { color: css("--accent"), borderRadius: [3, 3, 0, 0] } },
      { name: "Starting soon", type: "bar", xAxisIndex: 0, yAxisIndex: 0, stack: "s", barMaxWidth: 14,
        data: starts.map((d) => (d.label > thisMonth ? d.count : null)), itemStyle: { color: css("--future"), borderRadius: [3, 3, 0, 0] } },
      { name: "Primary completion due", type: "bar", xAxisIndex: 1, yAxisIndex: 1, barMaxWidth: 14,
        data: comps.map((d) => d.count), itemStyle: { color: css("--second"), borderRadius: [3, 3, 0, 0] } },
    ],
  }, true);
}

window.addEventListener("resize", () => Object.values(charts).forEach((c) => c.resize()));
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => state.result && renderCharts(state.result));

// ---------- table ----------

const PHASE_RANK = { "Early phase 1": 0, "Phase 1": 1, "Phase 2": 2, "Phase 3": 3, "Phase 4": 4, "No phase": 5 };

$("#table-filter").addEventListener("input", (e) => { state.textFilter = e.target.value.trim().toLowerCase(); applyTable(); });
document.querySelectorAll(".trials th[data-sort]").forEach((th) => {
  th.addEventListener("click", () => {
    const key = th.dataset.sort;
    state.sort = { key, dir: state.sort.key === key ? -state.sort.dir : 1 };
    document.querySelectorAll(".trials th").forEach((h) => h.removeAttribute("aria-sort"));
    th.setAttribute("aria-sort", state.sort.dir === 1 ? "ascending" : "descending");
    applyTable();
  });
});

function sortValue(t, key) {
  if (key === "phase") return t.phases.length ? Math.min(...t.phases.map((p) => PHASE_RANK[p] ?? 9)) : 9;
  return t[key];
}

function applyTable() {
  const r = state.result;
  if (!r) return;
  let rows = r.trials;
  if (state.stateFilter) rows = rows.filter((t) => t.states.includes(state.stateFilter));
  if (state.textFilter) {
    const q = state.textFilter;
    rows = rows.filter((t) => (`${t.id} ${t.title || ""} ${t.conditions.join(" ")}`).toLowerCase().includes(q));
  }
  if (state.sort.key) {
    const { key, dir } = state.sort;
    rows = [...rows].sort((a, b) => {
      const x = sortValue(a, key), y = sortValue(b, key);
      if (x == null && y == null) return 0;
      if (x == null) return 1; // blanks last either way
      if (y == null) return -1;
      return (x > y ? 1 : x < y ? -1 : 0) * dir;
    });
  }
  state.rows = rows;
  state.shown = 0;
  $("#table-body").innerHTML = "";
  $("#table-scroll").scrollTop = 0;
  const where = state.stateFilter ? ` with an open site in ${state.stateFilter}` : "";
  $("#table-count").textContent = `${fmt.format(rows.length)} of ${fmt.format(r.trials.length)} trials${where}`;
  if (!rows.length) {
    $("#table-body").innerHTML = `<tr class="table-empty"><td colspan="10">No trials match this filter. Clear the filter text${state.stateFilter ? " or the state" : ""} to see more.</td></tr>`;
    return;
  }
  renderMoreRows();
}

function renderMoreRows() {
  const next = state.rows.slice(state.shown, state.shown + PAGE);
  if (!next.length) return;
  const html = next.map((t) => `
    <tr>
      <td><a href="${t.url}" target="_blank" rel="noopener">${t.id}</a></td>
      <td>${escapeHtml(t.title || "")}</td>
      <td><span class="pill ${t.status.toLowerCase().replace(/ /g, "-")}">${t.status}</span></td>
      <td class="nowrap">${t.phases.join(", ") || "–"}</td>
      <td class="cond">${escapeHtml(t.conditions.join("; "))}</td>
      <td class="num">${fmt.format(t.open_us_sites)}</td>
      <td>${t.states.length > 4 ? `${t.states.slice(0, 4).map(stateCode).join(", ")} +${t.states.length - 4}` : t.states.map(stateCode).join(", ")}</td>
      <td class="num">${t.enrollment == null ? "–" : fmt.format(t.enrollment)}</td>
      <td class="nowrap">${t.start_date || "–"}</td>
      <td class="nowrap">${t.primary_completion_date || "–"}</td>
    </tr>`).join("");
  $("#table-body").insertAdjacentHTML("beforeend", html);
  state.shown += next.length;
}

new IntersectionObserver((entries) => {
  if (entries.some((e) => e.isIntersecting)) renderMoreRows();
}, { root: $("#table-scroll"), rootMargin: "400px" }).observe($("#table-sentinel"));

// ---------- helpers ----------

const CODE_BY_NAME = new Map(window.US_TILES.map(([code, name]) => [name, code]));
function stateCode(name) { return CODE_BY_NAME.get(name) || name || ""; }

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// Start from the URL so results can be bookmarked or refreshed
const initial = new URLSearchParams(location.search).get("condition");
if (initial) { conditionInput.value = initial; runSearch(initial, { push: false }); }

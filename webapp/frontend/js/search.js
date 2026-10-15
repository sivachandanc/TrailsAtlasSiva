"use strict";
// Condition search dashboard (/search?condition=...).

const PAGE = 100; // table rows rendered per scroll step
const PHASE_RANK = { "Early phase 1": 0, "Phase 1": 1, "Phase 2": 2, "Phase 3": 3, "Phase 4": 4, "No phase": 5 };

const state = {
  result: null,
  rows: [],          // trial rows after the state/text filters and sort
  shown: 0,
  stateFilter: null, // full state name
  textFilter: "",
  sort: { key: null, dir: 1 },
  map: null,
};

const condition = (new URLSearchParams(location.search).get("condition") || "").trim();
mountNav({ active: "search", withSearch: true, search: { mode: "condition", value: condition } });

if (condition) {
  runSearch(condition);
} else {
  $("#start").hidden = false;
  $(".searchbox input[name=q]").focus();
}

async function runSearch(cond) {
  document.title = `${cond} · Trial Atlas`;
  showLoading();
  try {
    const params = new URLSearchParams({ condition: cond, source: "clinicaltrials_gov", country: "United States" });
    const res = await fetch(`/api/search?${params}`);
    const body = await res.json();
    if (!res.ok) throw new Error(errorText(body));
    if (body.kpis.open_trials === 0) { showStatus("empty", cond); return; }
    render(body);
  } catch (err) {
    showStatus("error", cond, err.message);
  }
}

function errorText(body) {
  if (typeof body.detail === "string") return body.detail;
  if (Array.isArray(body.detail)) return body.detail.map((d) => d.msg).join(". ");
  return "The search didn't complete.";
}

function showLoading() {
  $("#dashboard").hidden = true;
  $("#skeleton").hidden = false;
  const el = $("#status");
  el.hidden = false;
  el.className = "status-msg loading";
  el.innerHTML = `<div class="spinner" role="presentation"></div>
    <h2>Searching ClinicalTrials.gov for open US trials on “${escapeHtml(condition)}”</h2>
    <p>Broad conditions like cancer return thousands of trials and can take up to 10 seconds.</p>`;
}

function showStatus(kind, cond, message = "") {
  $("#dashboard").hidden = true;
  $("#skeleton").hidden = true;
  const el = $("#status");
  el.hidden = false;
  el.className = `status-msg ${kind}`;
  if (kind === "empty") {
    el.innerHTML = `<h2>No open US trials match “${escapeHtml(cond)}”</h2>
      <p>Check the spelling, or try a broader term such as “diabetes” instead of a specific drug name.</p>`;
  } else {
    el.innerHTML = `<h2>The search didn't complete</h2><p>${escapeHtml(message)}</p><p>Try again in a moment.</p>`;
  }
}

// ---------- render ----------

function render(r) {
  state.result = r;
  $("#status").hidden = true;
  $("#skeleton").hidden = true;
  $("#dashboard").hidden = false;

  $("#result-title").textContent = `${fmt.format(r.kpis.open_trials)} open trials for “${r.condition}”`;
  const when = new Date(r.fetched_at).toLocaleString("en-US", { dateStyle: "medium", timeStyle: "short" });
  $("#result-note").textContent = r.capped
    ? `Showing the ${fmt.format(r.shown)} most relevant of ${fmt.format(r.total_matching)} matching trials. Live from ClinicalTrials.gov, ${when}.`
    : `Recruiting or not yet recruiting, with at least one US site. Live from ClinicalTrials.gov, ${when}.`;

  renderKpis(r.kpis);
  state.map = renderTileMap(
    $("#tile-map"), $("#map-legend"),
    new Map(r.by_state.map((s) => [s.state, { value: s.trials,
      title: `${s.state}: ${fmt.format(s.trials)} trials, ${fmt.format(s.open_sites)} open trial sites` }])),
    { unit: "trials", onSelect: setStateFilter },
  );
  applyTable();
  document.fonts.ready.then(() => renderCharts(r)); // measure chart labels with the real font
}

$("#copy-link").addEventListener("click", (e) => copyText(location.href, e.currentTarget));

function renderKpis(k) {
  const pct = (x) => (x == null ? "–" : `${Math.round(x * 100)}%`);
  const tiles = [
    [fmt.format(k.open_trials), "Open trials", `${fmt.format(k.recruiting)} recruiting, ${fmt.format(k.not_yet_recruiting)} not yet`],
    [fmt.format(k.open_us_sites), "Open trial sites", "One per trial at each open US location; a hospital running 3 trials counts 3 times"],
    [k.states, "States", "With at least one open site"],
    [fmt.format(k.cities), "Cities", "With at least one open site"],
    [k.median_enrollment == null ? "–" : fmt.format(k.median_enrollment), "Median enrollment", "Participants each trial aims to enroll"],
    [pct(k.industry_share), "Industry sponsored", "Led by a company rather than an academic or government sponsor"],
  ];
  $("#kpis").innerHTML = tiles.map(([v, l, s]) =>
    `<div class="kpi"><span class="kpi-value">${v}</span><span class="kpi-label">${l}</span><span class="kpi-sub">${s}</span></div>`
  ).join("");
}

function setStateFilter(name) {
  state.stateFilter = name;
  state.map.select(name);
  const chip = $("#state-chip");
  chip.hidden = !name;
  if (name) {
    chip.innerHTML = `${escapeHtml(name)} <button type="button" aria-label="Show all states">×</button>`;
    chip.querySelector("button").addEventListener("click", () => setStateFilter(null));
  }
  applyTable();
  if (name) $("#table-title").scrollIntoView({ behavior: reducedMotion() ? "auto" : "smooth", block: "start" });
}

// ---------- charts ----------

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
  const axis = (data, interval) => ({ type: "category", data, axisTick: { show: false },
    axisLine: { lineStyle: { color: css("--line") } }, axisLabel: { color: css("--muted"), interval } });
  const value = { type: "value", splitLine: { lineStyle: { color: css("--line") } }, axisLabel: { color: css("--muted") } };
  chart("chart-timing").setOption({
    ...baseOption(),
    legend: { top: 0, left: 0, textStyle: { color: css("--muted") }, itemWidth: 12, itemHeight: 12 },
    grid: [{ left: 8, right: "40%", top: 36, bottom: 8, containLabel: true },
           { left: "64%", right: 8, top: 36, bottom: 8, containLabel: true }],
    xAxis: [{ gridIndex: 0, ...axis(starts.map((d) => monthLabel(d.label)), 5) },
            { gridIndex: 1, ...axis(comps.map((d) => monthLabel(d.label)), 2) }],
    yAxis: [{ gridIndex: 0, ...value }, { gridIndex: 1, ...value }],
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

matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => state.result && renderCharts(state.result));

// ---------- table ----------

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
  $("#table-count").textContent = `${fmt.format(rows.length)} of ${fmt.format(r.trials.length)} trials${where}. Select an NCT ID to open the trial.`;
  if (!rows.length) {
    $("#table-body").innerHTML = `<tr class="table-empty"><td colspan="10">No trials match this filter. Clear the filter text${state.stateFilter ? " or the state" : ""} to see more.</td></tr>`;
    return;
  }
  renderMoreRows();
}

function renderMoreRows() {
  const next = state.rows.slice(state.shown, state.shown + PAGE);
  if (!next.length) return;
  const from = encodeURIComponent(condition);
  const html = next.map((t) => `
    <tr>
      <td><a href="/trials/${t.id}?from=${from}">${t.id}</a>
        <a class="ext" href="${t.url}" target="_blank" rel="noopener" aria-label="${t.id} on ClinicalTrials.gov" title="Open on ClinicalTrials.gov">↗</a></td>
      <td><a class="row-title" href="/trials/${t.id}?from=${from}">${escapeHtml(t.title || "")}</a></td>
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

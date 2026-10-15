"use strict";
// Single-trial dashboard (/trials/NCT...).

const OPEN = new Set(["RECRUITING", "NOT_YET_RECRUITING"]);
const SITE_STATUS = {
  RECRUITING: "Recruiting", NOT_YET_RECRUITING: "Not yet recruiting", ACTIVE_NOT_RECRUITING: "Active, not recruiting",
  ENROLLING_BY_INVITATION: "By invitation", COMPLETED: "Completed", WITHDRAWN: "Withdrawn",
  TERMINATED: "Terminated", SUSPENDED: "Suspended",
};
const ARM_TYPES = {
  EXPERIMENTAL: "Experimental", ACTIVE_COMPARATOR: "Active comparator", PLACEBO_COMPARATOR: "Placebo comparator",
  SHAM_COMPARATOR: "Sham comparator", NO_INTERVENTION: "No intervention", OTHER: "Other",
};

const rawId = decodeURIComponent(location.pathname.split("/").pop() || "");
const from = new URLSearchParams(location.search).get("from");
const view = { data: null, siteRows: [], stateFilter: null, text: "", sort: { key: null, dir: 1 }, map: null };

mountNav({ active: "lookup", withSearch: true, search: { mode: "nct", value: rawId } });

if (from) {
  const back = $("#back");
  back.href = `/search?condition=${encodeURIComponent(from)}`;
  back.textContent = `Back to results for “${from}”`;
  back.hidden = false;
}

load(rawId);

async function load(id) {
  document.title = `${id} · Trial Atlas`;
  $("#skeleton").hidden = false;
  try {
    const res = await fetch(`/api/trials/${encodeURIComponent(id)}`);
    const body = await res.json();
    if (!res.ok) throw Object.assign(new Error(detailText(body)), { status: res.status });
    render(body);
  } catch (err) {
    showError(err);
  } finally {
    $("#skeleton").hidden = true;
  }
}

function detailText(body) {
  if (typeof body.detail === "string") return body.detail;
  return "The trial couldn't be loaded.";
}

function showError(err) {
  const el = $("#status");
  el.hidden = false;
  el.className = "status-msg error";
  const title = err.status === 404 ? "Trial not found" : err.status === 422 ? "That isn't a trial ID" : "The trial couldn't be loaded";
  el.innerHTML = `<h2>${title}</h2><p>${escapeHtml(err.message)}</p>
    <p>Search for another trial ID above, or <a href="/">search by condition</a>.</p>`;
}

// ---------- formatting ----------

function niceDate(value) {
  if (!value) return "Not stated";
  const [y, m, d] = value.split("-").map(Number);
  const dt = new Date(y, (m || 1) - 1, d || 1);
  return dt.toLocaleDateString("en-US", d ? { month: "short", day: "numeric", year: "numeric" } : { month: "short", year: "numeric" });
}
function sentence(value) { return value ? value.replace(/_/g, " ").toLowerCase().replace(/^\w/, (c) => c.toUpperCase()) : ""; }
function pill(status) {
  const label = SITE_STATUS[status] || (status ? sentence(status) : "Not listed");
  const cls = status ? status.toLowerCase().replace(/_/g, "-") : "unlisted";
  return `<span class="pill ${cls}">${label}</span>`;
}
function paragraphs(text) {
  return escapeHtml(text).split(/\n\s*\n/).map((p) => `<p>${p.replace(/\n/g, "<br>")}</p>`).join("");
}

// ---------- render ----------

function render(d) {
  view.data = d;
  const t = d.trial;
  document.title = `${t.id}: ${t.title || "Trial"} · Trial Atlas`;
  $("#dashboard").hidden = false;

  $("#trial-id").textContent = t.id;
  $("#copy-id").addEventListener("click", (e) => copyText(t.id, e.currentTarget));
  $("#copy-link").addEventListener("click", (e) => copyText(location.origin + location.pathname, e.currentTarget));
  $("#trial-status").innerHTML = pill(t.status);
  const tags = [...d.phase_labels, sentence(t.study_type), t.primary_purpose ? sentence(t.primary_purpose) : null].filter(Boolean);
  $("#trial-tags").innerHTML = tags.map((x) => `<span class="tag">${escapeHtml(x)}</span>`).join("");
  $("#trial-title").textContent = t.acronym ? `${t.title} (${t.acronym})` : t.title || t.id;
  const official = $("#trial-official");
  official.textContent = t.official_title && t.official_title !== t.title ? t.official_title : "";
  official.hidden = !official.textContent;
  $("#trial-sponsor").innerHTML = t.sponsor_name
    ? `Sponsored by <strong>${escapeHtml(t.sponsor_name)}</strong>${t.collaborators.length ? `, with ${escapeHtml(t.collaborators.join(", "))}` : ""}`
    : "";
  $("#ctgov-link").href = t.url;
  $("#eligibility-link").href = `/trials/${t.id}/eligibility`;
  renderHeaderConditions(t);

  $("#banners").innerHTML = d.banners.map((b) => `<p class="banner" role="status">${escapeHtml(b)}</p>`).join("");

  renderKpis(d);
  renderSummary(t);
  renderTimeline(d);
  renderWhere(d);
  renderEligibility(d);
  renderArms(t);
  renderOutcomes(t);
  renderConditions(t);
  renderContacts(t);
  initSites(d);

  const when = new Date(d.fetched_at).toLocaleString("en-US", { dateStyle: "medium", timeStyle: "short" });
  $("#fetched").textContent = `Live from ClinicalTrials.gov, ${when}. Last updated by the sponsor ${niceDate(t.last_update)}.`;
  document.fonts.ready.then(() => renderSiteStatus(d));
}

// The trial's focus, right under the title: the sponsor's conditions, each a search link.
// MeSH terms (NLM's standard names) show on hover and in the Conditions panel further down.
function renderHeaderConditions(t) {
  const el = $("#trial-conditions");
  if (!t.conditions.length && !t.mesh_terms.length) { el.hidden = true; return; }
  const names = t.conditions.length ? t.conditions : t.mesh_terms;
  const mesh = t.mesh_terms.length ? `Standard terms (MeSH): ${t.mesh_terms.join(", ")}` : "";
  el.innerHTML = `<span class="tc-label">${names.length === 1 ? "Condition" : "Conditions"}</span>` +
    names.map((c) => `<a class="tc" href="/search?condition=${encodeURIComponent(c)}"
      title="Search open trials for ${escapeHtml(c)}${mesh ? `
${escapeHtml(mesh)}` : ""}">${escapeHtml(c)}</a>`).join("");
}

function renderKpis(d) {
  const k = d.kpis;
  const tiles = [
    [k.enrollment == null ? "–" : fmt.format(k.enrollment), "Enrollment target", "Participants the trial aims to enroll"],
    [`${fmt.format(k.open_us_sites)}`, "Open US sites", `Of ${fmt.format(k.us_sites)} US site${k.us_sites === 1 ? "" : "s"}`],
    [k.states, "States", "With at least one US site"],
    [k.countries, "Countries", `${fmt.format(k.sites)} site${k.sites === 1 ? "" : "s"} in total`],
    [k.ages, "Ages", "As stated by the sponsor"],
    [k.sex, "Sex", d.trial.sex === "ALL" ? "Open to all sexes" : "Eligibility is limited by sex"],
  ];
  $("#kpis").innerHTML = tiles.map(([v, l, s]) =>
    `<div class="kpi"><span class="kpi-value ${String(v).length > 10 ? "kpi-value-text" : ""}">${escapeHtml(v)}</span><span class="kpi-label">${l}</span><span class="kpi-sub">${escapeHtml(s)}</span></div>`
  ).join("");
}

function renderSummary(t) {
  const el = $("#summary");
  if (!t.brief_summary) { $("#about-panel").hidden = true; return; }
  el.innerHTML = paragraphs(t.brief_summary);
  const toggle = $("#summary-toggle");
  requestAnimationFrame(() => {
    if (el.scrollHeight > el.clientHeight + 4) {
      toggle.hidden = false;
      toggle.addEventListener("click", () => {
        const open = el.classList.toggle("open");
        toggle.textContent = open ? "Show less" : "Show full summary";
      });
    } else {
      el.classList.add("open");
    }
  });
}

function renderTimeline(d) {
  $("#timeline-note").textContent = d.timeline_note || "";
  const points = d.timeline.filter((m) => m.position != null);
  const startPos = d.timeline.find((m) => m.label === "Start")?.position;
  const primaryPos = d.timeline.find((m) => m.label === "Primary completion")?.position;
  const endPos = d.timeline.find((m) => m.label === "Completion")?.position ?? primaryPos;
  const pct = (p) => `${(p * 100).toFixed(2)}%`;
  const segs = [];
  if (startPos != null && primaryPos != null) segs.push(`<span class="seg seg-main" style="left:${pct(startPos)};width:${pct(Math.max(0, primaryPos - startPos))}"></span>`);
  if (primaryPos != null && endPos != null && endPos > primaryPos) segs.push(`<span class="seg seg-tail" style="left:${pct(primaryPos)};width:${pct(endPos - primaryPos)}"></span>`);
  const dots = points.map((m, i) => `<span class="dot dot-${i}" style="left:${pct(m.position)}" title="${escapeHtml(m.label)}: ${niceDate(m.date)}"></span>`).join("");
  const today = d.today_position != null
    ? `<span class="today${d.today_position > 0.85 ? " late" : ""}" style="left:${pct(d.today_position)}"><span>Today</span></span>` : "";
  $("#timeline").innerHTML = `
    <div class="track">${segs.join("")}${dots}${today}</div>
    <dl class="milestones">
      ${d.timeline.map((m, i) => `<div><dt><span class="key dot-${i}"></span>${escapeHtml(m.label)}</dt><dd>${niceDate(m.date)}</dd></div>`).join("")}
    </dl>`;
}

function renderWhere(d) {
  const items = new Map(d.by_state.map((s) => [s.state, {
    value: s.sites, title: `${s.state}: ${fmt.format(s.sites)} site${s.sites === 1 ? "" : "s"}, ${fmt.format(s.open_sites)} open`,
  }]));
  view.map = renderTileMap($("#tile-map"), $("#map-legend"), items, { unit: "sites", onSelect: setStateFilter });
  if (!d.by_state.length) $("#where-note").textContent = "This trial has no sites in the United States.";
  $("#other-countries").textContent = d.other_countries.length
    ? `Outside the US: ${d.other_countries.map((c) => `${c.label} (${fmt.format(c.count)})`).join(", ")}.` : "";
}

function renderSiteStatus(d) {
  const counts = new Map();
  for (const s of d.trial.sites) {
    const label = SITE_STATUS[s.status] || (s.status ? sentence(s.status) : "Not listed");
    counts.set(label, (counts.get(label) || 0) + 1);
  }
  const items = [...counts].map(([label, count]) => ({ label, count })).sort((a, b) => b.count - a.count);
  const el = $("#chart-site-status");
  if (!items.length) { el.innerHTML = '<p class="muted">No sites listed.</p>'; return; }
  el.style.height = `${Math.min(260, 40 + items.length * 36)}px`; // short when there are only a few statuses
  hbar("chart-site-status", items, { labelWidth: 140 });
}

function renderEligibility(d) {
  const e = d.eligibility;
  const t = d.trial;
  const el = $("#eligibility");
  const population = t.study_population
    ? `<div class="population"><h4>Study population</h4>${paragraphs(t.study_population)}</div>` : "";
  if (!e.raw_text) {
    el.innerHTML = population || '<p class="muted">The sponsor hasn\'t published eligibility criteria for this trial.</p>';
    return;
  }
  if (!e.parsed) {
    $("#elig-note").textContent = "Shown as written: this trial's criteria aren't split into inclusion and exclusion.";
    el.innerHTML = `${population}<div class="raw">${paragraphs(e.raw_text)}</div>`;
    return;
  }
  $("#elig-note").textContent = `Ages ${d.kpis.ages.toLowerCase()}, sex: ${d.kpis.sex.toLowerCase()}. Criteria as written by the sponsor.`;
  const list = (items) => `<ul class="criteria">${items.map((c) =>
    `<li class="lvl-${Math.min(c.level, 2)}">${escapeHtml(c.text)}</li>`).join("")}</ul>`;
  const top = (items) => items.filter((c) => c.level === 0).length;
  el.innerHTML = `${population}
    <div class="elig-cols">
      <div class="elig-col include"><h4>Must have <span class="count">${top(e.inclusion)}</span></h4>${e.inclusion.length ? list(e.inclusion) : '<p class="muted">None listed.</p>'}</div>
      <div class="elig-col exclude"><h4>Must not have <span class="count">${top(e.exclusion)}</span></h4>${e.exclusion.length ? list(e.exclusion) : '<p class="muted">None listed.</p>'}</div>
    </div>`;
}

function renderArms(t) {
  const el = $("#arms");
  if (!t.arms.length && !t.interventions.length) { el.innerHTML = '<p class="muted">No arms or interventions listed.</p>'; return; }
  const byName = new Map(t.interventions.map((i) => [`${sentence(i.type)}: ${i.name}`.toLowerCase(), i]));
  const arms = t.arms.length ? t.arms : [{ label: "Interventions", type: null, description: null, interventions: t.interventions.map((i) => `${i.type}: ${i.name}`) }];
  el.innerHTML = arms.map((a) => {
    const ivs = a.interventions.map((n) => {
      const iv = byName.get(n.toLowerCase());
      const other = iv && iv.other_names.length ? `<span class="muted"> Also called ${escapeHtml(iv.other_names.join(", "))}.</span>` : "";
      const desc = iv && iv.description ? `<p class="iv-desc">${escapeHtml(iv.description)}</p>` : "";
      return `<li><strong>${escapeHtml(n)}</strong>${other}${desc}</li>`;
    }).join("");
    return `<div class="arm arm-${(a.type || "other").toLowerCase().replace(/_/g, "-")}">
      <div class="arm-head"><h4>${escapeHtml(a.label || "Arm")}</h4>${a.type ? `<span class="tag">${ARM_TYPES[a.type] || sentence(a.type)}</span>` : ""}</div>
      ${a.description ? `<p>${escapeHtml(a.description)}</p>` : ""}
      ${ivs ? `<ul class="ivs">${ivs}</ul>` : ""}
    </div>`;
  }).join("");
}

function renderOutcomes(t) {
  const block = (title, items) => items.length ? `
    <h4 class="outcome-group">${title} <span class="count">${items.length}</span></h4>
    <ol class="outcomes">${items.map((o) => `<li>
      <strong>${escapeHtml(o.measure || "Unnamed measure")}</strong>
      ${o.time_frame ? `<span class="muted">Measured: ${escapeHtml(o.time_frame)}</span>` : ""}
      ${o.description ? `<details><summary>How it's measured</summary><p>${escapeHtml(o.description)}</p></details>` : ""}</li>`).join("")}</ol>` : "";
  const html = block("Primary outcomes", t.primary_outcomes) + block("Secondary outcomes", t.secondary_outcomes);
  $("#outcomes").innerHTML = html || '<p class="muted">No outcome measures listed.</p>';
}

function renderConditions(t) {
  const chips = (items, cls = "") => items.map((x) => `<span class="chip-static ${cls}">${escapeHtml(x)}</span>`).join("");
  const mesh = t.mesh_terms.map((term, i) => (t.mesh_ids[i] ? `${term} (${t.mesh_ids[i]})` : term));
  $("#conditions").innerHTML = `
    <div class="chip-group"><h4>Conditions</h4>${t.conditions.length ? chips(t.conditions) : '<span class="muted">None listed</span>'}</div>
    ${mesh.length ? `<div class="chip-group"><h4>MeSH terms</h4>${chips(mesh, "mesh")}</div>` : ""}
    ${t.keywords.length ? `<div class="chip-group"><h4>Keywords</h4>${chips(t.keywords, "kw")}</div>` : ""}
    <p class="muted small-note">Search these conditions: ${t.conditions.slice(0, 3).map((c) =>
      `<a href="/search?condition=${encodeURIComponent(c)}">${escapeHtml(c)}</a>`).join(", ") || "none"}</p>`;
}

function renderContacts(t) {
  const el = $("#contacts");
  const contacts = t.contacts.map((c) => `
    <div class="contact">
      <strong>${escapeHtml(c.name || "Study team")}</strong>${c.role ? ` <span class="muted">${escapeHtml(sentence(c.role))}</span>` : ""}
      ${c.phone ? `<div class="copy-row"><span>${escapeHtml(c.phone)}${c.phone_ext ? ` ext. ${escapeHtml(c.phone_ext)}` : ""}</span><button type="button" class="ghost small" data-copy="${escapeHtml(c.phone)}">Copy</button></div>` : ""}
      ${c.email ? `<div class="copy-row"><span>${escapeHtml(c.email)}</span><button type="button" class="ghost small" data-copy="${escapeHtml(c.email)}">Copy</button></div>` : ""}
    </div>`).join("");
  const officials = t.officials.map((o) => `
    <div class="contact"><strong>${escapeHtml(o.name || "")}</strong> <span class="muted">${escapeHtml(sentence(o.role))}</span>
      ${o.affiliation ? `<div class="muted">${escapeHtml(o.affiliation)}</div>` : ""}</div>`).join("");
  el.innerHTML = (contacts || '<p class="muted">No central contact listed. Site contacts are on ClinicalTrials.gov.</p>') +
    (officials ? `<h4 class="officials-title">Study leadership</h4>${officials}` : "");
  el.querySelectorAll("[data-copy]").forEach((b) => b.addEventListener("click", () => copyText(b.dataset.copy, b)));
}

// ---------- sites table ----------

function siteIsOpen(s) { return OPEN.has(s.status) || (s.status == null && view.data.is_open); }

function initSites(d) {
  // Open sites first, then US before other countries
  view.siteRows = [...d.trial.sites].sort((a, b) =>
    (siteIsOpen(b) - siteIsOpen(a)) || ((b.country === "United States") - (a.country === "United States")) ||
    String(a.state || "").localeCompare(String(b.state || "")));
  $("#site-filter").addEventListener("input", (e) => { view.text = e.target.value.trim().toLowerCase(); renderSites(); });
  document.querySelectorAll(".trials th[data-sort]").forEach((th) => th.addEventListener("click", () => {
    const key = th.dataset.sort;
    view.sort = { key, dir: view.sort.key === key ? -view.sort.dir : 1 };
    document.querySelectorAll(".trials th").forEach((h) => h.removeAttribute("aria-sort"));
    th.setAttribute("aria-sort", view.sort.dir === 1 ? "ascending" : "descending");
    renderSites();
  }));
  renderSites();
}

function setStateFilter(name) {
  view.stateFilter = name;
  view.map.select(name);
  const chip = $("#state-chip");
  chip.hidden = !name;
  if (name) {
    chip.innerHTML = `${escapeHtml(name)} <button type="button" aria-label="Show all states">×</button>`;
    chip.querySelector("button").addEventListener("click", () => setStateFilter(null));
  }
  renderSites();
  if (name) $("#sites-title").scrollIntoView({ behavior: reducedMotion() ? "auto" : "smooth", block: "start" });
}

function renderSites() {
  let rows = view.siteRows;
  if (view.stateFilter) rows = rows.filter((s) => s.state === view.stateFilter && s.country === "United States");
  if (view.text) rows = rows.filter((s) => `${s.facility || ""} ${s.city || ""} ${s.zip || ""}`.toLowerCase().includes(view.text));
  if (view.sort.key) {
    const { key, dir } = view.sort;
    rows = [...rows].sort((a, b) => String(a[key] ?? "").localeCompare(String(b[key] ?? "")) * dir);
  }
  const total = view.siteRows.length;
  $("#sites-count").textContent = `${fmt.format(rows.length)} of ${fmt.format(total)} site${total === 1 ? "" : "s"}${view.stateFilter ? ` in ${view.stateFilter}` : ""}. Open sites first.`;
  $("#sites-body").innerHTML = rows.length ? rows.map((s) => `
    <tr class="${siteIsOpen(s) ? "" : "row-closed"}">
      <td>${escapeHtml(s.facility || "Unnamed site")}</td>
      <td>${escapeHtml(s.city || "")}</td>
      <td>${escapeHtml(s.state || "")}</td>
      <td class="nowrap">${escapeHtml(s.zip || "")}</td>
      <td>${escapeHtml(s.country || "")}</td>
      <td>${pill(s.status)}</td>
    </tr>`).join("")
    : `<tr class="table-empty"><td colspan="6">No sites match this filter.</td></tr>`;
}

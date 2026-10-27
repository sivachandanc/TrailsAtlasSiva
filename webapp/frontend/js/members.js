"use strict";
// Member dashboard (/trials/NCT.../members): what step 3's query finds on Databricks, and how near the
// trial's sites those members live. The page asks for aggregates only; names come only with the member list.

const trialId = decodeURIComponent(location.pathname.split("/")[2] || "");
const api = (path) => `/api/trials/${encodeURIComponent(trialId)}/filters/review/${path}`;
const MILE = 1609.344;
const SITE_STATUS = {
  RECRUITING: "Recruiting", NOT_YET_RECRUITING: "Not yet recruiting", ACTIVE_NOT_RECRUITING: "Active, not recruiting",
  ENROLLING_BY_INVITATION: "By invitation", COMPLETED: "Completed", WITHDRAWN: "Withdrawn",
  TERMINATED: "Terminated", SUSPENDED: "Suspended",
};
const LIST_LABELS = { member_id: "Member ID", first_nme: "First name", last_nme: "Last name", birth_dte: "Birth date",
  gender_cde: "Sex", state_or_province_cde: "State", zip_5_postal_cde: "ZIP", nearest_site: "Nearest open site",
  distance_miles: "Miles" };

const view = {
  data: null, map: null, layers: {}, ring: 25, tiles: null, sort: { key: null, dir: -1 }, stateFilter: null,
  text: "", tileMap: null, list: null, listMax: null,
};

mountNav({ active: "lookup", withSearch: true, search: { mode: "nct", value: trialId } });
$("#back").href = `/trials/${encodeURIComponent(trialId)}/eligibility#review`;
$("#trial-link").href = `/trials/${encodeURIComponent(trialId)}`;
$("#review-link").href = `/trials/${encodeURIComponent(trialId)}/eligibility#review`;
document.title = `${trialId} members · Trails-POC`;

$("#rerun").addEventListener("click", () => load());
load();

// ---------- load ----------

async function load() {
  const status = $("#status");
  status.className = "status-msg loading";
  status.innerHTML = `<div class="spinner" role="presentation"></div>
    <p>Running 3 queries on Databricks… A stopped warehouse can take a minute to start.</p>`;
  status.hidden = false;
  $("#skeleton").hidden = !!view.data;
  $("#rerun").disabled = true;
  try {
    const res = await fetch(api("dashboard"), { method: "POST" });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw Object.assign(new Error(typeof body.detail === "string" ? body.detail : `The queries didn't run (HTTP ${res.status}).`), { status: res.status });
    status.hidden = true;
    render(body);
  } catch (err) {
    status.className = "status-msg error";
    const title = err.status === 503 ? "Databricks isn't set up" : err.status === 404 ? "Trial not found" : "The queries didn't run";
    status.innerHTML = `<h2>${title}</h2><p>${escapeHtml(err.message)}</p>
      <p><a href="/trials/${encodeURIComponent(trialId)}/eligibility#review">Back to step 3</a>${err.status === 503 ? " shows what to add to .env." : ""}</p>`;
  } finally {
    $("#skeleton").hidden = true;
    $("#rerun").disabled = false;
  }
}

// ---------- formatting ----------

const plural = (n, word, many = `${word}s`) => `${fmt.format(n)} ${n === 1 ? word : many}`;
const pct = (n, of) => (of ? `${(100 * n / of).toFixed(n && n / of < 0.01 ? 2 : 1)}%` : "–");
const miles = (m) => (m == null ? "–" : `${m < 10 ? m.toFixed(1) : fmt.format(Math.round(m))} mi`);
function sentence(value) { return value ? value.replace(/_/g, " ").toLowerCase().replace(/^\w/, (c) => c.toUpperCase()) : ""; }
function pill(status, open) {
  const label = SITE_STATUS[status] || (status ? sentence(status) : open ? "Open (trial status)" : "Not listed");
  const cls = status ? status.toLowerCase().replace(/_/g, "-") : open ? "recruiting" : "unlisted";
  return `<span class="pill ${cls}">${escapeHtml(label)}</span>`;
}
function niceDate(value) {
  const [y, m, d] = String(value).split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}
function siteName(s) { return s.facility || `Site ${s.index + 1}`; }
function place(s) { return [s.city, s.state].filter(Boolean).join(", "); }

// Colours for distance bands, nearest first: the map's sequential scale, strongest for the nearest
const BAND_TOKENS = ["--s6", "--s5", "--s4", "--s3", "--s2", "--s1"];
function bandColor(i) { return i < 0 ? css("--muted") : css(BAND_TOKENS[Math.min(i, BAND_TOKENS.length - 1)]); }
const BAND_LIMITS = [10, 25, 50, 100, 250, Infinity];
function bandIndex(m) { return m == null ? -1 : BAND_LIMITS.findIndex((limit) => m <= limit); }

// ---------- render ----------

function render(d) {
  view.data = d;
  $("#dashboard").hidden = false;
  $("#trial-id").textContent = d.trial_id;
  $("#trial-status").innerHTML = `<span class="pill ${d.status_label.toLowerCase().replace(/[ ,]+/g, "-")}">${escapeHtml(d.status_label)}</span>`;
  $("#trial-tags").innerHTML = d.phase_labels.map((p) => `<span class="tag">${escapeHtml(p)}</span>`).join("");
  $("#trial-title").textContent = d.title || d.trial_id;
  const ran = new Date(d.ran_at);
  $("#ran-line").textContent = `Members matching the step 3 query, counted on Databricks at ${ran.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" })} `
    + `on ${ran.toLocaleDateString("en-US", { month: "short", day: "numeric" })}. Ages and time windows are measured from ${niceDate(d.reference_date)}`
    + `${d.criteria_built ? `; step 2 was built by ${d.model}.` : "."}`;
  $("#banners").innerHTML = d.banners.map((b) => `<div class="banner">${escapeHtml(b)}</div>`).join("");
  renderKpis(d);
  renderMap(d);
  renderDistance(d);
  renderFunnel(d);
  renderWho(d);
  initSites(d);
  renderStates(d);
  renderRules(d);
  resetList();
  $("#fetched").textContent = `Queries took ${(d.elapsed_ms / 1000).toFixed(1)} s on the warehouse. Distances are straight lines from the centre of each member's ZIP code `
    + "(US Census ZCTA) to the site. Nothing on this page is saved, cached or logged.";
}

function renderKpis(d) {
  const k = d.kpis;
  const ring = d.radii.includes(25) ? 25 : d.radii[Math.floor(d.radii.length / 2)];
  const near = k.within[String(ring)] || 0;
  const people = k.expert_checks + k.not_applied;
  const tiles = [
    [fmt.format(k.eligible), "Eligible members", `${pct(k.eligible, k.all_members)} of ${fmt.format(k.all_members)} members`, "accent"],
    [k.enrollment ? `${(k.eligible / k.enrollment).toFixed(k.eligible / k.enrollment < 10 ? 1 : 0)}×` : "–", "Of the enrollment target",
     k.enrollment ? `The trial aims to enroll ${fmt.format(k.enrollment)}` : "The trial has no enrollment target"],
    [fmt.format(k.open_us_sites), "Open US sites", `Of ${plural(k.us_sites, "US site")}`],
    [fmt.format(near), `Within ${ring} mi of a${d.distance_to === "open" ? "n open" : ""} site`, `${pct(near, k.located)} of members with a known ZIP`, "go"],
    [miles(k.median_miles), "Median distance", `To the nearest ${d.distance_to === "open" ? "open " : ""}site`],
    [fmt.format(people), "Rules a person checks", `${plural(k.expert_checks, "expert check")} · ${k.not_applied} not in the query`, people ? "soon" : ""],
  ];
  $("#kpis").innerHTML = tiles.map(([v, l, s, tone]) =>
    `<div class="kpi${tone ? ` kpi-${tone}` : ""}"><span class="kpi-value">${escapeHtml(v)}</span><span class="kpi-label">${escapeHtml(l)}</span><span class="kpi-sub">${escapeHtml(s)}</span></div>`
  ).join("");
}

// ---------- map ----------

function tileUrl() {
  const dark = matchMedia("(prefers-color-scheme: dark)").matches;
  // Esri's grey canvas basemaps: no key needed, and quiet enough for the data to stand out
  return `https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_${dark ? "Dark" : "Light"}_Gray_Base/MapServer/tile/{z}/{y}/{x}`;
}

function renderMap(d) {
  if (!view.map) {
    view.map = L.map("map", { preferCanvas: true, zoomSnap: 0.5, scrollWheelZoom: false, worldCopyJump: true });
    view.map.attributionControl.setPrefix(false);  // drop the "Leaflet" credit; the tile credit must stay (Esri and OSM terms)
    view.map.createPane("rings").style.zIndex = 410;
    view.map.createPane("sites").style.zIndex = 450;
    view.tiles = L.tileLayer(tileUrl(), {
      maxZoom: 16,
      attribution: '&copy; <a href="https://www.esri.com">Esri</a>, HERE, Garmin, &copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    }).addTo(view.map);
    view.map.on("focus", () => view.map.scrollWheelZoom.enable());
    view.map.on("blur", () => view.map.scrollWheelZoom.disable());
    $("#fit-sites").addEventListener("click", () => fitTo("sites"));
    $("#fit-members").addEventListener("click", () => fitTo("members"));
  }
  Object.values(view.layers).forEach((layer) => layer.remove());
  view.layers = {};
  view.sitePoints = {};

  // Member ZIP bubbles: area by members, colour by distance to the nearest site
  const max = Math.max(1, ...d.zips.map((z) => z.members));
  const zips = L.layerGroup();
  for (const z of [...d.zips].reverse()) {  // largest drawn last, on top
    const site = z.nearest_site != null ? d.sites[z.nearest_site] : null;
    L.circleMarker([z.lat, z.lon], {
      radius: 3 + Math.sqrt(z.members / max) * 15, weight: 0.6, color: css("--surface"),
      fillColor: bandColor(bandIndex(z.miles)), fillOpacity: 0.78,
    }).bindTooltip(`<strong>ZIP ${escapeHtml(z.zip)}</strong> · ${plural(z.members, "member")}<br>${
      site ? `${miles(z.miles)} to ${escapeHtml(siteName(site))}` : "No site with a location"}`, { sticky: true }).addTo(zips);
  }
  view.layers.zips = zips.addTo(view.map);

  // Sites: filled when open, hollow when not recruiting
  const sites = L.layerGroup();
  for (const s of d.sites) {
    if (s.lat == null) continue;
    const marker = L.circleMarker([s.lat, s.lon], {
      pane: "sites", radius: s.open ? 7 : 6, weight: s.open ? 2.5 : 2, color: s.open ? css("--surface") : css("--muted"),
      fillColor: s.open ? css("--go") : css("--surface"), fillOpacity: s.open ? 1 : 0.85, dashArray: s.open ? null : "2 3",
    }).bindPopup(() => sitePopup(s, d), { maxWidth: 320 }).addTo(sites);
    view.sitePoints[s.index] = marker;
  }
  view.layers.sites = sites.addTo(view.map);
  drawRings();
  renderRadiusToggle(d);

  const open = d.sites.filter((s) => s.open && s.lat != null).length;
  const closed = d.sites.filter((s) => !s.open && s.lat != null).length;
  $("#map-note").textContent = `${plural(d.zips.length, "ZIP code")} with eligible members, ${plural(open, "open site")}`
    + `${closed ? ` and ${plural(closed, "site")} not recruiting` : ""}. Click a site for details; scroll-zoom after clicking the map.`;
  const bands = d.distance_bands.filter((b) => b.label !== "Unknown");
  $("#map-legend").innerHTML = `<span><span class="md-dot md-dot-open"></span>Open site</span>
    <span><span class="md-dot md-dot-closed"></span>Not recruiting</span>
    <span class="md-legend-sep">Members, by distance:</span>${bands.map((b, i) =>
      `<span><span class="swatch" style="background:${bandColor(i)}"></span>${escapeHtml(b.label)}</span>`).join("")}`;
  requestAnimationFrame(() => { view.map.invalidateSize(); fitTo(d.sites.some((s) => s.lat != null) ? "sites" : "members"); });
}

function sitePopup(s, d) {
  const contact = s.contacts.find((c) => c.phone || c.email);
  const pi = s.contacts.find((c) => c.role === "PRINCIPAL_INVESTIGATOR");
  const within = d.radii.map((r) => `<tr><td>Within ${r} mi</td><td class="num">${fmt.format(s.within[String(r)] || 0)}</td></tr>`).join("");
  return `<div class="md-pop">
    <strong>${escapeHtml(siteName(s))}</strong>
    <span class="muted">${escapeHtml([place(s), s.zip].filter(Boolean).join(" "))}</span>
    <span>${pill(s.status, s.open)}${s.located_by === "zip" ? ' <span class="tag tag-approx">Placed by ZIP</span>' : ""}</span>
    <table>${within}${s.open ? `<tr><td>Nearest open site for</td><td class="num">${fmt.format(s.catchment)}</td></tr>` : ""}</table>
    ${s.open ? "" : '<span class="muted">Not recruiting: these members would be in reach if it opens.</span>'}
    ${pi ? `<span>PI: ${escapeHtml(pi.name)}</span>` : ""}
    ${contact ? `<span>${escapeHtml(contact.name || "Contact")}${contact.phone ? ` · <a href="tel:${escapeHtml(contact.phone)}">${escapeHtml(contact.phone)}</a>` : ""}${
      contact.email ? ` · <a href="mailto:${escapeHtml(contact.email)}">${escapeHtml(contact.email)}</a>` : ""}</span>` : ""}
  </div>`;
}

function drawRings() {
  view.layers.rings?.remove();
  const d = view.data;
  if (!view.ring) return;
  const rings = L.layerGroup();
  for (const s of d.sites) {
    if (s.lat == null || !(s.open || d.distance_to === "all")) continue;
    L.circle([s.lat, s.lon], { pane: "rings", radius: view.ring * MILE, color: css("--accent"), weight: 1.2,
      dashArray: "4 4", fillColor: css("--accent"), fillOpacity: 0.05, interactive: false }).addTo(rings);
  }
  view.layers.rings = rings.addTo(view.map);
}

function renderRadiusToggle(d) {
  const options = [0, ...d.radii];
  if (!options.includes(view.ring)) view.ring = d.radii[0];
  const el = $("#radius-toggle");
  el.innerHTML = options.map((r) => `<button type="button" role="radio" aria-checked="${r === view.ring}" data-r="${r}">${r ? `${r} mi` : "Off"}</button>`).join("");
  el.onclick = (e) => {
    const b = e.target.closest("button[data-r]");
    if (!b) return;
    view.ring = Number(b.dataset.r);
    el.querySelectorAll("button").forEach((x) => x.setAttribute("aria-checked", String(x === b)));
    drawRings();
  };
}

function fitTo(what) {
  const d = view.data;
  const points = what === "sites"
    ? d.sites.filter((s) => s.lat != null && (s.open || d.distance_to === "all")).map((s) => [s.lat, s.lon])
    : d.zips.map((z) => [z.lat, z.lon]);
  if (!points.length) { view.map.setView([39.5, -98.35], 4); return; }
  // One site in Hawaii or Alaska would zoom the map out over the Pacific: frame the lower 48 when they have points
  const lower48 = points.filter(([lat, lon]) => lat > 24 && lat < 50 && lon > -125 && lon < -66);
  view.map.fitBounds(L.latLngBounds(lower48.length ? lower48 : points).pad(0.08), { maxZoom: 10, animate: !reducedMotion() });
}

function focusSite(index) {
  const marker = view.sitePoints[index];
  if (!marker) return;
  $("#map-title").scrollIntoView({ behavior: reducedMotion() ? "auto" : "smooth", block: "start" });
  view.map.setView(marker.getLatLng(), Math.max(view.map.getZoom(), 9), { animate: !reducedMotion() });
  marker.openPopup();
}

// Map tiles and chart colours follow the system theme
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
  if (!view.data) return;
  view.tiles.setUrl(tileUrl());
  render(view.data);
});

// ---------- charts ----------

function renderDistance(d) {
  const bands = d.distance_bands;
  const known = bands.filter((b) => b.label !== "Unknown");
  const unknown = bands.find((b) => b.label === "Unknown");
  $("#dist-note").textContent = `Members by straight-line distance from their ZIP code to the nearest ${d.distance_to === "open" ? "open " : ""}site.`
    + `${unknown ? ` ${plural(unknown.members, "member")} with an unknown ZIP aren't shown.` : ""}`;
  chart("chart-distance").setOption({
    ...baseOption(),
    grid: { left: 8, right: 8, top: 28, bottom: 30, containLabel: true },
    legend: { top: 0, left: 0, itemWidth: 12, itemHeight: 8, textStyle: { color: css("--muted") }, data: ["Members", "Cumulative share"] },
    tooltip: { ...baseOption().tooltip, valueFormatter: undefined,
      formatter: (ps) => `<strong>${ps[0].name}</strong><br>${ps.map((p) => `${p.marker}${p.seriesName}: ${
        p.seriesType === "line" ? `${p.value}%` : fmt.format(p.value)}`).join("<br>")}` },
    xAxis: { type: "category", data: known.map((b) => b.label.replace(" mi", "").replace(" or more", "+")),
             axisTick: { show: false }, axisLine: { lineStyle: { color: css("--line") } },
             axisLabel: { color: css("--muted"), interval: 0, fontSize: 11 }, name: `Miles to the nearest ${d.distance_to === "open" ? "open " : ""}site`, nameLocation: "middle", nameGap: 30,
             nameTextStyle: { color: css("--muted") } },
    yAxis: [{ type: "value", splitLine: { lineStyle: { color: css("--line") } }, axisLabel: { color: css("--muted"), formatter: (v) => fmt.format(v) } },
            { type: "value", max: 100, show: false }],
    series: [
      { name: "Members", type: "bar", barMaxWidth: 34,
        data: known.map((b, i) => ({ value: b.members, itemStyle: { color: bandColor(i), borderRadius: [4, 4, 0, 0] } })) },
      { name: "Cumulative share", type: "line", yAxisIndex: 1, smooth: true, symbolSize: 6,
        data: known.map((b) => (b.cumulative_share == null ? null : Math.round(b.cumulative_share * 1000) / 10)),
        lineStyle: { color: css("--go"), width: 2 }, itemStyle: { color: css("--go") },
        label: { show: true, position: "top", color: css("--muted"), fontSize: 10, formatter: (p) => `${p.value}%` } },
    ],
  }, true);
}

function renderFunnel(d) {
  const top = Math.max(1, d.funnel[0].members);
  const review = `/trials/${encodeURIComponent(trialId)}/eligibility#review`;
  $("#funnel").innerHTML = d.funnel.map((s, i) => {
    const share = s.members / top;
    return `<li class="md-step${s.expert ? " md-step-expert" : ""}${i === d.funnel.length - 1 ? " md-step-last" : ""}">
      <div class="md-step-label">
        ${i ? `<a href="${review}">${escapeHtml(s.label)}</a>` : `<span>${escapeHtml(s.label)}</span>`}
        ${s.expert ? '<span class="tag tag-approx">Expert check</span>' : ""}
      </div>
      <div class="md-bar" role="img" aria-label="${fmt.format(s.members)} members, ${pct(s.members, top)} of all members">
        <span style="width:${Math.max(share * 100, s.members ? 0.6 : 0)}%"></span>
      </div>
      <div class="md-step-n"><strong>${fmt.format(s.members)}</strong>${i ? `<span class="muted">${s.dropped ? `−${fmt.format(s.dropped)}` : "no change"}</span>` : ""}</div>
    </li>`;
  }).join("");
}

function renderWho(d) {
  const total = d.sex.reduce((a, s) => a + s.count, 0);
  $("#who-note").textContent = d.median_age != null ? `Median age ${d.median_age} on ${niceDate(d.reference_date)}. Ages in 5-year bands.` : "No ages to show.";
  const tone = { Female: "--accent", Male: "--second" };
  $("#sex-split").innerHTML = total ? `<div class="md-sex-bar">${d.sex.map((s) =>
      `<span style="flex:${s.count};background:${css(tone[s.label] || "--muted")}" title="${escapeHtml(s.label)}: ${fmt.format(s.count)}"></span>`).join("")}</div>
    <div class="legend">${d.sex.map((s) => `<span><span class="swatch" style="background:${css(tone[s.label] || "--muted")}"></span>${escapeHtml(s.label)} ${fmt.format(s.count)} (${pct(s.count, total)})</span>`).join("")}</div>` : "";
  const bands = d.age_bands;
  const series = [["Female", "female", "--accent"], ["Male", "male", "--second"], ["Other or not recorded", "other", "--muted"]]
    .filter(([, key]) => bands.some((b) => b[key]))
    .map(([name, key, token]) => ({ name, type: "bar", stack: "age", barMaxWidth: 26, data: bands.map((b) => b[key]),
      itemStyle: { color: css(token) }, emphasis: { focus: "series" } }));
  chart("chart-age").setOption({
    ...baseOption(),
    grid: { left: 8, right: 8, top: 12, bottom: 8, containLabel: true },
    xAxis: { type: "category", data: bands.map((b) => b.label), axisTick: { show: false },
             axisLine: { lineStyle: { color: css("--line") } }, axisLabel: { color: css("--muted"), fontSize: 11 } },
    yAxis: { type: "value", splitLine: { lineStyle: { color: css("--line") } }, axisLabel: { color: css("--muted"), formatter: (v) => fmt.format(v) } },
    series,
  }, true);
}

// ---------- sites ----------

function siteColumns(d) {
  return [
    { key: "facility", label: "Site", cls: "col-title" },
    { key: "status", label: "Status" },
    ...d.radii.map((r) => ({ key: `w${r}`, label: `≤ ${r} mi`, num: true })),
    { key: "catchment", label: "Nearest site for", num: true },
  ];
}

function initSites(d) {
  view.sort = { key: null, dir: -1 };
  $("#sites-head").innerHTML = `<tr>${siteColumns(d).map((c) =>
    `<th data-sort="${c.key}" class="${c.num ? "num" : ""} ${c.cls || ""}">${escapeHtml(c.label)}</th>`).join("")}</tr>`;
  $("#sites-head").onclick = (e) => {
    const th = e.target.closest("th[data-sort]");
    if (!th) return;
    const key = th.dataset.sort;
    view.sort = { key, dir: view.sort.key === key ? -view.sort.dir : (key === "facility" || key === "status" ? 1 : -1) };
    document.querySelectorAll("#sites-head th").forEach((h) => h.removeAttribute("aria-sort"));
    th.setAttribute("aria-sort", view.sort.dir === 1 ? "ascending" : "descending");
    renderSites();
  };
  $("#site-filter").oninput = (e) => { view.text = e.target.value.trim().toLowerCase(); renderSites(); };
  $("#sites-body").onclick = (e) => {
    const row = e.target.closest("tr[data-site]");
    if (row && !e.target.closest("a")) focusSite(Number(row.dataset.site));
  };
  renderSites();
}

function siteValue(s, key) {
  if (key.startsWith("w")) return s.within[key.slice(1)] || 0;
  if (key === "status") return SITE_STATUS[s.status] || s.status || "";
  return s[key] ?? "";
}

function renderSites() {
  const d = view.data;
  const largest = d.radii[d.radii.length - 1];
  let rows = [...d.sites];
  if (view.stateFilter) rows = rows.filter((s) => s.state === view.stateFilter);
  if (view.text) rows = rows.filter((s) => `${s.facility || ""} ${s.city || ""}`.toLowerCase().includes(view.text));
  if (view.sort.key) {
    const { key, dir } = view.sort;
    rows.sort((a, b) => {
      const x = siteValue(a, key), y = siteValue(b, key);
      return (typeof x === "number" ? x - y : String(x).localeCompare(String(y))) * dir;
    });
  } else {
    // Open sites first, ranked by the members they're nearest to, then by reach
    rows.sort((a, b) => (b.open - a.open) || (b.catchment - a.catchment) || ((b.within[largest] || 0) - (a.within[largest] || 0)));
  }
  const topCatch = Math.max(1, ...d.sites.map((s) => s.catchment));
  const open = d.sites.filter((s) => s.open).length;
  $("#sites-note").textContent = `${fmt.format(rows.length)} of ${plural(d.sites.length, "US site")} (${fmt.format(open)} open)`
    + `${view.stateFilter ? ` in ${view.stateFilter}` : ""}. Members within each distance, and how many have it as their nearest open site. Select a row to see it on the map.`;
  $("#sites-body").innerHTML = rows.length ? rows.map((s) => `
    <tr data-site="${s.index}" class="md-site-row${s.open ? "" : " row-closed"}" tabindex="0">
      <td class="col-title"><span class="md-site-name">${escapeHtml(siteName(s))}</span>
        <span class="muted md-site-place">${escapeHtml(place(s))}${s.located_by === "zip" ? " · placed by ZIP" : s.located_by === "none" ? " · no location" : ""}</span></td>
      <td>${pill(s.status, s.open)}</td>
      ${d.radii.map((r) => `<td class="num">${fmt.format(s.within[String(r)] || 0)}</td>`).join("")}
      <td class="num md-catch">${s.open ? `<span class="md-mini"><span style="width:${(100 * s.catchment / topCatch).toFixed(1)}%"></span></span>${fmt.format(s.catchment)}` : '<span class="muted">If it opens</span>'}</td>
    </tr>`).join("")
    : `<tr class="table-empty"><td colspan="${siteColumns(d).length}">No sites match this filter.</td></tr>`;
}

$("#sites-body").addEventListener("keydown", (e) => {
  const row = e.target.closest("tr[data-site]");
  if (row && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); focusSite(Number(row.dataset.site)); }
});

function setStateFilter(name) {
  view.stateFilter = name;
  view.tileMap.select(name);
  const chip = $("#state-chip");
  chip.hidden = !name;
  if (name) {
    chip.innerHTML = `${escapeHtml(name)} <button type="button" aria-label="Show all states">×</button>`;
    chip.querySelector("button").addEventListener("click", () => setStateFilter(null));
  }
  renderSites();
  if (name) $("#sites-title").scrollIntoView({ behavior: reducedMotion() ? "auto" : "smooth", block: "start" });
}

function renderStates(d) {
  const items = new Map(d.by_state.filter((s) => s.members).map((s) => [s.state, {
    value: s.members, title: `${s.state}: ${plural(s.members, "member")}, ${plural(s.open_sites, "open site")}`,
  }]));
  view.stateFilter = null;
  $("#state-chip").hidden = true;
  view.tileMap = renderTileMap($("#tile-map"), $("#tile-legend"), items, { unit: "members", onSelect: setStateFilter });
}

// ---------- rules and statements ----------

function renderRules(d) {
  const k = d.kpis;
  const review = `/trials/${encodeURIComponent(trialId)}/eligibility#review`;
  const conditions = d.funnel.length - 1;
  $("#rules-note").textContent = d.criteria_built
    ? "The count is only as good as what claims can show. These parts need a person."
    : "Step 2 hasn't been built, so the count applies age and sex only.";
  const rows = [
    ["group-codable", plural(conditions, "condition"), "in the query", "Every member counted passes all of them."],
    ["group-expert", plural(k.expert_checks, "expert check"), "", "In the query, but an expert confirms the members they return (e.g. how long a drug was taken)."],
    ["group-needs_review", fmt.format(k.not_applied), "not in the query", "Claims can't answer them (labs, history, consent), so they remove no one. Check them per member."],
    ["group-flagged", fmt.format(k.flagged), "flagged", "Left out after failing a check. Review them before relying on the count."],
  ];
  $("#rules").innerHTML = rows.map(([cls, n, what, text]) => `
    <a class="md-rule" href="${review}"><span class="group-chip ${cls}">${escapeHtml(n)}${what ? ` ${what}` : ""}</span><span class="muted">${escapeHtml(text)}</span></a>`).join("");
  const names = { funnel: "Funnel: members left after each condition", demographics: "Age and sex", geography: "ZIP code and state" };
  $("#statements").innerHTML = Object.entries(d.statements).map(([key, sql]) => `
    <p class="small-note"><strong>${escapeHtml(names[key] || key)}</strong></p><pre class="code-block md-sql">${escapeHtml(sql)}</pre>`).join("");
}

// ---------- member list (on demand) ----------

function resetList() {
  view.list = null;
  $("#list-table").hidden = $("#list-csv").hidden = true;
  $("#list-distance").parentElement.hidden = true;
  $("#list-error").hidden = true;
  $("#list-load").textContent = "Load member list";
  $("#list-note").textContent = "Names stay in Databricks until you ask for them. The list isn't saved, cached or logged.";
}

$("#list-load").addEventListener("click", async () => {
  const button = $("#list-load");
  button.disabled = true;
  $("#list-error").hidden = true;
  const status = $("#list-status");
  status.hidden = false;
  status.innerHTML = `<div class="spinner" role="presentation"></div><p>Getting the member list from Databricks…</p>`;
  try {
    const res = await fetch(api("members"), { method: "POST" });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(typeof body.detail === "string" ? body.detail : `The list didn't load (HTTP ${res.status}).`);
    view.list = body;
    const select = $("#list-distance");
    select.innerHTML = `<option value="">any distance</option>${view.data.radii.map((r) => `<option value="${r}">${r} mi</option>`).join("")}`;
    select.value = view.listMax ?? "";
    select.onchange = () => { view.listMax = select.value || null; renderList(); };
    select.parentElement.hidden = false;
    $("#list-csv").hidden = false;
    $("#list-table").hidden = false;
    button.textContent = "Reload";
    renderList();
  } catch (err) {
    const el = $("#list-error");
    el.hidden = false;
    el.innerHTML = `<h2>The list didn't load</h2><p>${escapeHtml(err.message)}</p>`;
  } finally {
    status.hidden = true;
    button.disabled = false;
  }
});

function listRows() {
  const r = view.list;
  const at = r.columns.indexOf("distance_miles");
  if (!view.listMax) return r.rows;
  return r.rows.filter((row) => row[at] != null && Number(row[at]) <= Number(view.listMax));
}

function renderList() {
  const r = view.list;
  const rows = listRows();
  const at = r.columns.indexOf("distance_miles");
  $("#list-note").textContent = `${r.truncated ? `The first ${fmt.format(r.limit)} members by member ID (more match; run the query in Databricks for everyone).` : `${plural(r.rows.length, "member")}, by member ID.`}`
    + `${view.listMax ? ` Showing the ${fmt.format(rows.length)} within ${view.listMax} mi of an open site.` : ""}`;
  $("#list-head").innerHTML = `<tr>${r.columns.map((c, i) => `<th class="${i === at ? "num" : ""}">${escapeHtml(LIST_LABELS[c] || c)}</th>`).join("")}</tr>`;
  $("#list-body").innerHTML = rows.length
    ? rows.map((row) => `<tr>${row.map((v, i) => `<td class="${i === at ? "num" : ""}">${escapeHtml(v ?? (i === at ? "–" : ""))}</td>`).join("")}</tr>`).join("")
    : `<tr class="table-empty"><td colspan="${r.columns.length}">No members ${view.listMax ? "within this distance" : "match"}.</td></tr>`;
}

function csvField(v) {
  const s = v == null ? "" : String(v);
  return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

$("#list-csv").addEventListener("click", () => {
  if (!view.list) return;
  const csv = [view.list.columns, ...listRows()].map((row) => row.map(csvField).join(",")).join("\r\n");
  const url = URL.createObjectURL(new Blob([csv + "\r\n"], { type: "text/csv" }));
  const suffix = view.listMax ? `-within-${view.listMax}mi` : "";
  const a = Object.assign(document.createElement("a"), { href: url, download: `${view.list.trial_id}-members${suffix}.csv` });
  document.body.appendChild(a); a.click(); a.remove();
  URL.revokeObjectURL(url);
});

"use strict";
// Eligibility filter builder (/trials/NCT.../eligibility).
// Step 1: age and sex, step 2: eligibility criteria (AI pharmacist), step 3: review (both as one query). One step is shown at a time; the URL hash picks it.

const parts = location.pathname.split("/");
const trialId = decodeURIComponent(parts[2] || "");
const STEPS = ["age-sex", "criteria", "review"];
const api = (path) => `/api/trials/${encodeURIComponent(trialId)}/filters/${path}`;

mountNav({ active: "lookup", withSearch: true, search: { mode: "nct", value: trialId } });
$("#back").href = `/trials/${encodeURIComponent(trialId)}`;

const data = { demographics: null, criteria: null, review: null };

async function getJson(url) {
  const res = await fetch(url);
  const body = await res.json();
  if (!res.ok) throw Object.assign(new Error(typeof body.detail === "string" ? body.detail : "Something went wrong."), { status: res.status });
  return body;
}

// ---------- steps ----------

function currentStep() {
  const h = location.hash.slice(1);
  return STEPS.includes(h) ? h : "age-sex";
}

function showStep() {
  const step = currentStep();
  const index = STEPS.indexOf(step);
  STEPS.forEach((name, i) => {
    $(`#step-${name}`).hidden = name !== step;
    const li = document.querySelector(`.step[data-step="${name}"]`);
    li.classList.toggle("active", name === step);
    li.classList.toggle("done", i < index);
    const link = li.querySelector(".step-link");
    if (name === step) link.setAttribute("aria-current", "step"); else link.removeAttribute("aria-current");
    li.querySelector(".step-sub").textContent = name === step ? "Viewing" : i < index ? "Done" : "Ready";
  });
  document.querySelector(".stepper").scrollIntoView({ block: "nearest" });
}
window.addEventListener("hashchange", showStep);

// ---------- load ----------

loadWarehouse();

(async function load() {
  // The age/sex step is quick and tells us whether the trial exists, so it gates the page
  try {
    data.demographics = await getJson(api("demographics"));
  } catch (err) {
    const el = $("#status");
    el.hidden = false;
    el.className = "status-msg error";
    el.innerHTML = `<h2>${err.status === 404 ? "Trial not found" : "The filters couldn't be built"}</h2><p>${escapeHtml(err.message)}</p>`;
    return;
  }
  renderHeader(data.demographics);
  renderAgeSex(data.demographics);
  $("#page").hidden = false;
  showStep();
  loadCriteria();
})();

function renderHeader(f) {
  document.title = `Eligibility filters: ${f.trial_id} · Trial Atlas`;
  const status = f.status ? f.status.replace(/_/g, " ").toLowerCase() : "";
  $("#trial-line").innerHTML = `<a href="/trials/${f.trial_id}">${f.trial_id}</a> · ${escapeHtml(f.title || "")}` +
    (status ? ` <span class="pill ${f.status.toLowerCase().replace(/_/g, "-")}">${escapeHtml(status[0].toUpperCase() + status.slice(1))}</span>` : "");
}

// ---------- step 1: age and sex ----------

function niceDate(iso) {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

function sourceValue(v) {
  return v == null ? '<span class="not-set">not set</span>' : `<code>${escapeHtml(JSON.stringify(v))}</code>`;
}

function renderAgeSex(f) {
  $("#warnings").innerHTML = f.warnings.map((w) => `<p class="banner" role="status">${escapeHtml(w)}</p>`).join("");

  const a = f.age;
  $("#age-card").classList.toggle("no-filter", !a.applies);
  $("#age-rule").textContent = a.applies ? a.rule : `${a.rule}: no age filter`;
  $("#age-range").textContent = a.born_on_or_before && a.born_after
    ? `Born after ${niceDate(a.born_after)} and on or before ${niceDate(a.born_on_or_before)}`
    : a.born_on_or_before ? `Born on or before ${niceDate(a.born_on_or_before)}`
    : a.born_after ? `Born after ${niceDate(a.born_after)}` : "Everyone passes the age check";
  $("#age-fields").innerHTML = `
    <div><dt>minimumAge</dt><dd>${sourceValue(a.minimum.raw)}</dd></div>
    <div><dt>maximumAge</dt><dd>${sourceValue(a.maximum.raw)}</dd></div>`;
  $("#age-why").innerHTML = a.reasons.map((r) => `<li>${escapeHtml(r)}</li>`).join("");

  const s = f.sex;
  $("#sex-card").classList.toggle("no-filter", !s.applies);
  $("#sex-rule").textContent = s.applies ? s.rule : `${s.rule}: no sex filter`;
  $("#sex-sub").textContent = s.applies ? `Members recorded as ${s.allowed.map((x) => x.toLowerCase()).join(" or ")}` : "Everyone passes the sex check";
  $("#sex-fields").innerHTML = `
    <div><dt>sex</dt><dd>${sourceValue(s.raw)}</dd></div>
    <div><dt>genderBased</dt><dd>${sourceValue(s.gender_based)}</dd></div>
    ${s.gender_description ? `<div><dt>genderDescription</dt><dd>${sourceValue(s.gender_description)}</dd></div>` : ""}`;
  $("#sex-why").innerHTML = s.reasons.map((r) => `<li>${escapeHtml(r)}</li>`).join("");

  $("#sql").textContent = f.sql;
  $("#json").textContent = JSON.stringify(f.definition, null, 2);
}

$("#copy-sql").addEventListener("click", (e) => data.demographics && copyText(data.demographics.sql, e.currentTarget));


// ---------- step 2: eligibility criteria (AI pharmacist) ----------

const GROUPS = {
  codable: { title: "Checked from claims", text: "Turned into filters. Members must meet every one of these. Rules marked “Approximate timing” use a standard window for an event, so a reviewer confirms the timing. Rules marked “Expert checks timing” filter on the drug alone, and an expert checks the duration, dose or timing." },
  needs_review: { title: "Needs review", text: "Claims can't answer these, so a person checks them. They don't filter anyone out." },
  covered: { title: "Covered by step 1", text: "Already handled by the age and sex step." },
  flagged: { title: "Flagged", text: "Failed a check, so they were left out of the filter. Review them before relying on this step." },
};
const MAX_NAMES = 8;  // drug names listed on a rule before "and N more"
const TOOL_LABELS = {
  search_icd10cm: (a) => `Searched ICD-10-CM for “${a.text}”`,
  icd10cm_code: (a) => `Checked ICD-10-CM code ${a.code}`,
  icd10cm_children: (a) => `Listed the codes under ${a.code}`,
  find_drug: (a) => `Looked up the drug “${a.name}” in RxNorm`,
  drug_class: (a) => `Looked up the drug class “${a.query}”`,
};
const toolLabel = (name, args) => (TOOL_LABELS[name] ? TOOL_LABELS[name](args || {}) : name);

// ---------- the criteria text, shown before, during and after a run ----------

let criteriaText = null; // the trial's parsed eligibility: { inclusion, exclusion, parsed, raw_text }

const norm = (t) => String(t || "").toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();

/** The lines of the criteria text, each with its section ("inclusion", "exclusion" or null) and nesting level. */
function criteriaLines() {
  const e = criteriaText;
  if (!e || !e.raw_text) return [];
  if (e.parsed) {
    return [...e.inclusion.map((c) => ({ ...c, section: "inclusion" })), ...e.exclusion.map((c) => ({ ...c, section: "exclusion" }))];
  }
  return e.raw_text.split(/\n+/).map((t) => t.trim()).filter(Boolean).map((text) => ({ text, level: 0, section: null }));
}

/** Which line each rule's quote came from: the line containing the quote, the line inside a multi-line
 *  quote, or failing that the line sharing most of the quote's words. */
function rulesByLine(lines, rules) {
  const normLines = lines.map((l) => norm(l.text));
  const byLine = lines.map(() => []);
  for (const r of rules) {
    const q = norm(r.source_text);
    if (!q) continue;
    // A line inside the quote counts only if it's long enough not to match by accident ("Pregnancy")
    let hits = normLines.map((l, i) => (l && (l.includes(q) || (l.split(" ").length >= 4 && q.includes(l))) ? i : -1)).filter((i) => i >= 0);
    if (!hits.length) {
      const words = q.split(" ");
      const scores = normLines.map((l) => { const set = new Set(l.split(" ")); return words.filter((w) => set.has(w)).length / words.length; });
      const best = Math.max(...scores);
      if (best >= 0.6) hits = [scores.indexOf(best)];
    }
    hits.forEach((i) => byLine[i].push(r));
  }
  return byLine;
}

function renderCriteriaText(result = null) {
  const panel = $("#cr-text");
  const lines = criteriaLines();
  if (!lines.length) { panel.hidden = true; return; }
  panel.hidden = false;
  const rules = result ? result.criteria : [];
  const byLine = rulesByLine(lines, rules);
  $("#cr-text-note").textContent = result
    ? "Each line shows the rules taken from it. Click a rule to see its card; lines without a tag produced no rule."
    : "As published on ClinicalTrials.gov. This is the only text rules can come from.";
  const label = (g) => GROUPS[g].title;
  const item = (l, i) => {
    const tags = byLine[i].map((r) => `<a class="line-rule group-${r.group}" href="#rule-${r.id}" title="${escapeHtml(label(r.group))}">${r.id} ${escapeHtml(label(r.group).toLowerCase())}</a>`).join("");
    const none = result && !byLine[i].length && l.level === 0 ? '<span class="line-none">no rule</span>' : "";
    return `<li class="lvl-${Math.min(l.level, 2)}">${escapeHtml(l.text)}${tags || none ? `<span class="line-rules">${tags}${none}</span>` : ""}</li>`;
  };
  const indexed = lines.map((l, i) => ({ l, i }));
  const list = (section) => `<ul class="criteria">${indexed.filter((x) => x.l.section === section).map((x) => item(x.l, x.i)).join("")}</ul>`;
  const mapped = new Set(byLine.flat().map((r) => r.id));
  const unplaced = rules.filter((r) => !mapped.has(r.id));
  $("#cr-text-body").innerHTML = (criteriaText.parsed
    ? `<div class="elig-cols">
        <div class="elig-col include"><h4>Inclusion</h4>${list("inclusion")}</div>
        <div class="elig-col exclude"><h4>Exclusion</h4>${list("exclusion")}</div>
      </div>`
    : `<ul class="criteria raw-lines">${indexed.map((x) => item(x.l, x.i)).join("")}</ul>`)
    + (unplaced.length ? `<p class="small-note muted">Couldn't place on a line: ${unplaced.map((r) => `<a href="#rule-${r.id}">${r.id}</a>`).join(", ")}.</p>` : "");
}

async function loadCriteriaText() {
  try {
    const d = await getJson(`/api/trials/${encodeURIComponent(trialId)}`);
    criteriaText = d.eligibility;
  } catch { criteriaText = null; }
  renderCriteriaText(data.criteria);
}

// Links inside step 2 jump within the page; letting them change the hash would switch steps
$("#step-criteria").addEventListener("click", (e) => {
  const a = e.target.closest('a[href^="#"]');
  if (!a || STEPS.includes(a.getAttribute("href").slice(1))) return;
  const target = document.getElementById(a.getAttribute("href").slice(1));
  if (!target) return;
  e.preventDefault();
  if (target.tagName === "DETAILS") target.open = true;
  target.closest("details")?.setAttribute("open", "");
  target.scrollIntoView({ behavior: reducedMotion() ? "auto" : "smooth", block: "center" });
  target.classList.remove("flash"); void target.offsetWidth; target.classList.add("flash");
});

async function loadCriteria() {
  loadCriteriaText();
  try {
    const res = await fetch(api("criteria"));
    if (res.ok) { renderCriteria(await res.json()); return; }
  } catch { /* fall through to the intro */ }
  $("#cr-intro").hidden = false;
  loadReview();  // step 2 not built: the review shows age and sex only
}

$("#cr-run").addEventListener("click", runCriteria);
$("#cr-rerun").addEventListener("click", runCriteria);
$("#cr-copy-sql").addEventListener("click", (e) => data.criteria && copyText(data.criteria.sql, e.currentTarget));

async function runCriteria() {
  $("#cr-intro").hidden = true;
  $("#cr-body").hidden = true;
  $("#cr-error").hidden = true;
  $("#cr-live").hidden = false;
  $("#cr-spinner").hidden = false;
  $("#cr-activity").innerHTML = "";
  setStatus("Starting…");
  const calls = new Map();
  try {
    const res = await fetch(api("criteria/run"), { method: "POST" });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(typeof body.detail === "string" ? body.detail : `The run didn't start (HTTP ${res.status}).`);
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let cut;
      while ((cut = buffer.indexOf("\n\n")) >= 0) {
        const chunk = buffer.slice(0, cut);
        buffer = buffer.slice(cut + 2);
        const line = chunk.split("\n").find((l) => l.startsWith("data: "));
        if (line) handleEvent(JSON.parse(line.slice(6)), calls);
      }
    }
  } catch (err) {
    showCriteriaError(err.message);
  }
}

function setStatus(text) { $("#cr-status").textContent = text; }

function addActivity(html, cls = "") {
  const li = document.createElement("li");
  li.className = cls;
  li.innerHTML = html;
  $("#cr-activity").appendChild(li);
  li.scrollIntoView({ block: "nearest" });
  return li;
}

function handleEvent(e, calls) {
  if (e.type === "status") { setStatus(e.message); addActivity(escapeHtml(e.message), "act-status"); }
  else if (e.type === "note") addActivity(escapeHtml(e.text), "act-note");
  else if (e.type === "tool_call") {
    setStatus(`${toolLabel(e.name, e.args)}…`);
    calls.set(e.id, addActivity(`<span class="act-call">${escapeHtml(toolLabel(e.name, e.args))}</span><span class="act-result muted">Waiting…</span>`, "act-tool"));
  } else if (e.type === "tool_result") {
    const li = calls.get(e.id);
    const target = li ? li.querySelector(".act-result") : addActivity("", "act-tool").appendChild(document.createElement("span"));
    target.textContent = summarizeResult(e.name, e.result, e.is_error);
    target.classList.toggle("act-error", e.is_error);
  } else if (e.type === "submitting") { setStatus(`Submitted ${e.count} rules`); addActivity(`Submitted ${e.count} rules for checking.`, "act-status"); }
  else if (e.type === "error") showCriteriaError(e.message);
  else if (e.type === "done") renderCriteria(e.result);
}

function summarizeResult(name, raw, isError) {
  if (isError) return "The lookup failed; the pharmacist was told and continued.";
  try {
    const v = JSON.parse(raw);
    if (!Array.isArray(v)) return v.result || (v.code ? `${v.code}: ${v.name}` : "Done");
    if (!v.length) return "Nothing found";
    if (name === "drug_class") return v.map((c) => `${c.atc} ${c.name} (${c.members.length} drugs)`).join("; ");
    if (name === "find_drug") return v.map((d) => `${d.name} → ${d.ingredients.map((i) => i.name).join(", ")}`).join("; ");
    return v.slice(0, 4).map((c) => `${c.code} ${c.name}`).join("; ") + (v.length > 4 ? `; and ${v.length - 4} more` : "");
  } catch { return raw.length > 160 ? `${raw.slice(0, 160)}…` : raw; }
}

function showCriteriaError(message) {
  $("#cr-spinner").hidden = true;
  setStatus("Stopped");
  const el = $("#cr-error");
  el.hidden = false;
  el.innerHTML = `<h2>Step 2 didn't finish</h2><p>${escapeHtml(message)}</p><p><button type="button" class="ghost" id="cr-retry">Try again</button></p>`;
  $("#cr-retry").addEventListener("click", runCriteria);
}

function renderCriteria(r) {
  data.criteria = r;
  renderCriteriaText(r);
  loadReview();  // the review query now includes step 2's rules
  $("#cr-live").hidden = true;
  $("#cr-intro").hidden = true;
  $("#cr-error").hidden = true;
  $("#cr-body").hidden = false;

  const counts = Object.fromEntries(Object.keys(GROUPS).map((g) => [g, r.criteria.filter((c) => c.group === g).length]));
  $("#cr-groups").innerHTML = Object.entries(GROUPS).map(([g, info]) =>
    `<a class="group-chip group-${g}" href="#cr-group-${g}">${counts[g]} ${escapeHtml(info.title.toLowerCase())}</a>`).join("");
  $("#cr-summary").textContent = r.agent_summary || "";
  const u = r.usage || {};
  $("#cr-meta").textContent = `Built by ${r.model} with ${r.tool_calls.length} lookups and ${u.llm_calls || 0} model calls `
    + `(${fmt.format(u.input_tokens || 0)} input and ${fmt.format(u.output_tokens || 0)} output tokens). Time windows measured from ${niceDate(r.reference_date)}.`;

  const logIndex = new Map(r.tool_calls.map((t, i) => [t.id, i + 1]));
  $("#cr-sections").innerHTML = Object.entries(GROUPS).filter(([g]) => counts[g]).map(([g, info]) => `
    <section class="cr-group" id="cr-group-${g}" aria-labelledby="cr-group-${g}-title">
      <div class="trace-head">
        <h3 id="cr-group-${g}-title">${info.title} <span class="count">${counts[g]}</span></h3>
        <p class="muted">${info.text}</p>
      </div>
      <div class="traces">${r.criteria.filter((c) => c.group === g).map((c) => criterionCard(c, logIndex)).join("")}</div>
    </section>`).join("");

  $("#cr-log").innerHTML = r.tool_calls.map((t, i) => `
    <li id="tc-${escapeHtml(t.id)}" class="${t.is_error ? "act-error" : ""}">
      <b>${i + 1}. ${escapeHtml(toolLabel(t.name, t.args))}</b>
      <span class="muted">${escapeHtml(summarizeResult(t.name, t.result, t.is_error))}</span>
      <details><summary>Raw result</summary><pre class="code-block">${escapeHtml(t.result)}</pre></details>
    </li>`).join("") || '<li class="muted">No lookups were made.</li>';
  $("#cr-notes").innerHTML = r.notes.map((n) => `<li>${escapeHtml(n)}</li>`).join("") || '<li class="muted">No notes.</li>';
  $("#cr-sql").textContent = r.sql;
  $("#cr-json").textContent = JSON.stringify(r.definition, null, 2);
}

function criterionCard(c, logIndex) {
  const req = c.requirement === "must_have" ? "Must have" : "Must not have";
  const codes = c.codes.length ? `<div><span class="node-label">ICD-10-CM codes (each covers its more specific codes)</span><ul class="code-list">${
    c.codes.map((x) => `<li class="${x.valid ? "" : "invalid"}"><b>${escapeHtml(x.code)}</b> ${escapeHtml(x.name || "not a real code")}</li>`).join("")}</ul></div>` : "";
  const drugs = c.drugs.length ? `<div><span class="node-label">Drugs</span><ul class="code-list">${c.drugs.map((d) => `
    <li class="${d.valid ? "" : "invalid"}"><b>${escapeHtml(d.atc_class ? `${d.atc_class} ${d.atc_name || d.name}` : d.name)}</b>
      ${d.valid ? `${escapeHtml(d.ingredients.join(", "))}${d.rxcui ? ` (RxCUI ${escapeHtml(d.rxcui)})` : ""}. ${fmt.format(d.ndc_count)} NDCs${d.ndcs.length ? `, e.g. ${escapeHtml(d.ndcs.slice(0, 3).join(", "))}` : ""}.${d.names.length ? ` Drug names matched: ${escapeHtml(d.names.slice(0, MAX_NAMES).join(", "))}${d.names.length > MAX_NAMES ? ` and ${d.names.length - MAX_NAMES} more` : ""}.` : ""}` : "couldn't be confirmed in RxNorm"}</li>`).join("")}</ul></div>` : "";
  const backing = c.tool_call_ids.length ? `<p class="small-note muted">Backed by lookups ${c.tool_call_ids.map((id) =>
    `<a href="#tc-${escapeHtml(id)}" class="tc-link">#${logIndex.get(id) || "?"}</a>`).join(", ")}</p>` : "";
  return `
    <article class="trace criterion crit-${c.group}" id="rule-${c.id}">
      <header class="trace-input">
        <b>${c.id}</b>
        <span class="req req-${c.requirement}">${req}</span>
        <span class="tag">${escapeHtml(c.section === "inclusion" ? "Inclusion" : "Exclusion")}</span>
        <span class="tag tag-type">${escapeHtml(c.type.replace("_", " "))}</span>
        ${c.approximate ? '<span class="tag tag-approx" title="The window stands in for an event; a reviewer confirms the timing">Approximate timing</span>' : ""}
        ${c.partial ? '<span class="tag tag-approx" title="Filtered on the drug alone; an expert checks the duration, dose or timing">Expert checks timing</span>' : ""}
      </header>
      <blockquote class="crit-quote">${escapeHtml(c.source_text)}</blockquote>
      ${c.quote_found ? "" : '<p class="crit-warn">This quote wasn\'t found in the trial\'s text.</p>'}
      ${codes}${drugs}
      <p><span class="node-label">Time window</span>${escapeHtml(c.window_text)}</p>
      ${c.reason_not_codable ? `<p><span class="node-label">${c.partial ? "What the expert checks" : "Why it needs review"}</span>${escapeHtml(c.reason_not_codable)}</p>` : ""}
      ${c.covered_by_step ? `<p><span class="node-label">Covered by</span>Step 1, age and sex</p>` : ""}
      <p><span class="node-label">Why</span>${escapeHtml(c.rationale)}</p>
      ${c.problems.length ? `<ul class="crit-problems">${c.problems.map((p) => `<li>${escapeHtml(p)}</li>`).join("")}</ul>` : ""}
      ${backing}
      ${c.sql ? `<details><summary>SQL for this rule</summary><pre class="code-block">${escapeHtml(c.sql)}</pre></details>` : ""}
    </article>`;
}


// ---------- step 3: review (both steps as one query) ----------

const REVIEW_GROUPS = [
  { key: "expert_checks", title: "Expert checks", chip: "group-expert",
    text: "In the query, but an expert confirms the members these conditions return. Marked EXPERT CHECK in the SQL." },
  { key: "not_applied", title: "Not in the query", chip: "group-needs_review",
    text: "Claims can't answer these, so a person checks them. They remove no one. Listed at the end of the SQL." },
  { key: "flagged", title: "Flagged", chip: "group-flagged",
    text: "Failed a check, so they were left out of the query. Review them before relying on it." },
  { key: "covered", title: "Covered by step 1", chip: "",
    text: "Restate the age or sex limits, which step 1 already applies." },
  { key: "applied", title: "In the query", chip: "group-codable",
    text: "Every condition in the SQL, in order. Each one has a comment quoting where it came from." },
];

async function loadReview() {
  try {
    data.review = await getJson(api("review"));
    renderReview(data.review);
  } catch (err) {
    const el = $("#rv-error");
    el.hidden = false;
    el.innerHTML = `<h2>The review couldn't be built</h2><p>${escapeHtml(err.message)}</p>`;
  }
}

function reviewLabel(i) {
  if (!i.section) return "Step 1 · ClinicalTrials.gov";
  const section = i.section[0].toUpperCase() + i.section.slice(1);
  return `${section} · ${i.requirement === "must_have" ? "Must have" : "Must not have"}`;
}

function reviewItem(i) {
  return `
    <article class="trace rv-item">
      <header class="trace-input"><b>${escapeHtml(i.id)}</b><span class="tag">${escapeHtml(reviewLabel(i))}</span></header>
      <p class="rv-quote">${i.section ? `“${escapeHtml(i.source_text)}”` : escapeHtml(i.source_text)}</p>
      <p class="muted">${escapeHtml(i.note)}</p>
    </article>`;
}

function renderReview(r) {
  $("#rv-error").hidden = true;
  $("#rv-body").hidden = false;
  // When step 2 isn't built, its warning comes first and links there
  $("#rv-warnings").innerHTML = r.warnings.map((w, i) => `<p class="banner" role="status">${escapeHtml(w)}${
    !r.criteria_built && i === 0 ? ' <a href="#criteria">Go to step 2</a>' : ""}</p>`).join("");
  const n = (k) => r[k].length;
  const plural = (count, word) => `${count} ${word}${count === 1 ? "" : "s"}`;
  $("#rv-counts").innerHTML = [
    `<a class="group-chip group-codable" href="#rv-group-applied">${plural(n("applied"), "condition")} in the query</a>`,
    n("expert_checks") ? `<a class="group-chip group-expert" href="#rv-group-expert_checks">${plural(n("expert_checks"), "expert check")}</a>` : "",
    n("not_applied") ? `<a class="group-chip group-needs_review" href="#rv-group-not_applied">${n("not_applied")} not in the query</a>` : "",
    n("flagged") ? `<a class="group-chip group-flagged" href="#rv-group-flagged">${n("flagged")} flagged</a>` : "",
  ].join("");
  $("#rv-meta").textContent = r.criteria_built
    ? `Step 2 built by ${r.model}. Ages and time windows measured from ${niceDate(r.reference_date)}.`
    : `Ages measured from ${niceDate(r.reference_date)}. Step 2 hasn't been built, so only age and sex are applied.`;
  $("#rv-sections").innerHTML = REVIEW_GROUPS.filter((g) => n(g.key)).map((g) => `
    <section class="cr-group" id="rv-group-${g.key}" aria-labelledby="rv-group-${g.key}-title">
      <div class="trace-head">
        <h3 id="rv-group-${g.key}-title">${g.title} <span class="count">${n(g.key)}</span></h3>
        <p class="muted">${g.text}</p>
      </div>
      <div class="traces">${r[g.key].map(reviewItem).join("")}</div>
    </section>`).join("");
  $("#rv-sql").textContent = r.sql;
  // The query changed (e.g. step 2 just finished), so earlier results no longer match it
  for (const id of ["#rv-count-result", "#rv-list-result", "#rv-ran", "#rv-run-error"]) $(id).hidden = true;
}

// Links inside step 3 jump within the page instead of switching steps
$("#step-review").addEventListener("click", (e) => {
  const a = e.target.closest('a[href^="#rv-"]');
  if (!a) return;
  e.preventDefault();
  document.getElementById(a.getAttribute("href").slice(1))?.scrollIntoView({ behavior: reducedMotion() ? "auto" : "smooth" });
});

$("#rv-copy-sql").addEventListener("click", (e) => data.review && copyText(data.review.sql, e.currentTarget));
$("#rv-download").addEventListener("click", () => {
  if (!data.review) return;
  const url = URL.createObjectURL(new Blob([data.review.sql + "\n"], { type: "application/sql" }));
  const a = Object.assign(document.createElement("a"), { href: url, download: `${data.review.trial_id}-eligibility.sql` });
  document.body.appendChild(a); a.click(); a.remove();
  URL.revokeObjectURL(url);
});


// ---------- step 3: running the query on Databricks ----------

const LIST_LABELS = { member_id: "Member ID", first_nme: "First name", last_nme: "Last name", birth_dte: "Birth date",
  gender_cde: "Sex", state_or_province_cde: "State", zip_5_postal_cde: "ZIP" };
let memberList = null;

async function loadWarehouse() {
  let w;
  try { w = await getJson("/api/warehouse"); } catch { return; }
  if (w.configured) {
    $("#rv-run-note").textContent = `Runs the query above on warehouse ${w.warehouse_id} at ${w.host.replace(/^https?:\/\//, "")}`
      + `${w.catalog ? ` (catalog ${w.catalog}${w.schema_name ? `, schema ${w.schema_name}` : ""})` : ""}. Each run uses warehouse compute. `
      + "Results are shown on this page only: Trial Atlas doesn't save, cache or log them.";
    $("#rv-run-ready").hidden = false;
    return;
  }
  $("#rv-run-note").textContent = "Count the matching members, or list them, straight from your Databricks SQL warehouse.";
  const setup = $("#rv-run-setup");
  setup.hidden = false;
  setup.innerHTML = `<p>Not set up yet. Add these to the <code>.env</code> file and restart the app:</p>
    <pre class="code-block">DATABRICKS_HOST=https://adb-1234567890.12.azuredatabricks.net
DATABRICKS_TOKEN=dapi...
DATABRICKS_HTTP_PATH=/sql/1.0/warehouses/abc123   # or DATABRICKS_WAREHOUSE_ID=abc123
# Optional: DATABRICKS_CATALOG, DATABRICKS_SCHEMA, MEMBERS_TABLE, MX_CLAIMS_TABLE, RX_CLAIMS_TABLE</pre>
    <p class="muted small-note">Still missing: ${w.missing.map(escapeHtml).join(", ")}. The host and HTTP path are on the warehouse's Connection details tab.</p>`;
}

async function runOnWarehouse(path, busyText) {
  const buttons = [$("#rv-count"), $("#rv-list")];
  buttons.forEach((b) => { b.disabled = true; });
  $("#rv-run-error").hidden = true;
  const status = $("#rv-run-status");
  status.hidden = false;
  status.innerHTML = `<div class="spinner" role="presentation"></div><p>${escapeHtml(busyText)}</p>`;
  try {
    const res = await fetch(api(path), { method: "POST" });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(typeof body.detail === "string" ? body.detail : `The query didn't run (HTTP ${res.status}).`);
    $("#rv-ran").hidden = false;
    $("#rv-ran-sql").textContent = body.statement;
    return body;
  } catch (err) {
    const el = $("#rv-run-error");
    el.hidden = false;
    el.innerHTML = `<h2>The query didn't run</h2><p>${escapeHtml(err.message)}</p>`;
    return null;
  } finally {
    status.hidden = true;
    buttons.forEach((b) => { b.disabled = false; });
  }
}

const seconds = (ms) => `${(ms / 1000).toFixed(1)} s`;

$("#rv-count").addEventListener("click", async () => {
  const r = await runOnWarehouse("review/count", "Counting members on Databricks… A stopped warehouse can take a minute to start.");
  if (!r) return;
  const el = $("#rv-count-result");
  el.hidden = false;
  const expert = data.review ? data.review.expert_checks.length : 0;
  el.innerHTML = `<p class="kpi-value">${fmt.format(r.count)}</p>
    <p class="kpi-label">member${r.count === 1 ? "" : "s"} match the query</p>
    <p class="kpi-sub">Ran in ${seconds(r.elapsed_ms)}.${r.criteria_built ? "" : " Step 2 isn't built, so this counts age and sex only."}${
      expert ? ` ${expert} condition${expert === 1 ? " needs" : "s need"} an expert check before these members are confirmed.` : ""}</p>`;
});

$("#rv-list").addEventListener("click", async () => {
  const r = await runOnWarehouse("review/members", "Getting the member list from Databricks…");
  if (!r) return;
  memberList = r;
  $("#rv-list-result").hidden = false;
  $("#rv-list-note").textContent = r.truncated
    ? `Showing the first ${fmt.format(r.limit)} members by member ID. More match: use Count members for the total, or run the query in Databricks.`
    : `${fmt.format(r.rows.length)} member${r.rows.length === 1 ? "" : "s"}, ordered by member ID. Ran in ${seconds(r.elapsed_ms)}.`;
  $("#rv-list-head").innerHTML = `<tr>${r.columns.map((c) => `<th>${escapeHtml(LIST_LABELS[c] || c)}</th>`).join("")}</tr>`;
  $("#rv-list-body").innerHTML = r.rows.length
    ? r.rows.map((row) => `<tr>${row.map((v) => `<td>${escapeHtml(v ?? "")}</td>`).join("")}</tr>`).join("")
    : `<tr class="table-empty"><td colspan="${r.columns.length}">No members match.</td></tr>`;
});

function csvField(v) {
  const s = v == null ? "" : String(v);
  return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

$("#rv-list-csv").addEventListener("click", () => {
  if (!memberList) return;
  const csv = [memberList.columns, ...memberList.rows].map((row) => row.map(csvField).join(",")).join("\r\n");
  const url = URL.createObjectURL(new Blob([csv + "\r\n"], { type: "text/csv" }));
  const a = Object.assign(document.createElement("a"), { href: url, download: `${memberList.trial_id}-members.csv` });
  document.body.appendChild(a); a.click(); a.remove();
  URL.revokeObjectURL(url);
});

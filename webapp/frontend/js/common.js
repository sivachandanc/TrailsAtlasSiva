"use strict";
// Shared by every page: nav bar, the two-mode search box, formatting, tile map and chart helpers.

const $ = (sel, root = document) => root.querySelector(sel);
const fmt = new Intl.NumberFormat("en-US");
const NCT_RE = /^NCT\d{8}$/;
const APP_NAME = "Trails-POC";

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

const CODE_BY_NAME = new Map(window.US_TILES.map(([code, name]) => [name, code]));
function stateCode(name) { return CODE_BY_NAME.get(name) || name || ""; }

function css(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }

function reducedMotion() { return matchMedia("(prefers-reduced-motion: reduce)").matches; }

/** "nct05909046", " 05909046 " -> "NCT05909046". Returns the input upper-cased if it can't be fixed. */
function normalizeNct(value) {
  const v = value.trim().toUpperCase().replace(/\s+/g, "");
  return /^\d{8}$/.test(v) ? `NCT${v}` : v;
}
function looksLikeNct(value) { return /^\s*(nct)?\s*\d{8}\s*$/i.test(value); }

async function copyText(text, button) {
  const original = button.textContent;
  try {
    await navigator.clipboard.writeText(text);
    button.textContent = "Copied";
  } catch {
    button.textContent = "Press ⌘C / Ctrl+C";
    const range = document.createRange();
    range.selectNodeContents(button.previousElementSibling || button);
    getSelection().removeAllRanges();
    getSelection().addRange(range);
  }
  setTimeout(() => { button.textContent = original; }, 1600);
}

// ---------- nav ----------

function mountNav({ active = "", withSearch = false, search = {} } = {}) {
  const el = document.getElementById("nav");
  el.innerHTML = `
    <div class="nav-inner">
      <a class="brand" href="/" aria-label="${APP_NAME} home">
        <span class="brand-mark" aria-hidden="true"><i></i><i></i><i></i><i></i></span>${APP_NAME}
      </a>
      ${withSearch ? '<div class="nav-search" id="nav-search"></div>' : ""}
      <button class="nav-menu" type="button" aria-expanded="false" aria-controls="nav-links">Menu</button>
      <ul class="nav-links" id="nav-links">
        <li><a href="/search" ${active === "search" ? 'aria-current="page"' : ""}>Search trials</a></li>
        <li><a href="/?mode=nct" ${active === "lookup" ? 'aria-current="page"' : ""}>Look up a trial</a></li>
        <li><a href="/data-schema" ${active === "schema" ? 'aria-current="page"' : ""}>Member data</a></li>
      </ul>
    </div>`;
  const menu = $(".nav-menu", el);
  menu.addEventListener("click", () => {
    const open = menu.getAttribute("aria-expanded") === "true";
    menu.setAttribute("aria-expanded", String(!open));
    el.classList.toggle("menu-open", !open);
  });
  if (withSearch) mountSearchBox($("#nav-search", el), { compact: true, ...search });
}

// ---------- search box (condition or NCT ID) ----------

const MODES = {
  condition: { label: "Condition", placeholder: "e.g. asthma", button: "Search trials" },
  nct: { label: "Trial ID", placeholder: "e.g. NCT05909046", button: "Open trial" },
};

/** Renders a search box into `container`. Submitting navigates to /search or /trials/{id}. */
function mountSearchBox(container, { compact = false, mode = "condition", value = "" } = {}) {
  const id = `sb-${Math.random().toString(36).slice(2, 8)}`;
  container.innerHTML = `
    <form class="searchbox ${compact ? "searchbox-compact" : ""}" role="search" novalidate>
      <div class="mode" role="radiogroup" aria-label="Search by">
        <button type="button" role="radio" data-mode="condition">Condition</button>
        <button type="button" role="radio" data-mode="nct">Trial ID (NCT)</button>
      </div>
      <div class="sb-row">
        <div class="field sb-source">
          <label for="${id}-source">Source</label>
          <select id="${id}-source" name="source"><option value="clinicaltrials_gov">ClinicalTrials.gov</option></select>
        </div>
        <div class="field sb-country">
          <label for="${id}-country">Country</label>
          <select id="${id}-country" name="country"><option>United States</option></select>
        </div>
        <div class="field field-grow">
          <label for="${id}-q" class="q-label">Condition</label>
          <input id="${id}-q" name="q" type="search" autocomplete="off" spellcheck="false" aria-describedby="${id}-hint">
        </div>
        <button type="submit" class="primary sb-submit">Search trials</button>
      </div>
      <p class="sb-hint" id="${id}-hint" aria-live="polite"></p>
    </form>`;

  const form = $("form", container);
  const input = $("input[name=q]", form);
  const hint = $(".sb-hint", form);
  let current = mode;

  function setMode(next, { announce = "" } = {}) {
    current = next;
    form.querySelectorAll(".mode button").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.mode === next)));
    $(".q-label", form).textContent = MODES[next].label;
    input.placeholder = MODES[next].placeholder;
    $(".sb-submit", form).textContent = MODES[next].button;
    hint.textContent = announce;
    hint.classList.remove("error");
  }

  form.querySelectorAll(".mode button").forEach((b) => b.addEventListener("click", () => { setMode(b.dataset.mode); input.focus(); }));
  input.addEventListener("input", () => {
    if (current === "condition" && looksLikeNct(input.value)) {
      setMode("nct", { announce: "That looks like a trial ID, so this will open the trial." });
    } else if (hint.classList.contains("error")) {
      hint.textContent = ""; hint.classList.remove("error");
    }
  });
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const raw = input.value.trim();
    if (current === "nct") {
      const nct = normalizeNct(raw);
      if (!NCT_RE.test(nct)) {
        hint.textContent = "Trial IDs look like NCT05909046: NCT followed by 8 digits.";
        hint.classList.add("error");
        input.focus();
        return;
      }
      location.href = `/trials/${nct}`;
    } else {
      if (raw.length < 2) {
        hint.textContent = "Type at least 2 characters, for example “asthma”.";
        hint.classList.add("error");
        input.focus();
        return;
      }
      location.href = `/search?condition=${encodeURIComponent(raw)}`;
    }
  });

  input.value = value;
  setMode(mode);
  return { form, input, setMode };
}

// "/" focuses the first search box on the page, like many search UIs
document.addEventListener("keydown", (e) => {
  if (e.key !== "/" || e.metaKey || e.ctrlKey || e.altKey) return;
  const t = e.target;
  if (t.closest && t.closest("input, textarea, select, [contenteditable]")) return;
  const input = $(".searchbox input[name=q]");
  if (input) { e.preventDefault(); input.focus(); input.select(); }
});

// ---------- tile map ----------

/**
 * items: Map(stateName -> {value, title}). Colours on a square-root scale in six steps.
 * onSelect(name|null) is called when a tile is clicked; returns {select(name)}.
 */
function renderTileMap(el, legendEl, items, { unit = "trials", onSelect = null } = {}) {
  const values = [...items.values()].map((i) => i.value);
  const max = Math.max(1, ...values);
  const bucket = (n) => (n ? Math.min(6, 1 + Math.floor(Math.sqrt(n / max) * 5.999)) : 0);
  let selected = null;

  el.innerHTML = "";
  for (const [code, name, col, row] of window.US_TILES) {
    const item = items.get(name);
    const n = item ? item.value : 0;
    const b = document.createElement("button");
    b.type = "button";
    b.className = `tile${n ? ` s${bucket(n)}` : ""}`;
    b.style.gridColumn = col + 1;
    b.style.gridRow = row + 1;
    b.dataset.state = name;
    b.disabled = !n || !onSelect;
    b.title = item ? item.title : `${name}: none`;
    b.setAttribute("aria-label", b.title);
    if (onSelect) b.setAttribute("aria-pressed", "false");
    b.innerHTML = `<span class="tile-code">${code}</span><span class="n">${n ? fmt.format(n) : ""}</span>`;
    if (onSelect && n) b.addEventListener("click", () => select(selected === name ? null : name, true));
    el.appendChild(b);
  }

  function select(name, notify = false) {
    selected = name;
    el.querySelectorAll(".tile").forEach((t) => {
      const on = t.dataset.state === name;
      t.classList.toggle("selected", on);
      if (onSelect) t.setAttribute("aria-pressed", String(on));
    });
    if (notify && onSelect) onSelect(name);
  }

  if (legendEl) {
    // Each colour step with the smallest count that gets it; steps no count can reach are skipped
    const seen = new Set();
    const steps = [];
    for (let n = 1; n <= max && steps.length < 6; n = Math.max(n + 1, Math.ceil(max * Math.pow(steps.length / 6, 2)))) {
      const b = bucket(n);
      if (!seen.has(b)) { seen.add(b); steps.push(`<span><span class="swatch tile s${b}"></span>${fmt.format(n)}+</span>`); }
    }
    legendEl.innerHTML = `<span>${unit[0].toUpperCase() + unit.slice(1)}:</span>${steps.join("")}`;
  }
  return { select };
}

// ---------- charts (ECharts) ----------

const charts = {};

function chart(id) {
  if (!charts[id]) charts[id] = echarts.init(document.getElementById(id), null, { renderer: "svg" });
  return charts[id];
}

function baseOption() {
  return {
    textStyle: { fontFamily: css("--font"), color: css("--ink") },
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" }, backgroundColor: css("--surface"),
               borderColor: css("--line"), textStyle: { color: css("--ink") } },
    animationDuration: reducedMotion() ? 0 : 500,
  };
}

/** Horizontal bars, first item on top, values printed on the bars. */
function hbar(id, items, { color = css("--accent"), labelWidth = 150 } = {}) {
  const data = [...items].reverse();
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

window.addEventListener("resize", () => Object.values(charts).forEach((c) => c.resize()));

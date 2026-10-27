# Frontend

Plain HTML, CSS and JavaScript in `webapp/frontend/`, served by FastAPI. No build step, no
framework.
- **Charts:** Apache ECharts 5.5.1 from jsDelivr, on the search, trial and member dashboard pages.
- **Map:** Leaflet 1.9.4 from cdnjs, with Esri grey canvas tiles (no key), on the member dashboard.
- **Font:** Schibsted Grotesk, from Google Fonts.

Each page loads `us_tiles.js` (the state tile-map layout), `js/common.js`, then its own script.

## Shared code (`js/common.js`)

| Function | Purpose |
|---|---|
| `$`, `fmt` | `querySelector` shorthand; `Intl.NumberFormat("en-US")` |
| `escapeHtml(s)` | used for every value put into HTML |
| `normalizeNct`, `looksLikeNct` | same NCT ID rules as the server |
| `copyText(text, button)` | clipboard copy; falls back to selecting the text |
| `mountNav(...)` | the top bar: brand, optional compact search, links (Search trials, Look up a trial, Member data), mobile menu |
| `mountSearchBox(...)` | two-mode box: condition → `/search?condition=…`, trial ID → `/trials/NCT…` |
| `renderTileMap(...)` | US tile map, shaded by count; click a state to select it |
| `chart`, `baseOption`, `hbar` | ECharts helpers (SVG renderer, theme colors from CSS tokens) |
| `stateCode`, `css`, `reducedMotion` | small helpers |

## Pages

| Page | Script | What it does |
|---|---|---|
| `/` `index.html` | `home.js` | Hero search box (`?mode=nct` starts in trial-ID mode), decorative tile map, live open-trial count |
| `/search` `search.html` | `search.js` | `GET /api/search`. Loading skeleton, KPIs, state tile map (filters the table), charts, sortable table with a text filter, rendered 100 rows per scroll (IntersectionObserver). Empty and error states |
| `/trials/{id}` `trial.html` | `trial.js` | `GET /api/trials/{id}`. Header, banners, KPIs, summary, timeline, map, site status, parsed eligibility, arms, outcomes, conditions, contacts, sites table, link to the filter builder |
| `/trials/{id}/eligibility` `eligibility.html` | `eligibility.js` | The 3-step filter builder (below) |
| `/trials/{id}/members` `members.html` | `members.js` | The member dashboard: `POST /review/dashboard` on load, map, charts, site table, member list on demand. See [member-dashboard.md](member-dashboard.md) |
| `/data-schema` `data-schema.html` | `data-schema.js` | `GET /api/member-schema`. One card per table (columns, types, key, used for) plus DDL with Copy |

## The filter builder (`eligibility.js`)

**Steps and URL.** `STEPS = ["age-sex", "criteria", "review"]`. The URL hash picks the step
(`#age-sex`, `#criteria`, `#review`); anything else opens step 1. `showStep()` shows one panel,
and marks the stepper Done / Viewing / Ready.

**Loading order:**
1. `loadWarehouse()`: `GET /api/warehouse` sets up the Databricks panel.
2. `GET /filters/demographics` gates the page: an error here shows "Trial not found" or the
   message. Then `renderHeader`, `renderAgeSex`, `showStep`.
3. `loadCriteria()`:
   - Loads the trial's criteria text.
   - Tries `GET /filters/criteria`. A cached result → `renderCriteria`; otherwise the intro with
     the **Build with AI pharmacist** button.
   - Then `loadReview()`.

**Step 1 (`renderAgeSex`).** Age and sex cards: the rule, the birth-date range, the source fields
(`minimumAge`, `maximumAge`, `sex`, `genderBased`, `genderDescription`), the reasons, warnings,
the SQL with Copy, and the JSON definition.

**Step 2:**
- **Intro:** what the agent does, what it sees, the cost.
- **Running** (`runCriteria`): `POST /filters/criteria/run`, reading the SSE stream with
  `fetch` + `ReadableStream` (split on blank lines, parse each `data:` line). `handleEvent()`
  turns events into a live activity list:
  - each tool call with a plain label (`TOOL_LABELS`) and a summarized result (`summarizeResult`);
  - progress notes, status lines, and errors with **Try again**.
- **Result** (`renderCriteria`):
  - Group chips; the agent's summary; model, lookups, calls and tokens.
  - One section per group (`GROUPS`), each with **criterion cards** (`criterionCard`): ID,
    must / must not, section, type, the quote, codes (invalid ones marked), drugs (ingredients,
    NDC count, sample NDCs, matched names), window, review reason / "What the expert checks",
    rationale, problems, links to the lookups that back it, and the tags "Approximate timing" and
    "Expert checks timing".
  - **The criteria text panel** (`renderCriteriaText`, `rulesByLine`): every line of the
    published criteria, tagged with the rules taken from it. Clicking a tag scrolls to its card.
  - Every lookup, the progress notes, the step's SQL and JSON.
  - In-step links scroll instead of changing the hash.
  - Finishing calls `loadReview()`.

**Step 3:**
- `renderReview`:
  - Warnings, with a link to step 2 if it isn't built.
  - Count chips: in the query, expert checks, not in the query, flagged.
  - Sections from `REVIEW_GROUPS`, each item showing ID, section and requirement, the quote and
    the note.
  - The commented query, with **Download .sql** and **Copy SQL**.
- **Run on Databricks:** setup instructions, or **Open member dashboard** (a link to
  `/trials/{id}/members`).

## The member dashboard (`members.js`)

- `load()` POSTs `…/review/dashboard`. It shows a spinner and skeleton, or an error (a 503 links back to
  step 3's setup instructions). **Run again** repeats it.
- `render()` fills the header, banners and KPIs, then:
  - `renderMap()`: Leaflet with `preferCanvas`. Panes keep rings under the sites and sites over the ZIP
    bubbles. Colors come from the theme tokens (`bandColor()` maps distance bands to `--s6…--s1`).
  - `renderDistance()`, `renderWho()`: ECharts.
  - `renderFunnel()`: HTML bars.
  - `initSites()` / `renderSites()`: the sortable site table. A row click calls `focusSite()`.
  - `renderStates()`: `renderTileMap()` with unit "members". It drives the site table's state filter.
  - `renderRules()`: the rule counts and the statements that ran.
- **The member list** loads on demand. `listRows()` applies the distance filter, and `csvField()`
  quotes CSV fields; the file is built in the browser.
- **Theme:** a theme change swaps the tile URL (light or dark grey) and re-renders, so charts pick up the
  new tokens.

## Styles (`styles.css`)

- Tokens on `:root`: colors, radii, shadow, font, plus status pairs (`--go`, `--soon`, `--err` and
  their `-soft` versions).
- Dark mode via `@media (prefers-color-scheme: dark)`.
- Shared parts: `.panel`, `.kpi*`, `.table-scroll`, `.code-block`, `.banner`, `.tag`, `.pill`,
  `.stepper`.
- Step parts: `.trace` (rule cards), `.group-chip` (+ `.group-codable` / `-needs_review` /
  `-flagged` / `-expert`), `.rv-*` (step 3), `.md-*` (member dashboard, including Leaflet popup and
  tooltip theming).
- Responsive: below 1100px grids get fewer columns; below 680px (phones) the stepper stacks
  vertically and panels take the full width.

## Caching

The server sends `Cache-Control: no-cache` on every page and static file (`revalidate_frontend` in
`main.py`), so browsers check the ETag on each load and never run an old script against a newer
page.

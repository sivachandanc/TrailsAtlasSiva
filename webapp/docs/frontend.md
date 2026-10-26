# Frontend

Plain HTML, CSS and JavaScript in `webapp/frontend/`, served by FastAPI. No build step, no
framework.
- **Charts:** Apache ECharts 5.5.1 from jsDelivr, on the search and trial pages.
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
  - Earlier Databricks results are cleared, because the query changed.
- **Run on Databricks:**
  - Setup instructions, or the two buttons.
  - `runOnWarehouse()` POSTs, disables both buttons while running, shows a spinner and then the
    result or error, and fills **Show exactly what ran**.
  - The count is shown big. The list is a table (`LIST_LABELS` headings).
  - **Download CSV** is built in the browser by `csvField()` (quotes commas, quotes and line
    breaks) and never touches the server.

## Styles (`styles.css`)

- Tokens on `:root`: colors, radii, shadow, font, plus status pairs (`--go`, `--soon`, `--err` and
  their `-soft` versions).
- Dark mode via `@media (prefers-color-scheme: dark)`.
- Shared parts: `.panel`, `.kpi*`, `.table-scroll`, `.code-block`, `.banner`, `.tag`, `.pill`,
  `.stepper`.
- Step parts: `.trace` (rule cards), `.group-chip` (+ `.group-codable` / `-needs_review` /
  `-flagged` / `-expert`), `.rv-*` (step 3).
- Responsive: below 1100px grids get fewer columns; below 680px (phones) the stepper stacks
  vertically and panels take the full width.

## Caching

The server sends `Cache-Control: no-cache` on every page and static file (`revalidate_frontend` in
`main.py`), so browsers check the ETag on each load and never run an old script against a newer
page.

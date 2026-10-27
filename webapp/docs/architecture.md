# Architecture

## Components

```
Browser (static HTML + vanilla JS)
   │  fetch /api/...            (no build step, no framework)
   ▼
FastAPI app  (webapp/backend/main.py)
   ├─ api/routes.py ............ every /api endpoint
   ├─ sources/ ................. trial registries (ClinicalTrials.gov today)
   ├─ services/ ................ pure logic: dashboards, filters, SQL, review
   ├─ agents/ .................. the AI pharmacist (LangGraph + Claude)
   ├─ terminology/ ............. UMLS (ICD-10-CM) and RxNorm (drugs) clients
   └─ warehouse/ ............... Databricks SQL Statement API client
   │
   ▼  outbound HTTPS (one shared httpx.AsyncClient)
ClinicalTrials.gov API v2 · UMLS REST · RxNav REST · Anthropic API · Databricks SQL warehouse
```

The app **stores nothing**. Every trial is fetched live from ClinicalTrials.gov; results are kept
in in-memory caches that vanish when the process stops. Member data is only ever read from
Databricks on request and passed straight to the browser.

## Folder map

```
webapp/
├── backend/
│   ├── main.py                    FastAPI app, startup (lifespan), page routes, cache headers
│   ├── config.py                  every setting; reads .env
│   ├── models.py                  all Pydantic models (API shapes and internal data)
│   ├── api/routes.py              /api endpoints, dependencies, caches, SSE streaming
│   ├── sources/
│   │   ├── __init__.py            build_sources(): registry of trial sources
│   │   ├── base.py                TrialSource protocol, NCT_ID regex, errors
│   │   └── clinicaltrials_gov.py  ClinicalTrials.gov v2 adapter (field paths → models)
│   ├── services/
│   │   ├── cache.py               TTLCache: in-memory, expiring
│   │   ├── summary.py             search results → dashboard (KPIs, charts, table)
│   │   ├── trial_summary.py       one trial → dashboard; eligibility text parser
│   │   ├── eligibility_filters.py step 1: age and sex → SQL
│   │   ├── criteria_filter.py     step 2: checks the agent's rules, writes SQL
│   │   ├── review.py              step 3: combined commented query, count and list SQL
│   │   └── member_dashboard.py    member dashboard: funnel/demographics/geography SQL, distances
│   ├── agents/
│   │   ├── llm.py                 builds the Claude chat model
│   │   ├── criteria_graph.py      the LangGraph state machine
│   │   ├── prompts.py             system prompt and user message
│   │   ├── tools.py               the 6 tools the model can call
│   │   └── member_schema.py       member tables/columns (SQL, prompt and schema page)
│   ├── terminology/
│   │   ├── umls.py                ICD-10-CM search, code check, child codes
│   │   └── rxnorm.py              drug search, ATC classes, NDCs, brand names
│   ├── warehouse/
│   │   └── databricks.py          runs SQL on a Databricks SQL warehouse
│   └── geo/
│       ├── __init__.py            ZIP → point, haversine miles, state codes → names
│       ├── zip_centroids.csv      US Census 2024 ZCTA centroids (~33.8k ZIPs)
│       └── build_zip_centroids.py regenerates the CSV from the Census Gazetteer
├── frontend/
│   ├── index.html, search.html, trial.html, eligibility.html, members.html, data-schema.html
│   ├── js/common.js               nav, search box, helpers shared by every page
│   ├── js/{home,search,trial,eligibility,members,data-schema}.js   one script per page
│   ├── styles.css                 all styles; light and dark themes
│   └── us_tiles.js                US state tile-map layout
└── docs/                          this documentation
tests/webapp/                      pytest suite (no network)
```

## Startup

`main.py` → `lifespan()` runs once when the server starts:

1. Opens **one** `httpx.AsyncClient` (timeout `HTTP_TIMEOUT_SECONDS`, `accept: application/json`),
   reused for every outbound call so connections are pooled.
2. Builds and stores on `app.state`:
   - `sources` = `build_sources(client)` → `{"clinicaltrials_gov": ClinicalTrialsGovSource}`
   - `umls` = `UmlsClient(client, UMLS_API_KEY)`
   - `rxnorm` = `RxNormClient(client)`
   - `criteria_llm_factory` = `make_criteria_llm` (a function; the model is built per run)
   - `warehouse` = `DatabricksClient(client, host, token, warehouse_id, catalog, schema, timeout)`
3. On shutdown the client is closed.

Routes get these through FastAPI dependencies (`get_sources`, `get_umls`, `get_rxnorm`,
`get_llm_factory`, `get_warehouse` in `routes.py`). Tests override the dependencies with fakes.

`config.py` calls `load_dotenv(<repo root>/.env, override=True)` at import, so `.env` is read before anything else and its values win over variables already set in the environment.

## Request flow, end to end

```
/                      home: search box, live open-trial count
/search?condition=X    GET /api/search          → ClinicalTrials.gov (paged) → summary.py → dashboard
/trials/NCT…           GET /api/trials/{id}     → ClinicalTrials.gov (one study) → trial_summary.py
/trials/NCT…/eligibility
   step 1              GET  /filters/demographics      → eligibility_filters.py
   step 2              GET  /filters/criteria           (cached result or 404)
                       POST /filters/criteria/run       → LangGraph agent → criteria_filter.py (SSE stream)
   step 3              GET  /filters/review             → review.py (combines 1 and 2)
                       GET  /api/warehouse              (is Databricks set up?)
/trials/NCT…/members   member dashboard (opened from step 3)
                       POST /filters/review/dashboard   → member_dashboard.py: 3 aggregate statements
                                                          → Databricks, then distances to sites
                       POST /filters/review/members     → review.py list SQL → Databricks (+ nearest site)
                       (POST /filters/review/count      → count SQL; API only)
/data-schema           GET /api/member-schema    → member_schema.py
```

## Caches (all in memory, per process)

| Cache | Where | Key | Lifetime | Holds |
|---|---|---|---|---|
| `_cache` | routes.py | (source, country, condition lowercased) | 30 min | search dashboards |
| `_trial_cache` | routes.py | (source, NCT ID) | 30 min | trial dashboards |
| `_count_cache` | routes.py | (source, country) | 1 hour | open-trial count |
| `_criteria_cache` | routes.py | (source, NCT ID) | 24 hours | step 2 results (they cost money) |
| `_criteria_running` | routes.py | (source, NCT ID) | while running | guards against two runs at once |
| UMLS `_cache` | umls.py | per call | 24 hours, 20k entries | ICD lookups |
| RxNorm `_cache` | rxnorm.py | per URL+params | 24 hours, 20k entries | drug lookups, NDC lists |

`TTLCache` (`services/cache.py`) drops expired entries on read and, when full, evicts the entry
closest to expiry. **Member data is never cached.**

## Design rules the code follows

- **Sources map into canonical models.** Everything after `sources/` sees only `Trial` /
  `TrialDetail`, so another registry can be added without touching the rest.
- **Services are pure functions.** `summary.py`, `trial_summary.py`, `eligibility_filters.py`,
  `review.py` do no I/O; `criteria_filter.py` only calls the terminology clients. Easy to test.
- **The model proposes, code decides.** Claude never writes SQL and never sees member data. Every
  code, drug and quote it returns is checked again before it can reach the SQL.
- **Transparency.** Every step returns its reasons (age reasons, rule rationales, tool logs, SQL
  comments) so a person can see why each filter exists.
- **The browser never sends SQL.** Endpoints that run SQL take only a trial ID; the server rebuilds
  the query from its own checked data.
- **One place for table and column names:** `agents/member_schema.py`, used by the SQL builders,
  the agent's prompt and the schema page.

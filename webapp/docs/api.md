# HTTP API

All JSON endpoints are under `/api` and defined in `webapp/backend/api/routes.py`. FastAPI serves
interactive docs at **`/docs`** and the OpenAPI schema at `/openapi.json`. Response shapes are the
Pydantic models in `webapp/backend/models.py`.

Errors come back as `{"detail": "<plain-language message>"}` with the status codes listed per
endpoint. Validation errors from FastAPI (wrong parameter types, a too-short condition) are `422`
with `detail` as a list.

**Common parameter:** `source` (default `clinicaltrials_gov`). An unknown source returns `422`.
**Trial IDs** are normalized: `nct05909046`, ` NCT05909046 ` and `05909046` all mean
`NCT05909046`. Anything else that isn't `NCT` + 8 digits returns `422`.

## Pages (HTML)

Served by `main.py`; each returns a static file, and the page's script calls the API.

| Path | File | Page |
|---|---|---|
| `/` | `index.html` | Home |
| `/search?condition=…` | `search.html` | Condition search dashboard |
| `/trials/{id}` | `trial.html` | Single-trial dashboard |
| `/trials/{id}/eligibility` | `eligibility.html` | Eligibility filter builder (steps 1–3) |
| `/data-schema` | `data-schema.html` | Member tables the SQL expects |
| `/health` | n/a | `{"status": "ok"}` |
| `/js/*`, `/styles.css`, … | `frontend/` | Static files |

Every non-`/api` response gets `Cache-Control: no-cache` (middleware `revalidate_frontend`), so
browsers always revalidate and never run an old script against a new page.

---

## Sources and stats

### `GET /api/sources`
Lists trial sources. → `list[SourceOption]`
```json
[{"id": "clinicaltrials_gov", "label": "ClinicalTrials.gov", "countries": ["United States"]}]
```

### `GET /api/stats/open-trials`
How many trials are open with a US site. Cached for an hour.

| Param | Default | |
|---|---|---|
| `source` | `clinicaltrials_gov` | |
| `country` | `United States` | must be one the source supports, else `422` |

→ `{"source": "...", "country": "United States", "count": 25266}` · `502` if the source fails.

---

## Trial search and dashboards

### `GET /api/search`
Open trials for a condition, summarized for the dashboard. Cached 30 minutes per
(source, country, condition).

| Param | Required | Notes |
|---|---|---|
| `condition` | yes | 2–200 characters; extra spaces are collapsed |
| `source` | no | |
| `country` | no | default `United States` |

→ `SearchResult`: `total_matching`, `shown`, `capped`, `kpis`, `by_state`, `top_cities`,
`top_conditions`, `phases`, `study_types`, `purposes`, `intervention_types`, `sponsor_classes`,
`sex`, `age_groups`, `starts_by_month`, `completions_by_month`, `trials` (table rows).
See [trial-search.md](trial-search.md). Errors: `422`, `502`.

### `GET /api/trials/{trial_id}`
One trial in any status, with derived dashboard values. Cached 30 minutes.

→ `TrialDashboard`: `trial` (`TrialDetail`, the full record), `status_label`, `phase_labels`,
`is_open`, `banners`, `kpis`, `by_state`, `other_countries`, `timeline`, `today_position`,
`timeline_note`, `eligibility` (parsed inclusion/exclusion lists).
Errors: `422` bad ID · `404` no such trial · `502` source failed.

---

## Eligibility filters

All take `{trial_id}` and `source`, and return `422` / `404` / `502` like `/api/trials/{id}`.

### `GET /api/trials/{trial_id}/filters/demographics` (step 1)

| Param | Default | |
|---|---|---|
| `reference_date` | today | ISO date; ages are measured on this day |

→ `DemographicFilter`: `age` (`minimum`, `maximum`, `applies`, `rule`, `born_on_or_before`,
`born_after`, `reasons`, `sql`), `sex` (`raw`, `allowed`, `applies`, `rule`, `gender_based`,
`gender_description`, `reasons`, `sql`), `warnings`, `sql`, `definition`.
See [step1-age-sex.md](step1-age-sex.md).

### `GET /api/trials/{trial_id}/filters/criteria` (step 2, cached result)
The step 2 result from an earlier run (kept 24 hours). `404` until it has been built.
→ `CriteriaFilter` (see below).

### `POST /api/trials/{trial_id}/filters/criteria/run` (step 2, run the agent)
Runs the AI pharmacist. **Each call is billed to the Anthropic key.** Returns a
**server-sent event stream** (`text/event-stream`); each event is a line `data: {json}`.

| Event `type` | Fields | When |
|---|---|---|
| `status` | `message` | start, and while the checks run after submission |
| `note` | `text` | the model's progress notes (thinking updates or text) |
| `tool_call` | `id`, `name`, `args` | the model asked for a lookup |
| `tool_result` | `id`, `name`, `result` (≤600 chars), `is_error` | the lookup's result |
| `submitting` | `count` | the model submitted its rules |
| `error` | `message` | the run stopped; the stream ends |
| `done` | `result` (`CriteriaFilter`) | finished; the result is now cached |

Errors before the stream starts: `422` no eligibility text · `409` already running for this
trial · `503` no `ANTHROPIC_API_KEY`. Errors during the run arrive as `error` events: bad key,
rate limit, API failure, a lookup failure, too many steps, a refusal, or two text answers
without submitting.

**`CriteriaFilter`:** `trial_id`, `model`, `reference_date`, `criteria` (list of `CriterionOut`),
`tool_calls` (every lookup: `id`, `name`, `args`, `result`, `is_error`), `notes`,
`agent_summary`, `sql`, `definition`, `usage` (`input_tokens`, `output_tokens`,
`cache_read_input_tokens`, `llm_calls`).

**`CriterionOut`:** `id` (`C1`…), `group` (`codable` / `needs_review` / `covered` / `flagged`),
`source_text`, `quote_found`, `section`, `requirement`, `type`, `codes` (`code`, `name`,
`valid`), `drugs` (`name`, `rxcui`, `atc_class`, `atc_name`, `ingredients`, `ndc_count`, `ndcs`,
`names`, `valid`), `window`, `window_text`, `approximate`, `partial`, `covered_by_step`,
`reason_not_codable`, `rationale`, `problems`, `tool_call_ids`, `sql`.
See [step2-ai-pharmacist.md](step2-ai-pharmacist.md).

### `GET /api/trials/{trial_id}/filters/review` (step 3)
Steps 1 and 2 as one commented Databricks query. Works before step 2 is built (age and sex
only, with a warning).

→ `ReviewFilter`: `reference_date`, `criteria_built`, `model`, `applied`, `expert_checks`,
`not_applied`, `flagged`, `covered` (each a list of `ReviewItem`: `id`, `source_text`,
`section`, `requirement`, `note`), `warnings`, `sql` (the full file: header, query, `;`, notes),
`query` (the SELECT alone, which the count and list wrap), `conditions` (each WHERE condition in
order as `ReviewCondition`: `id`, `label`, `sql`, `expert`; the dashboard's funnel steps).
See [step3-review-and-databricks.md](step3-review-and-databricks.md).

---

## Databricks

### `GET /api/warehouse`
→ `WarehouseStatus`: `configured`, `host`, `warehouse_id`, `catalog`, `schema_name`, `missing`
(the `.env` settings still needed), `member_list_limit`. **Never includes the token.**

### `POST /api/trials/{trial_id}/filters/review/count`
Rebuilds the step 3 query on the server and runs
`SELECT COUNT(*) AS member_count FROM (<query>) AS eligible`. No request body: **the browser
never sends SQL.**

→ `MemberCount`: `trial_id`, `count`, `criteria_built`, `statement` (exactly what ran),
`elapsed_ms`. Header `Cache-Control: no-store`.
Errors: `503` Databricks not set up · `502` Databricks error (bad token, failed statement with
Databricks' message, timeout after 120 s).

### `POST /api/trials/{trial_id}/filters/review/members`
Runs the member list: `member_id, first_nme, last_nme, birth_dte, gender_cde,
state_or_province_cde, zip_5_postal_cde` for members matching the query, ordered by `member_id`,
limited to `MEMBER_LIST_LIMIT`.

The server adds two columns after the query: `nearest_site` (the nearest open site's name, city
and state) and `distance_miles` (straight line from the member's ZIP centroid, one decimal), or
null when the ZIP isn't found.

→ `MemberList`: `trial_id`, `columns`, `rows` (every value a string or null), `limit`,
`truncated` (more members match), `criteria_built`, `statement`, `elapsed_ms`.
Header `Cache-Control: no-store`. Errors as for count.

### `POST /api/trials/{trial_id}/filters/review/dashboard`
Runs three aggregate statements built from the step 3 query (funnel, age and sex, ZIP code and
state) and measures each ZIP's distance to the trial's sites. Returns no names or member IDs.

→ `MemberDashboard` (fields in [member-dashboard.md](member-dashboard.md#response-shape-memberdashboard)).
Header `Cache-Control: no-store`. Errors as for count, plus `404` for an unknown trial.

---

## Member schema

### `GET /api/member-schema`
→ `MemberSchema`: `tables` (`name`, `status`, `grain`, `key`, `used_for`, `columns` with
`name`, `type`, `description`, `nullable`), `ddl` (CREATE TABLE statements, Spark types).
Describes the shape only; reads no data. See [member-data.md](member-data.md).

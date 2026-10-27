# Configuration

All settings live in `webapp/backend/config.py`. Values marked **.env** can be set in a `.env` file
at the repo root (read by `python-dotenv` at startup) or as environment variables. Restart the app
after changing them. Secrets are never sent to the browser.

**`.env` wins.** A setting in `.env` overrides the same variable set in your shell, your profile,
or Windows/macOS system settings (`load_dotenv(..., override=True)`), so a stale system-level
`ANTHROPIC_API_KEY` or `ANTHROPIC_BASE_URL` can't sneak in. Variables that aren't in `.env` still
come from the environment. To switch off a system-level setting without deleting it, put it in
`.env` with a blank value (`ANTHROPIC_BASE_URL=`): blank counts as not set.

## Example `.env`

```bash
# Step 2 (AI pharmacist) and code lookups
ANTHROPIC_API_KEY=sk-ant-...
UMLS_API_KEY=...

# Optional: a different model or an API gateway/proxy
# CRITERIA_MODEL=claude-sonnet-5-5
# ANTHROPIC_BASE_URL=https://gateway.example.com
# CRITERIA_RETRIES=3            # retries per model call (connection, timeout, 408/409/425/429, 5xx)
# CRITERIA_RETRY_SECONDS=5      # wait before each retry

# Member tables (catalog.schema.table works too)
MEMBERS_TABLE=main.claims.members
MX_CLAIMS_TABLE=main.claims.mx_claims
RX_CLAIMS_TABLE=main.claims.rx_claims

# Optional: run the step 3 query on Databricks
DATABRICKS_HOST=https://adb-1234567890.12.azuredatabricks.net
DATABRICKS_TOKEN=dapi...
DATABRICKS_HTTP_PATH=/sql/1.0/warehouses/abc123     # or DATABRICKS_WAREHOUSE_ID=abc123
# DATABRICKS_CATALOG=main
# DATABRICKS_SCHEMA=claims
# MEMBER_LIST_LIMIT=1000
```

## Trial data (ClinicalTrials.gov)

| Setting | Default | .env | Meaning |
|---|---|---|---|
| `CTGOV_BASE_URL` | `https://clinicaltrials.gov/api/v2` | no | API root |
| `HTTP_TIMEOUT_SECONDS` | `60` | no | Timeout for every outbound HTTP call |
| `OPEN_STATUSES` | `RECRUITING`, `NOT_YET_RECRUITING` | no | What counts as an open trial or site |
| `MAX_TRIALS` | `10000` | no | A search stops after this many trials (sorted by relevance) |
| `PAGE_SIZE` | `1000` | no | Trials per ClinicalTrials.gov page |
| `CACHE_TTL_SECONDS` | `1800` (30 min) | no | Search and trial dashboard cache lifetime |

No key is needed for ClinicalTrials.gov.

## Terminology

| Setting | Default | .env | Meaning |
|---|---|---|---|
| `UMLS_API_KEY` | none | **yes** | UMLS key (free from NLM). Needed for ICD-10-CM lookups in step 2 |
| `UMLS_BASE_URL` | `https://uts-ws.nlm.nih.gov/rest` | no | UMLS REST root |
| `UMLS_CACHE_TTL_SECONDS` | `86400` | no | Lookup cache lifetime |
| `UMLS_MAX_CONCURRENT` | `8` | no | Parallel UMLS calls (stays under the rate limit) |
| `ICD_SOURCE` | `ICD10CM` | no | UMLS vocabulary used for codes |

RxNorm (RxNav) needs no key. Its root is `RXNAV` in `terminology/rxnorm.py`.

## AI pharmacist (step 2)

| Setting | Default | .env | Meaning |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | none | **yes** | Without it, running step 2 returns 503 with instructions |
| `CRITERIA_MODEL` | `claude-opus-5-5` | **yes** | Model for the agent. Blank = default |
| `ANTHROPIC_BASE_URL` | none (Anthropic's default) | **yes** | Custom endpoint, e.g. a gateway. Passed only when set |
| `CRITERIA_EFFORT` | `high` | no | `output_config.effort` sent with each call |
| `CRITERIA_RETRIES` | `3` | **yes** | Retries per model call when it fails in a way waiting can fix. `0` turns retrying off |
| `CRITERIA_RETRY_SECONDS` | `5` | **yes** | Wait before each retry. Raise it for endpoints that refuse requests arriving too soon |
| `RECURSION_LIMIT` | `100` | no (`criteria_graph.py`) | Max graph steps (~45 rounds of lookups) |

The model is called with options tuned for Claude Opus 5.5 (adaptive thinking with progress
updates, effort, a server-side fallback beta). Another model or a gateway may reject some of them;
see `agents/llm.py`.

## Member tables

| Setting | Default | .env | Meaning |
|---|---|---|---|
| `MEMBERS_TABLE` | `members` | **yes** | One row per member |
| `MX_CLAIMS_TABLE` | `mx_claims` | **yes** | Medical claims |
| `RX_CLAIMS_TABLE` | `rx_claims` | **yes** | Pharmacy claims |

Column names are fixed in `agents/member_schema.py`; see [member-data.md](member-data.md).

## Databricks (optional)

| Setting | Default | .env | Meaning |
|---|---|---|---|
| `DATABRICKS_HOST` | none | **yes** | Workspace URL. `https://` is added if missing |
| `DATABRICKS_TOKEN` | none | **yes** | Personal access or service principal token with *Can use* on the warehouse |
| `DATABRICKS_WAREHOUSE_ID` | none | **yes** | SQL warehouse ID |
| `DATABRICKS_HTTP_PATH` | none | **yes** | Alternative to the ID: `/sql/1.0/warehouses/<id>`; the last segment is used |
| `DATABRICKS_CATALOG` | none | **yes** | Default catalog for unqualified table names |
| `DATABRICKS_SCHEMA` | none | **yes** | Default schema for unqualified table names |
| `DATABRICKS_TIMEOUT_SECONDS` | `120` | no | A statement still running after this is cancelled |
| `MEMBER_LIST_LIMIT` | `1000` | **yes** | Most members returned by the dashboard's "Load member list" |
| `DASHBOARD_RADII_MILES` | `(10, 25, 50, 100)` | no | Distances the member dashboard counts members within, per site |

Host, token and warehouse are all required; `GET /api/warehouse` reports which are missing.
Host and HTTP path are on the warehouse's **Connection details** tab in Databricks.

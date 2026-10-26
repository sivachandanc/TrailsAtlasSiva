# Security, data handling and operations

## What data goes where

| Data | Goes to | Stored? |
|---|---|---|
| Condition searches, trial IDs | ClinicalTrials.gov | no (30-minute in-memory cache) |
| Trial text (criteria, titles, summary, arms) | Anthropic API, when step 2 runs | no (24-hour in-memory cache of the result) |
| Code and drug lookups | UMLS, RxNav | no (24-hour in-memory cache) |
| Member table and column **names** | Anthropic API (in the prompt) | no |
| **Member rows** | Databricks → server → your browser only | **never**: not cached, not logged, `Cache-Control: no-store` |

The AI pharmacist never receives member data. The CSV download is built in the browser.

## Secrets

- `ANTHROPIC_API_KEY`, `UMLS_API_KEY` and `DATABRICKS_TOKEN` are read from `.env` and used only in
  outbound calls. They're never returned by any endpoint: `/api/warehouse` reports whether a token
  is set, not the token.
- Keep `.env` out of version control.
- Use a Databricks token with only what's needed: *Can use* on the warehouse, and SELECT on the
  three tables.

## SQL safety

- **The browser never sends SQL.** Endpoints that run SQL take a trial ID. The server rebuilds the
  query from its own checked data.
- **Values in the SQL come from checked sources:**
  - ICD codes confirmed by UMLS.
  - NDCs from RxNorm, quoted as 11-digit strings.
  - Drug-name regexes built from letters and digits only.
  - Dates computed by code.
  - Age and sex values computed by code.
  - Table names from `.env` (trusted configuration).
- **Comments can't escape.** All free text (criteria quotes, rationales, reasons) is collapsed to
  one line before going into a `--` comment.
- **`NOT IN` is NULL-safe.** Every subquery filters `inferred_member_id IS NOT NULL`.

## Running it

```bash
make web            # development server with reload, http://127.0.0.1:8000
uv run fastapi run webapp/backend/main.py --port 8000   # without reload
```
- One process holds all caches. With several workers, each has its own caches, and a step 2 result
  built in one worker isn't visible to another.
- `/health` returns `{"status": "ok"}`.
- `/docs` is the interactive API reference.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| A page shows only its header after an update | The browser ran an old cached script. Hard-refresh (Cmd+Shift+R). The `no-cache` header prevents this from then on |
| Step 2: "needs an Anthropic API key" (503) | Set `ANTHROPIC_API_KEY` and restart |
| Step 2: "Anthropic rejected the API key" | Wrong or revoked key, or a gateway at `ANTHROPIC_BASE_URL` that needs a different one |
| Step 2: "The API call failed: BadRequestError" with a custom model | The model or gateway doesn't accept the Opus 5.5 options in `agents/llm.py` (thinking display, effort, betas) |
| Step 2: "already being built" (409) | A run is in progress for this trial. Wait for it to finish |
| Step 2: "used too many lookups" | Hit `RECURSION_LIMIT`. Run again; very long criteria may need a higher limit |
| Step 2: "A code lookup failed … UMLS_API_KEY" | Set the UMLS key |
| Step 3 count: "needs a Databricks SQL warehouse" (503) | Add the settings it names to `.env` |
| "Databricks rejected the token" | Token wrong or expired, or it can't use the warehouse |
| `[TABLE_OR_VIEW_NOT_FOUND]` | Set `MEMBERS_TABLE` / `MX_CLAIMS_TABLE` / `RX_CLAIMS_TABLE` (fully qualified), or `DATABRICKS_CATALOG` and `DATABRICKS_SCHEMA` |
| "took longer than 120 seconds, so it was cancelled" | The warehouse was starting, or the query is heavy. Try again; start the warehouse first |
| A count looks too high | Check the step 3 lists. Rules "not in the query" and "flagged" remove no one, and expert checks still need review |
| A medication rule matches too much | Name matching includes combination products (Synjardy contains empagliflozin). That's intended for "taking X". See the drug card's matched names |

## Known limits

- Only the primary diagnosis per medical claim is checked.
- Voided claims aren't excluded yet.
- Drug duration, dose and new starts can't be checked from claims. Must-have rules filter on the
  drug and an expert checks the rest.
- Very large drug classes (e.g. insulins) put thousands of NDCs into one condition. Databricks
  handles it, but the SQL is long.
- All caches are lost on restart. A step 2 result must be rebuilt (and paid for) after that.

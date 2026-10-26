# Trails-POC: web app documentation

Trails-POC finds open clinical trials in the US and turns a trial's eligibility rules into SQL that
finds matching health-plan members in claims data. This folder documents the whole web app
(`webapp/`), from the HTTP request to the SQL that runs on Databricks.

The sandbox in `internal_core/` (DuckDB experiments) is separate and isn't covered here.

## What it does

1. **Find trials.** Search open US trials by condition, or open one trial by its NCT ID. Live data
   from the ClinicalTrials.gov API, shown as dashboards.
2. **Build eligibility filters for one trial**, in three steps:
   - **Step 1, age and sex:** from the trial's structured fields. Plain code, no AI.
   - **Step 2, eligibility criteria:** an AI pharmacist (Claude, through LangGraph) reads the
     criteria text, looks up real ICD-10-CM codes (UMLS) and drugs (RxNorm), and proposes rules. Code
     then checks every code, drug and quote and writes the SQL.
   - **Step 3, review:** both steps as one Databricks SQL query, with a comment on every condition
     quoting the criteria text it came from. Optionally runs the query on a Databricks SQL warehouse
     to count or list the matching members.

## Quick start

```bash
uv sync      # install dependencies
             # create .env at the repo root with your keys: see configuration.md
make web     # http://127.0.0.1:8000  (interactive API docs at /docs); reloads on code changes
make test    # run the test suite
```

Only `ANTHROPIC_API_KEY` (step 2) and `UMLS_API_KEY` (code lookups) are needed for the full
filter builder. Trial search needs no keys. Databricks settings are optional.

## Contents

| Document | What's in it |
|---|---|
| [architecture.md](architecture.md) | Components, folder map, startup, request flow, caching, design rules |
| [configuration.md](configuration.md) | Every setting and `.env` variable, with defaults |
| [api.md](api.md) | Every HTTP endpoint: parameters, responses, errors |
| [trial-search.md](trial-search.md) | Home page, condition search, single-trial dashboard, the ClinicalTrials.gov source |
| [step1-age-sex.md](step1-age-sex.md) | How ages and sex become SQL, with every edge case |
| [step2-ai-pharmacist.md](step2-ai-pharmacist.md) | The LangGraph agent: prompt, tools, loop, checks, SQL |
| [step3-review-and-databricks.md](step3-review-and-databricks.md) | The commented final query, and running it on Databricks |
| [member-data.md](member-data.md) | The tables and columns the SQL expects |
| [terminology.md](terminology.md) | UMLS (ICD-10-CM) and RxNorm (drugs, NDCs) clients |
| [frontend.md](frontend.md) | Pages, scripts, styles, how each page loads and renders |
| [testing.md](testing.md) | Test layout, fakes, what each file covers, manual checks |
| [security-and-operations.md](security-and-operations.md) | Data handling, secrets, SQL safety, troubleshooting |

## Glossary

| Term | Meaning |
|---|---|
| NCT ID | A trial's ClinicalTrials.gov ID: `NCT` and 8 digits, e.g. `NCT05909046` |
| Open trial | Status `RECRUITING` or `NOT_YET_RECRUITING` |
| MeSH | NLM's standard vocabulary of condition names, attached to each trial |
| ICD-10-CM | US diagnosis codes, as on medical claims (e.g. `E11.9`) |
| RxNorm / RxCUI | NLM's drug vocabulary and its concept IDs (e.g. empagliflozin = `1545653`) |
| ATC class | WHO drug classification (e.g. `A10BK` = SGLT2 inhibitors) |
| NDC | National Drug Code: one specific drug package, as on pharmacy claims (11 digits) |
| UMLS | NLM's Unified Medical Language System; the app uses its ICD-10-CM search |
| Rule / criterion | One atomic requirement from the eligibility text, e.g. "no SGLT2 inhibitor in the past 3 months" |
| must_have / must_not_have | Whether a member needs the rule's codes/drugs (SQL `IN`) or must lack them (`NOT IN`) |
| Codable | Claims data can answer the rule |
| Expert check | A condition that is in the SQL but whose members an expert confirms |

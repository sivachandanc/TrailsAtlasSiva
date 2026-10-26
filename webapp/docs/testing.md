# Testing

```bash
make test            # = uv run pytest tests
uv run pytest tests/webapp/test_review.py -q      # one file
```
The suite makes **no network calls and needs no keys**:
- External services are replaced with fakes (dependency overrides) or `httpx.MockTransport`.
- The model is a scripted fake.
- Async tests use the `anyio` plugin.

## Fakes

| Fake | File | Stands in for |
|---|---|---|
| `ScriptedChatModel` | `fakes_agent.py` | the Claude chat model: returns scripted `AIMessage`s in order, records what it was sent |
| `scripted_turns()`, `SUBMISSION`, `CONTEXT`, `ELIGIBILITY` | `fakes_agent.py` | a 3-call run: two lookup rounds, then `submit_criteria` with 5 rules (covered, codable diagnosis, lab, codable medication, a made-up drug) |
| `FakeIcd` | `fakes_agent.py` | UMLS ICD-10-CM (`O24.11`, `E11`) |
| `FakeRx` | `fakes_agent.py` | RxNorm: empagliflozin/dapagliflozin, class A10BK, NDCs, brand names |
| `FakeSource` | `test_api.py` | ClinicalTrials.gov: one trial `NCT05909046`, a search, a count; can fail on demand |
| `FakeWarehouse` | `test_api.py` | Databricks: answers the count and the list, records statements |

`client_with(source, umls=…, llm_factory=…, warehouse=…)` in `test_api.py` gives a FastAPI
`TestClient` with every dependency overridden and all caches cleared.

## What each file covers

| File | Covers |
|---|---|
| `test_ctgov_mapping.py` | `get_path` over lists; mapping a real saved page; defaults for missing fields; search paging and filters; the 10,000 cap; HTTP errors |
| `test_trial_mapping.py` | mapping a real saved study; `get_trial` fields and errors; `count_open` |
| `test_summary.py` | only open US sites count; state and city breakdowns; MeSH before sponsor text; labels; timing windows; table rows and capping; empty result |
| `test_trial_summary.py` | eligibility parsing (standard and group-prefixed headings, inline content, nested bullets, no headings, empty); site KPIs; closed-trial banners; timeline; age text |
| `test_eligibility_filters.py` | step 1: inclusive limits, units, invalid ages, min > max, sex codes, gender-based warning |
| `test_criteria_agent.py` | step 2: full scripted run (groups, code/drug checks, SQL with LPAD and RLIKE, tool backing, usage); remind and give-up paths; invalid submission sent back; refusal; quote matching; windows; partial must-have drug rules; must-not rules stay in review; prompt contents; name pattern |
| `test_review.py` | step 3: every condition commented with its source; expert checks; rules left out listed after the query; a line break can't escape a comment; age-and-sex-only; gender-based expert check; the `;` isn't swallowed by a trailing comment |
| `test_databricks.py` | Statement API client: request shape and auth, polling, cancel on timeout, failed statement message, rejected token, result chunks, missing settings |
| `test_rxnorm.py` | products, current + historical NDCs, brand names |
| `test_umls.py` | ICD search parameters, caching, missing and rejected key |
| `test_api.py` | every endpoint through HTTP: sources, search, trial, demographics, criteria (stream, cache, 409, 503, errors), review before and after step 2, warehouse status (no token), count and list (query rebuilt on the server, `no-store`, truncation, not configured) |

## Manual checks used during development

These need tools outside the project, so they aren't part of `make test`.

- **SQL in real Spark:** generated queries were run in local PySpark 3.5 on small made-up tables:
  `uv run --no-project --with pyspark==3.5.3 python script.py`. Java 17 is required. This
  confirmed `LPAD`, `RLIKE '\\b…\\b'`, `NOT IN` with NULLs, comments, the `;`, and the count/list
  wrappers.
- **Pages in Chrome:** Playwright driving the installed Chrome
  (`uv run --no-project --with playwright python drive.py`, `channel="chrome"`), against the app
  pointed at a local fake Databricks server. It clicked Count, List and Download CSV.
- **Live lookups:** the agent's tools can be called directly against real UMLS and RxNorm, with no
  model involved:
  ```python
  tools = {t.name: t for t in build_tools(UmlsClient(http, config.UMLS_API_KEY), RxNormClient(http))}
  await tools["drug_class"].ainvoke({"query": "SGLT2"})
  ```

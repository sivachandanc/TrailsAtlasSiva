# Step 2: eligibility criteria (the AI pharmacist)

**Code:** `agents/` (LangGraph agent) and `services/criteria_filter.py` (checks and SQL) ·
**Endpoints:** `POST /api/trials/{id}/filters/criteria/run`, `GET …/filters/criteria` ·
**Tests:** `tests/webapp/test_criteria_agent.py` (with `fakes_agent.py`)

A LangGraph agent on Claude reads the trial's eligibility text and splits it into atomic rules,
using tools to look up real ICD-10-CM codes and drugs. Plain code then checks everything it
proposed and writes the SQL. **The model never writes SQL and never sees member data.**

```
routes.run_criteria_agent ─ context ─► criteria_graph
   prepare ─► pharmacist ⇄ tools (UMLS, RxNorm)
                 │ submit_criteria accepted
                 ▼
              collect ─► finalize ─► criteria_filter.build_criteria_filter ─► CriteriaFilter
```

## 1. Starting a run (`api/routes.py`, `run_criteria_agent`)

1. `load_trial()` fetches the trial (cached).
2. Guards:
   - No eligibility text → `422`.
   - A run already going for this trial → `409` (`_criteria_running`).
   - No API key → `503` (`LlmNotConfigured`).
3. `make_llm()` builds the chat model (`agents/llm.py`).
4. `build_demographic_filter(trial, today)` gives step 1's rules to pass along.
5. Builds the **context** dict:
   - **Source of rules:** `eligibility_text` (the only one).
   - **Already covered:** `age_rule`, `sex_rule`.
   - **Background, to help interpret only:** `official_title`, `brief_summary`, `interventions`,
     `arms`, `conditions`, `study_population`.
6. `build_criteria_graph(llm, umls, rxnorm, model_name)`, then streams
   `graph.astream({"context": context}, {"recursion_limit": 100}, stream_mode="updates")`.
   - Each node's update is turned into SSE events by `agent_events()`.
   - On `finalize`, the result is cached for 24 hours and sent as `done`.

**Error mapping inside the stream:**

| Exception | Message the user sees |
|---|---|
| `GraphRecursionError` | the agent used too many lookups |
| `anthropic.AuthenticationError` | the key was rejected |
| `anthropic.RateLimitError` | the endpoint is rate limiting (after the retries, if they ran) |
| `anthropic.APIStatusError` / `APIConnectionError` | the endpoint call failed, with the HTTP status or "connection failed" (after the retries, if they ran) |
| UMLS / RxNorm errors | a code lookup failed |

## 2. The model (`agents/llm.py`)

`make_criteria_llm()` → `ChatAnthropic` with:

| Option | Value | Why |
|---|---|---|
| `model` | `CRITERIA_MODEL` (default `claude-opus-5-5`) | |
| `max_tokens` | 32,000 | room for thinking and a long `submit_criteria` |
| `streaming` | true | long runs don't time out |
| `thinking` | `{"type": "adaptive", "display": "updates"}` | short progress notes between tool calls, shown live |
| `output_config.effort` | `high` | |
| betas | `thinking-display-updates-2026-08-18`, `server-side-fallback-2026-07-01` | |
| `model_kwargs.fallbacks` | `default` | if a safety classifier declines, the API retries on a fallback model |
| `max_retries` | 0 | the graph retries instead, with a fixed wait (below) |
| `base_url` | `ANTHROPIC_BASE_URL`, only if set | |

## 3. The prompt (`agents/prompts.py`)

**System prompt** (`SYSTEM_PROMPT`, the same for every trial, sent with `cache_control: ephemeral`
so later calls in a run read it from the prompt cache):

1. **Role:** a clinical pharmacist and coder at a health plan.
2. **The member data:** table and column descriptions from `member_schema.prompt_text()`, plus
   what claims can and can't answer:
   - Only the primary diagnosis per claim.
   - Every fill kept.
   - No days of supply, labs, vitals, procedures or notes.
3. **Your job:** split every inclusion and exclusion criterion into atomic rules.
   - Decide `must_have` / `must_not_have` from the meaning, not the heading: "no prior insulin"
     under inclusion is `must_not_have`.
   - The criteria are the only source of rules. Background helps interpret "the study drug", but
     never creates a rule.
4. **Codes and drugs:**
   - Use only ICD-10-CM codes a tool returned, and check the level of the code tree.
   - For drugs, use `find_drug` or `drug_class` and give an ingredient RxCUI or an ATC class.
   - **Never write NDCs.**
   - Look things up in parallel.
5. **Time windows:** describe the window, don't compute dates.
   - `days_ago` with `max_days_ago` (in the last N days), `min_days_ago` (at least N days ago), or
     both.
   - `ever`, `not_stated`, or `event`, using the **standard approximations** in
     `EVENT_APPROXIMATIONS`:

   | Event wording | Window |
   |---|---|
   | currently taking / current / active / at screening / at enrollment / at baseline | last 90 days |
   | within N days/weeks before screening, enrollment, randomization or first dose | last N days |
   | this / current / index pregnancy | last 365 days |
   | postpartum / after delivery | a rule on delivery codes; "immediately postpartum" = last 30 days |
   | current hospitalization / admission | last 30 days |
   | open-ended ("since surgery", "during the study") | no limits |

6. **Rules the data can't answer:** labs, vitals, scores, imaging, most procedures, drug
   duration, dose or new starts, consent, language, site, judgment, willingness.
   - Keep them with `codable: false` and a reason. Never guess a code.
   - A medication rule still names its drugs, so must-have rules can filter on the drug (see
     "partial" below).
7. **Earlier step:** mark a rule `covered_by_step: "age_sex"` when it only repeats the age/sex
   limits. All diagnosis rules are the agent's job: a trial's conditions say what it studies, not
   who can join.
8. **Output:**
   - Copy `source_text` verbatim and write a 1–2 sentence plain rationale.
   - Call `submit_criteria` once with all rules.
9. **Progress updates:** a short plain note before each round of lookups (shown live on the page).

**User message** (`user_message(context)`):
```
Trial <id>: <title>
Already handled by an earlier step:
- Step 1, age and sex: age <rule>; sex <rule>
Background, to help you interpret the criteria (not a source of rules):
Official title / Conditions / Brief summary (max 3,000 chars) / Interventions / Arms / Study population
Eligibility criteria, exactly as published (the only source of rules):
<<<
…criteria text…
>>>
```

## 4. Tools (`agents/tools.py`)

Each tool returns compact JSON. The same text is logged and shown on the page, so a reviewer sees
exactly what the model saw.

| Tool | Arguments | Calls | Returns |
|---|---|---|---|
| `search_icd10cm` | `text` | `umls.search_icd10cm` | up to 12 `{code, name}`, most relevant first |
| `icd10cm_code` | `code` (with dot) | `umls.icd10cm_code` | `{code, name, has_children}` or "not a code" |
| `icd10cm_children` | `code` | `umls.icd10cm_children` | next level of more specific codes |
| `find_drug` | `name` (ingredient, brand, product) | `rxnorm.find_drug` | top 3 matches → ingredients (RxCUI) → their ATC classes |
| `drug_class` | `query` (ATC ID or words) | `rxnorm.drug_class` | up to 5 ATC classes, most specific first, with member ingredients |
| `submit_criteria` | `criteria: list[AgentCriterion]`, `summary` | none | ends the loop; arguments are validated by Pydantic |

### The schema the model submits (`models.py`)

**`AgentCriterion`:**

| Field | Values / meaning |
|---|---|
| `source_text` | the exact phrase from the criteria |
| `section` | `inclusion` / `exclusion` |
| `requirement` | `must_have` / `must_not_have` |
| `type` | `diagnosis`, `medication`, `age_sex`, `lab`, `procedure`, `other` |
| `codable` | claims can answer it |
| `icd10cm_codes` | codes from the ICD tools; each covers its more specific codes |
| `drugs` | list of `AgentDrug`: `name`, `rxcui` (ingredient) and/or `atc_class` |
| `window` | `TimeWindow`: `type` (`ever` / `days_ago` / `event` / `not_stated`), `min_days_ago`, `max_days_ago`, `event`, `approximation` |
| `covered_by_step` | `age_sex` or null |
| `reason_not_codable` | why claims can't answer it |
| `rationale` | plain explanation |

## 5. The loop (`agents/criteria_graph.py`)

**State** (`CriteriaState`):
- `context`
- `messages` (with the `add_messages` reducer, so the conversation grows instead of being replaced)
- `reminders`, `submitted`, `error`, `result`

| Node | Does |
|---|---|
| `prepare` | `[SystemMessage(SYSTEM_PROMPT, cached), HumanMessage(user_message(context))]` |
| `pharmacist` | one model call with the tools bound (`tool_choice="auto"`; forcing a tool is a 400 on Opus 5.5). A refusal, or a `max_tokens` stop without tool calls, sets `error` |
| `tools` | LangGraph `ToolNode(handle_tool_errors=True)`: runs the requested tools. A failed tool, including a schema-invalid `submit_criteria`, comes back as an error message the model can fix |
| `remind` | adds "Please finish by calling submit_criteria…" (once) |
| `gave_up` | error: the model answered in text twice |
| `collect` | `submitted = submitted_call(messages)`: the arguments of the accepted `submit_criteria` |
| `finalize` | `build_criteria_filter(...)` → `result` |

**Routing:**
- After `pharmacist` (`after_pharmacist`):
  - `error` → END
  - tool calls → `tools`
  - plain text, first time → `remind`
  - plain text, second time → `gave_up`
- After `tools` (`after_tools`): an accepted `submit_criteria` → `collect`; otherwise back to `pharmacist`.
- Fixed edges: START → `prepare` → `pharmacist`; `remind` → `pharmacist`; `collect` → `finalize` → END; `gave_up` → END.

`submitted_call()` finds the last AI message's `submit_criteria` call and its matching
`ToolMessage` (same `tool_call_id`). It counts only if that tool run didn't fail.

`RECURSION_LIMIT = 100` graph steps. Each lookup round is 2 steps, so about 45 rounds.

### Retrying the model endpoint
`call_model()` inside `pharmacist` retries a failed model call **after a fixed wait**. Some company
endpoints and gateways refuse requests that arrive too soon after the last one, and the SDK's own
retries wait less than a second at first.

| Setting | Default |
|---|---|
| `CRITERIA_RETRIES` | 3 retries (4 attempts in all) |
| `CRITERIA_RETRY_SECONDS` | 5 seconds before each retry |

- **Retried** (`retryable()`):
  - connection refused or dropped, and timeouts (`APIConnectionError`, `APITimeoutError`)
  - HTTP 408, 409, 425 and 429
  - any 5xx, including Anthropic's 529 "overloaded"
- **Not retried:** a bad key (401), forbidden (403), a bad request (400) and other 4xx. Waiting
  won't fix them, so they fail at once.
- **Shown live:** each retry is sent to the page as it happens, using LangGraph's
  `get_stream_writer()` (a custom stream event), e.g. "The model endpoint didn't accept the
  request (HTTP 503). Trying again in 5 s (retry 1 of 3)." The route streams with
  `stream_mode=["updates", "custom"]` to receive them.
- **When retries run out,** the error says so: "… after 3 retries 5 s apart."
- **Safe to repeat:** a retry resends the same conversation. Nothing is added to the graph state
  until a call succeeds.
- langchain-anthropic raises its own subclasses (`AnthropicAPIError`, `AnthropicConnectionError`).
  They're subclasses of the standard Anthropic errors, so the checks above still apply.

## 6. Checks and SQL (`services/criteria_filter.py`, `build_criteria_filter`)

No LLM here.

1. **Validate** every submitted rule with `AgentCriterion.model_validate`.
2. **ICD codes:** each distinct code is checked once with `umls.icd10cm_code`. Unknown codes are
   dropped and listed in `problems`.
3. **Drugs** (`resolve_drug` → `expand`):
   - **Ingredient (rxcui):**
     - `properties` must exist.
     - `ingredients_of` gives the ingredients (combination products have several).
   - **Class (atc_class):**
     - `drug_class` must contain that exact ATC ID.
     - Its member ingredients are used.
   - **Then, for every ingredient:**
     - **NDCs** (`ndcs_for_ingredient`): every product's current and historical NDCs, with no cap.
     - **Names:** ingredient names plus brand names (`brand_names`).
   - Invalid drugs are dropped and listed in `problems`.
4. **Quote check** (`quote_found`): the quote must appear in the criteria text after normalizing
   case, punctuation and line breaks. Alternatively, for quotes of 4 or more words, at least 90%
   of the words must appear. This catches made-up rules.
5. **Window check:** `min_days_ago > max_days_ago` → flagged as backwards.
6. **Partial rules:** a `must_have` medication rule with confirmed drugs and no codes, where claims
   can't check the timing, duration or dose. That means `codable: false`, or an `event` window
   with no limits.
   - The rule is applied **on the drug alone, at any time**, marked `partial`, and an expert
     checks the rest.
   - Not done for `must_not_have`: removing everyone who ever filled the drug would also remove
     people who qualify.
7. **Group**, checked in this order:

   | Group | When |
   |---|---|
   | `covered` | `covered_by_step` is set |
   | `flagged` | quote not found; or codable but no confirmed codes/drugs; or a backwards window |
   | `codable` (partial) | the partial case above |
   | `needs_review` | `codable: false`; or an `event` window with no limits ("which claims can't place reliably") |
   | `codable` | everything else |

8. **SQL per rule.** `must_have` → `member_id IN (…)`, `must_not_have` → `member_id NOT IN (…)`.
   Both subqueries include `inferred_member_id IS NOT NULL`, because one NULL in a `NOT IN` list
   would make the test fail for every member.
   - **Diagnosis** (`diagnosis_sql`):
     ```sql
     member_id IN (
       SELECT inferred_member_id FROM mx_claims
       WHERE inferred_member_id IS NOT NULL
         AND (UPPER(REPLACE(primary_diagnosis_cd, '.', '')) LIKE 'O2411%' OR …)
         AND first_serviced_dt >= DATE '…'
     )
     ```
     The dot is removed on both sides, so `O24.11` and `O2411` both match. `LIKE 'X%'` includes
     the more specific codes.
   - **Medication** (`medication_sql`), matching NDC **or** drug name:
     ```sql
     member_id NOT IN (
       SELECT inferred_member_id FROM rx_claims
       WHERE inferred_member_id IS NOT NULL
         AND (
           LPAD(CAST(product_id AS STRING), 11, '0') IN ('00597015207', …)
           OR UPPER(brand_nme) RLIKE '\\b(EMPAGLIFLOZIN|JARDIANCE|SYNJARDY|…)\\b'
         )
         AND serviced_dte >= DATE '…'
     )
     ```
     - `LPAD` restores the leading zeros lost because `product_id` is an INT.
     - `name_pattern()` builds a whole-word regex from letters and digits only. Words in a name
       are joined by `[^A-Z0-9]+`, so "insulin glargine" also matches "INSULIN GLARGINE-YFGN".
       Names under 3 characters are dropped. Nothing from a name needs escaping in the SQL.
   - **Windows** (`date_condition`): `max_days_ago` → `date >= today − N`; `min_days_ago` →
     `date <= today − N`; both → a range. Every claim and fill date is kept, so a window means "a
     matching claim dated inside the range".
   - A rule with both codes and drugs uses the diagnosis SQL.
9. **Step SQL:** `SELECT member_id FROM members WHERE <codable rules joined with AND>`. With none:
   everyone passes.
10. **Audit trail:**
    - `tool_logs()`: every lookup.
    - `tool_call_ids`: which lookups' results contain the rule's codes or drugs.
    - `progress_notes()`: the model's notes.
    - `usage_totals()`: tokens, cache reads, number of model calls.
    - `window_text()`: the window in words, e.g. "Filled in the last 90 days (on or after 2026-07-28)".
    - `definition`: the rules as JSON, with `partial`.

## 7. Cost and caching

Each run makes several model calls (the tests' scripted run makes 3; real runs usually make more),
plus free UMLS and RxNorm lookups. The page estimates $0.20–0.50 per run on Opus 5.5. Results are
cached for 24 hours, so reopening the page is free. "Run again" re-bills.

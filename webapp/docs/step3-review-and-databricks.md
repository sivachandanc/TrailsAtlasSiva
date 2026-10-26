# Step 3: review, and running the query on Databricks

**Code:** `services/review.py` (pure), `warehouse/databricks.py` ·
**Endpoints:** `GET …/filters/review`, `GET /api/warehouse`, `POST …/filters/review/count`,
`POST …/filters/review/members` ·
**Tests:** `tests/webapp/test_review.py`, `tests/webapp/test_databricks.py`, `tests/webapp/test_api.py`

## The combined query (`build_review`)

`review_for()` in `routes.py` loads the trial and takes step 2's cached result, if any. It
rebuilds step 1 **on step 2's reference date**, so ages and time windows are measured from the same
day. Then `build_review(demographics, criteria)` produces one query.

**Conditions, in order:**
1. **Step 1 · Age**, if it applies. Source: the `minimumAge` and `maximumAge` values.
2. **Step 1 · Sex**, if it applies. Source: `sex`. With gender-based eligibility it's also an
   **expert check**.
3. **Step 2**: every rule in group `codable` that has SQL, in rule order.

**Comment block above every condition** (`rule_comment`):
```sql
  -- C3 · Exclusion · must not have · medication
  -- Criteria: "Use of an SGLT2 inhibitor in the past 3 months"     ← verbatim from the trial
  -- Why: Any SGLT2 inhibitor fill in the last 90 days excludes the member.
  -- Drugs: SGLT2 inhibitors (ATC A10BK): 392 NDCs and 21 drug names
  -- When: Filled in the last 90 days (on or after 2026-07-28)
  -- EXPERT CHECK: …                                                ← only when it applies
  AND member_id NOT IN ( … )
```
- `Codes:` lists up to 6 ICD codes with their names ("and N more" after that).
- `EXPERT CHECK` appears for:
  - partial rules: "Filtered on the drug alone." plus the reason;
  - approximated event timing: "The timing is approximated: …".
- The rule IDs (`C1`…) match the cards in step 2.

**The whole file:**
```
-- header: trial, reference date, how rules were built, number of EXPERT CHECKs
SELECT member_id
FROM members
WHERE
  <comment block> <condition>
  <comment block> AND <condition>
  …
;
-- Not in this query: claims can't answer these… (needs_review rules, with the reason)
-- Left out after failing a check… (flagged rules, with the problems)
-- Covered by step 1 (age and sex): …
```

**Rules for writing it safely:**
- **`oneline()`** collapses all whitespace in any text that goes into a comment. A line break in
  the criteria text would otherwise end the `--` comment and the rest would run as SQL. Tested
  with `"Type 2 diabetes\nDROP TABLE members"`.
- **The `;` sits on its own line**, because the last condition can end with a `--` comment (e.g.
  `-- at most 75 years old`) that would swallow it.
- Multi-line conditions are indented under their `AND`.

**Before step 2 is built:** the query has age and sex only. `criteria_built` is false, and a
warning (first in `warnings`) links to step 2.

**`ReviewFilter` lists:**

| List | Contains |
|---|---|
| `applied` | every condition in the query (AGE, SEX, C…) |
| `expert_checks` | conditions in the query whose members an expert confirms |
| `not_applied` | `needs_review` rules: a person checks them; they remove no one |
| `flagged` | rules that failed a check: left out; review before relying on the query |
| `covered` | rules that only repeat age or sex |

`sql` is the full file (for copy and download). `query` is the SELECT without the `;` and the
trailing notes: what the count and list wrap.

## Running it on Databricks

### Safety model
- **The browser never sends SQL.** `count` and `members` take only the trial ID. The server
  rebuilds the query from its cached, checked rules, so nothing typed into a page can reach the
  warehouse.
- **Nothing is stored.** Results go straight to the browser. They aren't cached or logged, and
  responses carry `Cache-Control: no-store`.
- **The token stays on the server.** `GET /api/warehouse` reports the host, warehouse and what's
  missing, never the token.

### The statements (`count_sql`, `members_sql`)
```sql
SELECT COUNT(*) AS member_count FROM (
<query>
) AS eligible
```
```sql
SELECT m.member_id, m.first_nme, m.last_nme, m.birth_dte, m.gender_cde,
       m.state_or_province_cde, m.zip_5_postal_cde
FROM members AS m
WHERE m.member_id IN (
<query>
)
ORDER BY m.member_id
LIMIT <MEMBER_LIST_LIMIT + 1>
```
The closing parenthesis is on its own line for the same trailing-comment reason. The list asks
for one row more than the limit, to tell whether it was cut off (`truncated`).

### The client (`warehouse/databricks.py`, `DatabricksClient.query`)
Uses the **Databricks SQL Statement Execution API** over HTTPS with the shared `httpx` client. No
JDBC or ODBC driver is needed.

1. **Not configured** → `WarehouseNotConfigured`, naming the missing settings (route: `503`).
2. **Submit:** `POST {host}/api/2.0/sql/statements` with `Authorization: Bearer <token>` and body:
   - `warehouse_id`, `statement`
   - `wait_timeout: "30s"`, `on_wait_timeout: "CONTINUE"`
   - `format: "JSON_ARRAY"`, `disposition: "INLINE"`
   - optional `row_limit`, `catalog`, `schema`
3. **Wait:** if the state isn't final (`PENDING` / `RUNNING`, e.g. the warehouse is starting),
   poll `GET …/statements/{id}` every second. After `DATABRICKS_TIMEOUT_SECONDS` (120), it sends
   `POST …/statements/{id}/cancel` and raises.
4. **Result:**
   - `SUCCEEDED` → columns from `manifest.schema.columns`, rows from `result.data_array`. Every
     value arrives as a string.
   - Further chunks are followed through `next_chunk_internal_link` until the row limit is reached.
   - Any other final state → `WarehouseError` with Databricks' own message, e.g.
     `[TABLE_OR_VIEW_NOT_FOUND] …`.
5. **HTTP errors:**
   - `401` / `403` → "rejected the token"
   - `404` → check `DATABRICKS_HOST`
   - other ≥400 → the error message
   - network failure → "couldn't be reached"

   All of these surface as `502` with the message.

### On the page
The **Run on Databricks** panel sits under the query:
- **Not set up:** shows the `.env` lines to add and what's still missing.
- **Set up:** says which warehouse it will use, that runs use warehouse compute, and that nothing
  is saved. Then:
  - **Count members** shows the number and the time taken. It reminds you when step 2 isn't
    built, and how many conditions need an expert check.
  - **Show member list** shows a table (member ID, name, birth date, sex, state, ZIP) and says
    when the list was cut off at the limit.
  - **Download CSV** builds the file in the browser, quoting fields with commas or quotes.
  - **Show exactly what ran** shows the statement sent to Databricks.

  Results are cleared whenever the query changes, e.g. after step 2 finishes.

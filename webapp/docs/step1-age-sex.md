# Step 1: age and sex

**Code:** `services/eligibility_filters.py` (pure functions, no I/O) ·
**Endpoint:** `GET /api/trials/{id}/filters/demographics` ·
**Tests:** `tests/webapp/test_eligibility_filters.py`

Turns the trial's structured ClinicalTrials.gov fields (`minimumAge`, `maximumAge`, `sex`,
`genderBased`, `genderDescription`) into a filter on the `members` table, with a plain-language
reason for every decision. No AI.

## Example

Trial: minimum "18 Years", maximum "65 Years", sex FEMALE, measured on 2026-10-26.

```sql
SELECT member_id
FROM members
WHERE birth_dte <= DATE '2008-10-26'  -- at least 18 years old
  AND birth_dte > DATE '1960-10-26'  -- at most 65 years old
  AND gender_cde = 'F'
```
Reasons returned:
- "The sponsor set a minimum age of “18 Years”, so a member must be at least 18 years old on
  Oct 26, 2026: born on or before Oct 26, 2008."
- "The sponsor set a maximum age of “65 Years”. ClinicalTrials.gov treats the maximum as
  inclusive, so a member can be 65 years old but not 66 years: born after Oct 26, 1960."
- "The sponsor set sex to FEMALE, so only female members are included."

## Ages, function by function

1. **`parse_age(raw)`** reads text like `"18 Years"` with the regex
   `^\s*(\d+)\s*(year|month|week|day)s?\s*$` (case-insensitive) → `AgeLimit(raw, value, unit)`.
   - Missing or blank → no limit.
   - Anything else (e.g. `"6 Hours"`, minutes) → `valid=False`, treated as not set, with a warning.
2. **`subtract(date, value, unit)`** measures back from the reference date.
   - Years and months keep the day of month, clamped to the month's length (Feb 29 → Feb 28).
   - Weeks and days subtract days.
3. **`build_age_filter(trial, ref)`**:
   - **Minimum** N → `born_on_or_before = ref − N` → `birth_dte <= DATE '…'` (turning N today counts).
   - **Minimum 0** → no lower limit ("newborns qualify").
   - **Maximum** N → `born_after = ref − (N + 1)` → `birth_dte > DATE '…'`. The maximum is
     inclusive: someone N years old passes, N + 1 doesn't.
   - **Minimum above maximum** → both limits are dropped, with a warning to check the record.
   - **`rule`** in words: "18 to 65 years", "18 years and older", "Up to 17 years", "Any age".
     Mixed units are spelled out: "6 months to 17 years".

Ages are always measured **as of a reference date**: today by default, or step 2's date when
combined in step 3, so both steps agree.

## Sex

**`build_sex_filter(trial)`**:

| ClinicalTrials.gov `sex` | SQL | Rule |
|---|---|---|
| `FEMALE` | `gender_cde = 'F'` | Female only |
| `MALE` | `gender_cde = 'M'` | Male only |
| `ALL` | none | Any sex |
| missing | none | Any sex ("isn't stated") |

The mapping is `GENDER_CODES` in `agents/member_schema.py`.

**Gender-based eligibility.** If `genderBased` is true, a warning says claims record sex, which may
not match gender identity, and quotes the sponsor's `genderDescription`. In step 3 this becomes an
**expert check**.

## Output (`DemographicFilter`)

- `age`, `sex`: each with `applies`, `rule`, `reasons`, `sql` and the source values
- `warnings`
- `sql`: the combined query. With no limits it's `SELECT member_id FROM members`, everyone passes.
- `definition`: the same filter as JSON:
  ```json
  {"step": "age_sex", "trial_id": "NCT1", "reference_date": "2026-10-26",
   "age": {"minimum": "18 Years", "maximum": "65 Years", "born_on_or_before": "2008-10-26", "born_after": "1960-10-26"},
   "sex": {"source_value": "FEMALE", "allowed": ["FEMALE"], "gender_based": false}}
  ```

Step 2 is told the age and sex rules, so it marks criteria that only repeat them (such as
"Adults 18 years or older") as `covered_by_step: "age_sex"` instead of creating duplicate filters.

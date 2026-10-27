# Member data the SQL expects

**Code:** `agents/member_schema.py`, the one place for table and column names. Used by:
- the SQL builders (steps 1–3),
- the AI pharmacist's prompt (`prompt_text()`), which gets names and meanings only and never sees rows,
- the `/data-schema` page and `GET /api/member-schema` (with DDL from `ddl()`).

The SQL is **Databricks / Spark SQL**. Table names come from `.env` (`MEMBERS_TABLE`,
`MX_CLAIMS_TABLE`, `RX_CLAIMS_TABLE`) and can be fully qualified (`catalog.schema.table`). Only
the columns the app reads are listed; the real tables can have more.

## `members` (one row per member)

| Column | Type | Used for |
|---|---|---|
| `member_id` | INT | the member key, joined to `inferred_member_id` in the claims tables |
| `birth_dte` | DATE | age (step 1) |
| `gender_cde` | STRING | sex: `F` / `M` (`GENDER_CODES` maps ClinicalTrials.gov's FEMALE/MALE) |
| `first_nme`, `last_nme` | STRING | member list only |
| `state_or_province_cde`, `zip_5_postal_cde` | STRING | member list, and grouped (counts per ZIP and state) by the member dashboard to measure distance to trial sites |

The list-only columns are in `list_columns`. They're kept out of the AI pharmacist's prompt, and
listed in `MEMBER_LIST_COLUMNS` in display order.

## Medical claims (`mx_claims`)

One row per medical claim.

| Column | Type | Used for |
|---|---|---|
| `inferred_member_id` | INT | `members.member_id` |
| `first_serviced_dt` | DATE | the claim's date, for time windows |
| `primary_diagnosis_cd` | STRING | ICD-10-CM code, with or without the dot. Compared dot-free and upper-cased |

**Limits:**
- Only the **primary** diagnosis is available. A condition coded only as a secondary diagnosis
  isn't found, which matters most for "no history of X" rules. The prompt tells the agent this.
- Voided claims are not excluded yet (`medical_claim_void_ind` is ignored, by choice).

## Pharmacy claims (`rx_claims`)

One row per fill; every fill is kept.

| Column | Type | Used for |
|---|---|---|
| `inferred_member_id` | INT | `members.member_id` |
| `serviced_dte` | DATE | fill date, for time windows |
| `product_id` | INT | the NDC. Leading zeros are lost in an INT, so the SQL compares `LPAD(CAST(product_id AS STRING), 11, '0')` |
| `brand_nme` | STRING | drug name: brand, or generic name for generics. Matched as whole words, upper-cased |

**Limits:**
- Days of supply, quantity and dose aren't read. Rules about **duration** ("for at least 3
  months"), **new starts** or **dose** can't be fully checked. For must-have rules the drug part
  is applied and an expert checks the rest (see step 2, "partial").

## Not used

The member key is `member_id` alone; the `*_src_cde` columns aren't joined. `patient_id`,
`last_serviced_dt`, costs, `phcy_product_service_id` and its qualifier aren't read.

"""The member data the eligibility filters run against.

One place for table and column names: the SQL builders use them, the criteria agent's prompt
describes them (names and meanings only; the agent never sees any member rows), and the
/data-schema page shows them, with DDL. Only the columns the filters read are listed; the real
tables have more. The SQL is written for Databricks / Spark SQL.
"""

from webapp.backend import config

MEMBERS = config.MEMBERS_TABLE
MX_CLAIMS = config.MX_CLAIMS_TABLE
RX_CLAIMS = config.RX_CLAIMS_TABLE

# members.gender_cde values for ClinicalTrials.gov's sex field
GENDER_CODES = {"FEMALE": "F", "MALE": "M"}

TABLES = [
    {
        "name": MEMBERS,
        "status": "required",
        "grain": "one row per member",
        "key": ["member_id"],
        "columns": [("member_id", "INT", "member ID"),
                    ("birth_dte", "DATE", "date of birth"),
                    ("gender_cde", "STRING", "M or F")],
        # Read only by the member list in step 3, never by a filter, so the agent's prompt leaves them out
        "list_columns": [("first_nme", "STRING", "first name"),
                         ("last_nme", "STRING", "last name"),
                         ("state_or_province_cde", "STRING", "state"),
                         ("zip_5_postal_cde", "STRING", "5-digit ZIP code")],
        "used_for": "Step 1 (age and sex); every filter starts from this table. The step 3 member list also "
                    "shows name, state and ZIP.",
    },
    {
        "name": MX_CLAIMS,
        "status": "required",
        "grain": "one row per medical claim, with its primary diagnosis",
        "key": [],
        "columns": [("inferred_member_id", "INT", "member ID (members.member_id)"),
                    ("first_serviced_dt", "DATE", "first date of service on the claim"),
                    ("primary_diagnosis_cd", "STRING", "primary ICD-10-CM diagnosis code, with or without the dot")],
        "used_for": "Step 2 diagnosis rules: ever, in the last N days, at least N days ago, or between two times.",
    },
    {
        "name": RX_CLAIMS,
        "status": "required",
        "grain": "one row per pharmacy claim (every fill kept)",
        "key": [],
        "columns": [("inferred_member_id", "INT", "member ID (members.member_id)"),
                    ("serviced_dte", "DATE", "date the prescription was filled"),
                    ("product_id", "INT", "NDC of the dispensed product, stored as a number (leading zeros dropped)"),
                    ("brand_nme", "STRING", "drug name: the brand, or the generic name for generics")],
        "used_for": "Step 2 medication rules: a fill of the drug, matched by NDC or by drug name, in a time window.",
    },
]

REQUIRED = [t for t in TABLES if t["status"] == "required"]
# The member list's columns, in order
MEMBER_LIST_COLUMNS = ["member_id", "first_nme", "last_nme", "birth_dte", "gender_cde",
                       "state_or_province_cde", "zip_5_postal_cde"]


def all_columns(t: dict) -> list[tuple[str, str, str]]:
    return t["columns"] + t.get("list_columns", [])


def ddl() -> str:
    """CREATE TABLE statements for the columns the filters read (Spark SQL types)."""
    blocks = []
    for t in TABLES:
        width = max(len(c) for c, _, _ in all_columns(t))
        optional = t.get("optional", []) + [c for c, _, _ in t.get("list_columns", [])]
        cols = [f"  {c.ljust(width)} {typ}{'' if c in optional else ' NOT NULL'},  -- {desc}"
                for c, typ, desc in all_columns(t)]
        if t["key"]:
            cols.append(f"  PRIMARY KEY ({', '.join(t['key'])})")
        else:
            cols[-1] = cols[-1].replace(",  --", "   --", 1)
        blocks.append(f"-- {t['grain']}\nCREATE TABLE {t['name']} (\n" + "\n".join(cols) + "\n);")
    return "\n\n".join(blocks)


def prompt_text() -> str:
    lines = []
    for t in REQUIRED:
        cols = ", ".join(f"{c} ({d})" for c, _, d in t["columns"])
        lines.append(f"- {t['name']}: {t['grain']}. Columns: {cols}.")
    lines += [
        "",
        "What these tables can and can't answer:",
        "- Diagnoses: whether a code appeared as a claim's primary diagnosis, ever or in a window: in the last "
        "N days, at least N days ago, or between two times (every claim date is kept). Secondary diagnoses "
        "aren't available, so a condition coded only as a secondary diagnosis won't be found.",
        "- Medications: whether a drug was filled, ever or in a window (every fill is kept with its date). "
        "Days of supply, quantity and dose are not stored, so rules about duration (\"on a stable dose for 3 "
        "months\"), new starts (\"started within 6 months\") or dose can't be checked from claims.",
        "- Nothing else: no lab results, vital signs, procedures, imaging, notes, consent or language.",
    ]
    return "\n".join(lines)

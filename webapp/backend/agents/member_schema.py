"""The member data the eligibility filters run against.

One place for table and column names: the SQL builder uses them, the criteria agent's prompt
describes them (names and meanings only; the agent never sees any member rows), and the
/data-schema page shows them, with DDL, so the data team knows what to load.
"""

MEMBERS = "members"
DIAGNOSES = "member_diagnoses"
PRESCRIPTIONS = "member_prescriptions"
NDC_LOOKUP = "ndc_lookup"  # reference table built from RxNorm: ndc -> ingredient_rxcui, atc_class
FILLS = "member_pharmacy_fills"  # preferred replacement for PRESCRIPTIONS; not used by the filters yet

# status: "required" = the filters query it today; "reference" = built from RxNorm, not member data;
# "preferred" = what we'd like instead, once the source data has it (nothing reads it yet)
TABLES = [
    {
        "name": MEMBERS,
        "status": "required",
        "grain": "one row per member",
        "key": ["member_id"],
        "columns": [("member_id", "VARCHAR", "member ID"),
                    ("birth_date", "DATE", "date of birth"),
                    ("sex", "VARCHAR", "MALE or FEMALE")],
        "used_for": "Step 1 (age and sex); every filter starts from this table.",
    },
    {
        "name": DIAGNOSES,
        "status": "required",
        "grain": "one row per diagnosis code per date it appears on a claim; every date is kept",
        "key": ["member_id", "code_system", "icd_code", "diagnosis_date"],
        "columns": [("member_id", "VARCHAR", "member ID"),
                    ("code_system", "VARCHAR", "ICD10CM (older claims may be ICD9CM)"),
                    ("icd_code", "VARCHAR", "diagnosis code without the dot, e.g. O2411 for O24.11"),
                    ("diagnosis_date", "DATE", "date of the claim the code appeared on")],
        "used_for": "Step 2 diagnosis rules: ever, in the last N days, "
                    "at least N days ago, or between two times.",
    },
    {
        "name": PRESCRIPTIONS,
        "status": "required",
        "grain": "one row per member and NDC, holding only the most recent fill",
        "key": ["member_id", "ndc"],
        "columns": [("member_id", "VARCHAR", "member ID"),
                    ("ndc", "VARCHAR", "11-digit NDC of the dispensed product, no dashes"),
                    ("last_fill_date", "DATE", "date of the most recent fill of that NDC")],
        "used_for": "Step 2 medication rules: filled, or not filled, in the last N days.",
    },
    {
        "name": NDC_LOOKUP,
        "status": "reference",
        "grain": "one row per NDC, ingredient and ATC class (combination products have several)",
        "key": ["ndc", "ingredient_rxcui", "atc_class"],
        "columns": [("ndc", "VARCHAR", "11-digit NDC, no dashes"),
                    ("ingredient_rxcui", "VARCHAR", "RxNorm ingredient RxCUI"),
                    ("atc_class", "VARCHAR", "ATC class code, e.g. A10BK for SGLT2 inhibitors")],
        "used_for": "Joins prescriptions to the drugs and classes the agent names. Built from RxNorm, "
                    "not from member data.",
    },
    {
        "name": FILLS,
        "status": "preferred",
        "grain": "one row per pharmacy claim (every fill kept)",
        "key": ["member_id", "ndc", "fill_date"],
        "columns": [("member_id", "VARCHAR", "member ID"),
                    ("ndc", "VARCHAR", "11-digit NDC of the dispensed product, no dashes"),
                    ("fill_date", "DATE", "date the prescription was filled"),
                    ("days_supply", "INTEGER", "days the fill is meant to last"),
                    ("quantity", "DECIMAL(12,3)", "units dispensed")],
        "optional": ["quantity"],
        "used_for": f"Would replace {PRESCRIPTIONS}: makes drug duration (\"stable dose for 3 months\") "
                    "and new-start (\"started within 6 months\") rules checkable. Not used yet.",
    },
]

REQUIRED = [t for t in TABLES if t["status"] == "required"]


def ddl() -> str:
    """CREATE TABLE statements for every table, in DuckDB / ANSI SQL."""
    blocks = []
    for t in TABLES:
        width = max(len(c) for c, _, _ in t["columns"])
        optional = t.get("optional", [])
        cols = [f"  {c.ljust(width)} {typ}{'' if c in optional else ' NOT NULL'},  -- {desc}"
                for c, typ, desc in t["columns"]]
        cols.append(f"  PRIMARY KEY ({', '.join(t['key'])})")
        blocks.append(f"-- {t['status']}: {t['grain']}\nCREATE TABLE {t['name']} (\n" + "\n".join(cols) + "\n);")
    return "\n\n".join(blocks)


def prompt_text() -> str:
    lines = []
    for t in REQUIRED:
        cols = ", ".join(f"{c} ({d})" for c, _, d in t["columns"])
        lines.append(f"- {t['name']}: {t['grain']}. Columns: {cols}.")
    lines += [
        "",
        "What these tables can and can't answer:",
        "- Diagnoses: whether a code ever appeared, or appeared in a window: in the last N days, at least N "
        "days ago, or between two times (every date is kept).",
        "- Medications: whether a drug was filled in the last N days, or not filled in the last N days, "
        "using the most recent fill only. The first fill, how long someone has taken a drug, the number of "
        "fills, dose and days of supply are not stored, so rules about duration (\"on a stable dose for 3 "
        "months\"), new starts (\"started within 6 months\") or dose can't be checked from claims.",
        "- Nothing else: no lab results, vital signs, procedures, imaging, notes, consent or language.",
    ]
    return "\n".join(lines)

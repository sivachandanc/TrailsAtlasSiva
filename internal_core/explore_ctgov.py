"""Fetch studies from ClinicalTrials.gov v2, flatten them, and store them in DuckDB.

Tables written:
  studies              one row per trial
  trial_sites          one row per site (location)
  trial_interventions  one row per intervention, as written by the sponsor
  trial_contacts       one row per central contact (public, but names real staff)
"""

import json
import tempfile
import time
from pathlib import Path

import duckdb
import requests

BASE_URL = "https://clinicaltrials.gov/api/v2/studies"
# Only trials with at least one site in this country (they may also have sites elsewhere)
COUNTRY = "United States"
# Only trials that can still enroll people
STATUSES = ["RECRUITING", "NOT_YET_RECRUITING"]
DB_PATH = str(Path(__file__).parent / "clinical_trials.duckdb")

# Reuse one HTTP connection across pages instead of a new TLS handshake per request.
session = requests.Session()

PROTOCOL = "protocolSection"
DERIVED = "derivedSection"

# studies columns. our_name: (API field name, path of keys from the top of the study JSON)
FIELD_PATHS = {
    "nct_id":                  ("NCTId",                 (PROTOCOL, "identificationModule", "nctId")),
    "brief_title":             ("BriefTitle",            (PROTOCOL, "identificationModule", "briefTitle")),
    "official_title":          ("OfficialTitle",         (PROTOCOL, "identificationModule", "officialTitle")),
    "overall_status":          ("OverallStatus",         (PROTOCOL, "statusModule", "overallStatus")),
    "last_update_post_date":   ("LastUpdatePostDate",    (PROTOCOL, "statusModule", "lastUpdatePostDateStruct", "date")),
    # Partial dates like "2027-05" are common here, so this stays text
    "primary_completion_date": ("PrimaryCompletionDate", (PROTOCOL, "statusModule", "primaryCompletionDateStruct", "date")),
    "lead_sponsor_class":      ("LeadSponsorClass",      (PROTOCOL, "sponsorCollaboratorsModule", "leadSponsor", "class")),
    "brief_summary":           ("BriefSummary",          (PROTOCOL, "descriptionModule", "briefSummary")),
    "detailed_description":    ("DetailedDescription",   (PROTOCOL, "descriptionModule", "detailedDescription")),
    "conditions":              ("Condition",             (PROTOCOL, "conditionsModule", "conditions")),
    "keywords":                ("Keyword",               (PROTOCOL, "conditionsModule", "keywords")),
    "study_type":              ("StudyType",             (PROTOCOL, "designModule", "studyType")),
    "phases":                  ("Phase",                 (PROTOCOL, "designModule", "phases")),
    "primary_purpose":         ("DesignPrimaryPurpose",  (PROTOCOL, "designModule", "designInfo", "primaryPurpose")),
    "enrollment_count":        ("EnrollmentCount",       (PROTOCOL, "designModule", "enrollmentInfo", "count")),
    "eligibility_criteria":    ("EligibilityCriteria",   (PROTOCOL, "eligibilityModule", "eligibilityCriteria")),
    # Observational trials often describe who they want here instead of in the criteria
    "study_population":        ("StudyPopulation",       (PROTOCOL, "eligibilityModule", "studyPopulation")),
    "healthy_volunteers":      ("HealthyVolunteers",     (PROTOCOL, "eligibilityModule", "healthyVolunteers")),
    "sex":                     ("Sex",                   (PROTOCOL, "eligibilityModule", "sex")),
    "gender_based":            ("GenderBased",           (PROTOCOL, "eligibilityModule", "genderBased")),
    "gender_description":      ("GenderDescription",     (PROTOCOL, "eligibilityModule", "genderDescription")),
    "minimum_age":             ("MinimumAge",            (PROTOCOL, "eligibilityModule", "minimumAge")),
    "maximum_age":             ("MaximumAge",            (PROTOCOL, "eligibilityModule", "maximumAge")),
    "std_ages":                ("StdAge",                (PROTOCOL, "eligibilityModule", "stdAges")),
    # meshes is a list of objects; the trailing "id" is taken from each one -> ["D003920", ...]
    "condition_mesh_ids":      ("ConditionMeshId",       (DERIVED, "conditionBrowseModule", "meshes", "id")),
    "condition_mesh_terms":    ("ConditionMeshTerm",     (DERIVED, "conditionBrowseModule", "meshes", "term")),
}

COLUMN_TYPES = {
    "nct_id": "VARCHAR PRIMARY KEY",
    "last_update_post_date": "DATE",
    "enrollment_count": "INTEGER",
    "healthy_volunteers": "BOOLEAN",
    "gender_based": "BOOLEAN",
    **dict.fromkeys(
        ["conditions", "keywords", "phases", "std_ages", "condition_mesh_ids", "condition_mesh_terms"],
        "VARCHAR[]",
    ),
}

# Lists inside a study that get their own table, one row per item.
# columns: our_name -> (path inside one item, DuckDB type)
CHILD_TABLES = {
    "trial_sites": {
        "api_fields": ["LocationFacility", "LocationStatus", "LocationCity", "LocationState",
                       "LocationZip", "LocationCountry", "LocationGeoPoint"],
        "items": (PROTOCOL, "contactsLocationsModule", "locations"),
        "columns": {
            "facility":    (("facility",), "VARCHAR"),
            "city":        (("city",), "VARCHAR"),
            "state":       (("state",), "VARCHAR"),
            "zip":         (("zip",), "VARCHAR"),
            "country":     (("country",), "VARCHAR"),
            "lat":         (("geoPoint", "lat"), "DOUBLE"),
            "lon":         (("geoPoint", "lon"), "DOUBLE"),
            # A site can be closed while the trial still recruits elsewhere
            "site_status": (("status",), "VARCHAR"),
        },
    },
    "trial_interventions": {
        "api_fields": ["InterventionType", "InterventionName", "InterventionOtherName",
                       "InterventionDescription", "InterventionArmGroupLabel"],
        "items": (PROTOCOL, "armsInterventionsModule", "interventions"),
        "columns": {
            "type":             (("type",), "VARCHAR"),
            "name":             (("name",), "VARCHAR"),
            "other_names":      (("otherNames",), "VARCHAR[]"),
            "description":      (("description",), "VARCHAR"),
            "arm_group_labels": (("armGroupLabels",), "VARCHAR[]"),
        },
    },
    "trial_contacts": {
        "api_fields": ["CentralContactName", "CentralContactRole", "CentralContactPhone",
                       "CentralContactPhoneExt", "CentralContactEMail"],
        "items": (PROTOCOL, "contactsLocationsModule", "centralContacts"),
        "columns": {
            "name":      (("name",), "VARCHAR"),
            "role":      (("role",), "VARCHAR"),
            "phone":     (("phone",), "VARCHAR"),
            "phone_ext": (("phoneExt",), "VARCHAR"),
            "email":     (("email",), "VARCHAR"),
        },
    },
}


def get_path(value, path: tuple[str, ...]):
    """Follow `path` into nested dicts; on a list, apply the rest of the path to each item."""
    for i, key in enumerate(path):
        if value is None:
            return None
        if isinstance(value, list):
            return [get_path(item, path[i:]) for item in value]
        value = value.get(key)
    return value


def flatten(study: dict) -> dict:
    return {name: get_path(study, path) for name, (_, path) in FIELD_PATHS.items()}


def extract_children(study: dict) -> dict[str, list[dict]]:
    """One row per site / intervention / contact, each carrying the trial's nct_id."""
    nct_id = get_path(study, FIELD_PATHS["nct_id"][1])
    children = {}
    for table, spec in CHILD_TABLES.items():
        items = get_path(study, spec["items"]) or []
        children[table] = [
            {"nct_id": nct_id, **{col: get_path(item, path) for col, (path, _) in spec["columns"].items()}}
            for item in items
        ]
    return children


def search_studies(condition: str, page_size: int = 1000, page_token: str | None = None) -> dict:
    api_fields = [api_field for api_field, _ in FIELD_PATHS.values()]
    api_fields += [f for spec in CHILD_TABLES.values() for f in spec["api_fields"]]
    params = {
        "query.cond": condition,
        "filter.overallStatus": ",".join(STATUSES),
        # Matches site countries only, unlike query.locn which also searches other location text
        "filter.advanced": f'AREA[LocationCountry]"{COUNTRY}"',
        "fields": "|".join(api_fields),
        "sort": "@relevance",
        "countTotal": "true",
        "pageSize": page_size,
    }
    if page_token:
        params["pageToken"] = page_token
    response = session.get(
        BASE_URL, params=params, headers={"accept": "application/json"}, timeout=60
    )
    response.raise_for_status()
    data = response.json()
    raw = data.get("studies", [])
    children = {table: [] for table in CHILD_TABLES}
    for study in raw:
        for table, rows in extract_children(study).items():
            children[table].extend(rows)
    return {
        "total": data.get("totalCount"),
        "next_page_token": data.get("nextPageToken"),
        "studies": [flatten(s) for s in raw],
        "children": children,
    }


def fetch_all_studies(condition: str, page_size: int = 1000) -> tuple[list[dict], dict[str, list[dict]]]:
    studies, children, page_token, total = [], {t: [] for t in CHILD_TABLES}, None, None
    while True:
        result = search_studies(condition, page_size, page_token)
        studies.extend(result["studies"])
        for table, rows in result["children"].items():
            children[table].extend(rows)
        total = total or result["total"]  # the API only returns the total on the first page
        print(f"  fetched {len(studies)} / {total}")
        page_token = result["next_page_token"]
        if not page_token:
            return studies, children


def _load_temp(con: duckdb.DuckDBPyConnection, name: str, rows: list[dict], types: dict[str, str]) -> None:
    """Bulk-load rows into a temp table via an NDJSON file: DuckDB's JSON reader takes ~0.1s
    here, vs ~10s for passing the rows as a query parameter and ~13s for executemany."""
    columns = ", ".join(f"'{c}': '{t}'" for c, t in types.items())
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / f"{name}.ndjson"
        with path.open("w") as f:
            for row in rows:
                f.write(json.dumps(row) + "\n")
        con.execute(
            f"CREATE TEMP TABLE {name} AS SELECT * FROM read_json(?, "
            f"format = 'newline_delimited', columns = {{{columns}}})",
            [str(path)],
        )


def save_to_duckdb(
    studies: list[dict], children: dict[str, list[dict]], condition: str, db_path: str = DB_PATH
) -> None:
    columns = list(FIELD_PATHS)
    types = {c: COLUMN_TYPES.get(c, "VARCHAR") for c in columns}
    column_defs = ",\n    ".join(f"{c} {t}" for c, t in types.items())
    create_sql = """
        CREATE TABLE {name} (
            {column_defs},
            search_condition VARCHAR,
            fetched_at TIMESTAMP DEFAULT current_timestamp
        )
    """
    col_list = ", ".join(columns)
    with duckdb.connect(db_path) as con:
        if not con.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name = 'studies'"
        ).fetchone():
            con.execute(create_sql.format(name="studies", column_defs=column_defs))

        _load_temp(con, "incoming", studies,
                   {c: t.removesuffix(" PRIMARY KEY") for c, t in types.items()})

        # Upsert by rebuilding the table (kept rows + new rows) and swapping it in.
        # INSERT OR REPLACE leaves the replaced rows' space unused, so the file
        # grew ~70 MB per re-run; dropping the old table lets DuckDB reuse it.
        con.execute("BEGIN")
        con.execute(create_sql.format(name="studies_new", column_defs=column_defs))
        # Keep rows from other searches only: a re-run fully replaces this condition's
        # rows, so trials the current filters no longer return (e.g. closed) drop out.
        # BY NAME matches columns by name, so an existing table that predates a newly
        # added field still copies over (the new column is NULL for kept rows).
        con.execute(
            "INSERT INTO studies_new BY NAME SELECT * FROM studies "
            "WHERE nct_id NOT IN (SELECT nct_id FROM incoming) "
            "AND search_condition IS DISTINCT FROM ?",
            [condition],
        )
        con.execute(
            f"INSERT INTO studies_new ({col_list}, search_condition) "
            f"SELECT {col_list}, ? FROM incoming",
            [condition],
        )
        con.execute("DROP TABLE studies")
        con.execute("ALTER TABLE studies_new RENAME TO studies")

        # Child tables: replace rows for the incoming trials, and drop rows whose
        # trial is no longer in studies.
        for table, spec in CHILD_TABLES.items():
            child_types = {"nct_id": "VARCHAR", **{c: t for c, (_, t) in spec["columns"].items()}}
            con.execute(
                f"CREATE TABLE IF NOT EXISTS {table} ("
                + ", ".join(f"{c} {t}" for c, t in child_types.items()) + ")"
            )
            con.execute(
                f"DELETE FROM {table} WHERE nct_id IN (SELECT nct_id FROM incoming) "
                f"OR nct_id NOT IN (SELECT nct_id FROM studies)"
            )
            _load_temp(con, f"incoming_{table}", children[table], child_types)
            con.execute(f"INSERT INTO {table} BY NAME SELECT * FROM incoming_{table}")

        con.execute("COMMIT")
        con.execute("CHECKPOINT")
        total = con.execute("SELECT count(*) FROM studies").fetchone()[0]
        child_counts = {t: con.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in CHILD_TABLES}
    print(f"Saved {len(studies)} studies to {db_path} (table now has {total} rows)")
    print("  " + ", ".join(f"{t}: {n} rows" for t, n in child_counts.items()))


if __name__ == "__main__":
    condition = "diabetes"
    print(f"Fetching {'/'.join(STATUSES)} studies for '{condition}' with a site in {COUNTRY}...")

    start = time.perf_counter()
    studies, children = fetch_all_studies(condition)
    fetched = time.perf_counter()
    save_to_duckdb(studies, children, condition)
    saved = time.perf_counter()

    print(f"\nFetch: {fetched - start:.2f}s | Save: {saved - fetched:.2f}s | "
          f"Total: {saved - start:.2f}s")

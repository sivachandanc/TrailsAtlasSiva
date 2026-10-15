"""Build a trial's ICD-10-CM prefix list for the preliminary cohort (the ICD prefilter).

Three sources from the trial's ClinicalTrials.gov record, combined with OR:
  1. condition_mesh_ids + condition_mesh_terms: CUIs matching both the MeSH ID and its term
  2. conditions (sponsor's free text): UMLS term search across all vocabularies
Every CUI is mapped to ICD-10-CM codes, which are cut to their 3-character category.

Usage: uv run python -m internal_core.trial_icd NCT03570632
"""

import sys

import duckdb

from internal_core.explore_ctgov import DB_PATH
from internal_core.get_icd_codes import cui_to_icd, find_cuis, mesh_to_cuis

# Symptom (R) and factor/administrative (Z) categories are catch-alls (e.g. Z79 covers all
# long-term drug use), so codes in those chapters are kept at full length instead.
FULL_LENGTH_CHAPTERS = ("R", "Z")

_icd_cache: dict[str, list[str]] = {}


def to_prefix(icd_code: str) -> str:
    """Dot-free, upper-case code cut to its 3-character category (R and Z codes kept whole)."""
    code = icd_code.replace(".", "").strip().upper()
    return code if code.startswith(FULL_LENGTH_CHAPTERS) else code[:3]


def icd_codes_for(cui: str) -> list[str]:
    if cui not in _icd_cache:
        _icd_cache[cui] = [icd["code"] for icd in cui_to_icd(cui, with_names=False)]
    return _icd_cache[cui]


def build_trial_codes(nct_id: str, con: duckdb.DuckDBPyConnection) -> tuple[list[dict], list[str]]:
    """Returns (one row per source -> CUI -> ICD code, list of inputs that matched nothing)."""
    row = con.execute(
        "SELECT condition_mesh_ids, condition_mesh_terms, conditions FROM studies WHERE nct_id = ?",
        [nct_id],
    ).fetchone()
    if row is None:
        raise SystemExit(f"{nct_id} is not in the studies table")
    mesh_ids, mesh_terms, conditions = (value or [] for value in row)

    rows, unmatched = [], []

    def add(source: str, source_value: str, match_type: str, cuis: dict[str, str]) -> None:
        if not cuis:
            unmatched.append(f"{source}: {source_value} (no CUI)")
        for cui, cui_name in cuis.items():
            codes = icd_codes_for(cui)
            if not codes:
                unmatched.append(f"{source}: {source_value} -> {cui} {cui_name} (no ICD-10-CM code)")
            for icd_code in codes:
                rows.append({
                    "nct_id": nct_id, "source": source, "source_value": source_value,
                    "match_type": match_type, "cui": cui, "cui_name": cui_name,
                    "icd_code": icd_code, "prefix": to_prefix(icd_code),
                })

    # Source 1: MeSH ID + term pairs (same position in the two lists)
    for mesh_id, mesh_term in zip(mesh_ids, mesh_terms, strict=True):
        cuis, match_type = mesh_to_cuis(mesh_id, mesh_term)
        add("mesh", f"{mesh_id} {mesh_term}", match_type, cuis)

    # Source 2: sponsor condition text. normalizedString tolerates case, plurals and word
    # order; "words" was too loose (it matched e.g. "No evidence of prenatal infection").
    for condition in conditions:
        cuis = find_cuis(condition, "atom", search_type="normalizedString", sabs=None)
        add("condition", condition, "normalizedString", cuis)

    return rows, unmatched


def save_trial_codes(nct_id: str, rows: list[dict], con: duckdb.DuckDBPyConnection) -> None:
    con.execute("""
        CREATE TABLE IF NOT EXISTS trial_icd_codes (
            nct_id VARCHAR, source VARCHAR, source_value VARCHAR, match_type VARCHAR,
            cui VARCHAR, cui_name VARCHAR, icd_code VARCHAR, prefix VARCHAR,
            created_at TIMESTAMP DEFAULT current_timestamp
        )
    """)
    con.execute("BEGIN")
    con.execute("DELETE FROM trial_icd_codes WHERE nct_id = ?", [nct_id])  # re-run replaces
    if rows:
        cols = list(rows[0])
        con.executemany(
            f"INSERT INTO trial_icd_codes ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
            [[r[c] for c in cols] for r in rows],
        )
    con.execute("COMMIT")


# Preliminary cohort: members with at least one ICD-10-CM code starting with any of the
# trial's prefixes. Joining on the stored prefixes is the OR filter, without building a
# long "LIKE ... OR LIKE ..." string. Assumes member_diagnoses stores dot-free codes.
COHORT_SQL = """
SELECT DISTINCT d.member_id
FROM member_diagnoses d
JOIN (SELECT DISTINCT prefix FROM trial_icd_codes WHERE nct_id = $nct_id) p
  ON d.icd_code LIKE p.prefix || '%'
WHERE d.code_system = 'ICD10CM'
"""


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    nct_id = sys.argv[1].upper()

    with duckdb.connect(DB_PATH) as con:
        rows, unmatched = build_trial_codes(nct_id, con)
        save_trial_codes(nct_id, rows, con)
        summary = con.execute("""
            SELECT prefix,
                   list(DISTINCT icd_code ORDER BY icd_code) AS icd_codes,
                   list(DISTINCT source ORDER BY source)     AS sources
            FROM trial_icd_codes WHERE nct_id = ?
            GROUP BY prefix ORDER BY prefix
        """, [nct_id]).fetchall()

    print(f"{nct_id}: {len(rows)} source->ICD rows, {len(summary)} prefixes in the OR filter\n")
    for prefix, icd_codes, sources in summary:
        print(f"  {prefix + '%':9} from {', '.join(sources):16} codes: {', '.join(icd_codes)}")
    if not summary:
        print("  No ICD codes found: skip the ICD prefilter for this trial.")
    if unmatched:
        print("\nNo ICD code from:")
        for item in unmatched:
            print(f"  - {item}")
    print(f"\nSaved to trial_icd_codes in {DB_PATH}")

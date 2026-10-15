import os

import requests
from dotenv import load_dotenv
load_dotenv()

API_KEY = os.environ["UMLS_API_KEY"]  # export UMLS_API_KEY=... (never commit the key)
BASE_URL = "https://uts-ws.nlm.nih.gov/rest"
SEARCH_URL = f"{BASE_URL}/search/current"
ICD_SOURCE = "ICD10CM"  # US clinical ICD-10; use "ICD10" for the WHO version

mesh_codes = ["D011254", "D006973", "D059466", "D011225"]  # Example MeSH codes
mesh_terms = ["Pregnancy in Diabetics", "Hypertension", "White Coat Hypertension", "Pre-Eclampsia"]

session = requests.Session()


def find_cuis(
    string: str, input_type: str, search_type: str = "exact", sabs: str | None = "MSH"
) -> dict[str, str]:
    """CUIs ({cui: name}) for a MeSH code (input_type="sourceUi") or term ("atom").
    sabs limits matches to one vocabulary (default MeSH); None searches all of UMLS."""
    params = {
        "apiKey": API_KEY,
        "string": string,
        "inputType": input_type,
        "searchType": search_type,
        "returnIdType": "concept",  # return UMLS CUIs
    }
    if sabs:
        params["sabs"] = sabs
    response = session.get(SEARCH_URL, params=params, timeout=30)
    response.raise_for_status()
    data = response.json()

    # UMLS returns ui "NONE" when nothing matches
    results = data.get("result", {}).get("results", [])
    return {r["ui"]: r["name"] for r in results if r.get("ui") != "NONE"}


def mesh_to_cuis(code: str, term: str) -> tuple[dict[str, str], str]:
    """CUIs matching both the MeSH code and term (set intersection), plus how they matched.
    Falls back to the code's CUIs when the term has no exact match, since the code is authoritative."""
    by_code = find_cuis(code, "sourceUi")
    by_term = find_cuis(term, "atom")
    both = {cui: name for cui, name in by_code.items() if cui in by_term}
    if both:
        return both, "code+term"
    return by_code, "code only"


def cui_to_icd(cui: str, with_names: bool = True) -> list[dict]:
    """ICD codes attached to a UMLS concept (its atoms from the ICD source vocabulary).
    with_names=False skips the extra per-code name lookup."""
    url = f"{BASE_URL}/content/current/CUI/{cui}/atoms"
    params = {"apiKey": API_KEY, "sabs": ICD_SOURCE, "pageSize": 100}
    response = session.get(url, params=params, timeout=30)
    if response.status_code == 404:  # concept has no atoms in that vocabulary
        return []
    response.raise_for_status()

    # atom["code"] is a URL like .../source/ICD10CM/E11.9 -> the code is the last segment
    codes = dict.fromkeys(atom["code"].rsplit("/", 1)[-1] for atom in response.json().get("result", []))
    return [{"code": code, "name": icd_name(code) if with_names else None} for code in codes]


_icd_names: dict[str, str] = {}


def icd_name(code: str) -> str:
    """Official ICD name for a code. The atom attached to a CUI can be a synonym
    (e.g. I10 shows up as "high blood pressure"), so look the code up directly."""
    if code not in _icd_names:
        url = f"{BASE_URL}/content/current/source/{ICD_SOURCE}/{code}"
        response = session.get(url, params={"apiKey": API_KEY}, timeout=30)
        response.raise_for_status()
        _icd_names[code] = response.json()["result"]["name"]
    return _icd_names[code]


if __name__ == "__main__":
    for code, term in zip(mesh_codes, mesh_terms, strict=True):
        cuis, match_type = mesh_to_cuis(code, term)
        if not cuis:
            print(f"{code} / {term}: no CUI match")
        for cui, name in cuis.items():
            print(f"{code} / {term} -> CUI: {cui}, Name: {name} ({match_type})")
            icd_codes = cui_to_icd(cui)
            if not icd_codes:
                print(f"    {ICD_SOURCE}: no direct code for this concept")
            for icd in icd_codes:
                print(f"    {ICD_SOURCE}: {icd['code']}  {icd['name']}")

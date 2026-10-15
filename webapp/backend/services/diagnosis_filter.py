"""Step 1 of the eligibility filter builder: diagnoses (ICD-10-CM).

Ported from the sandbox (internal_core/trial_icd.py). Two inputs from the trial, combined with OR:
  1. MeSH conditions: CUIs that both the MeSH ID and its term lead to
  2. The sponsor's condition text: UMLS term search across all vocabularies
Every concept -> ICD-10-CM codes -> dot-free, first 3 characters (R and Z kept whole).
Every decision is recorded so the page can show how each prefix was reached.
"""

import asyncio

from webapp.backend.models import (
    ConditionTrace, CuiRef, CuiTrace, DiagnosisFilter, IcdCode, MeshTrace, PrefixRow, Rule, TrialDetail,
)

# Symptom (R) and factor/administrative (Z) categories are catch-alls (e.g. Z79 covers all
# long-term drug use), so codes in those chapters are kept at full length instead.
FULL_LENGTH_CHAPTERS = ("R", "Z")

RULES = [
    Rule(title="MeSH ID and term must agree",
         text="Each MeSH condition is looked up twice in UMLS: by its ID and by its name. A concept is kept "
              "when both lookups return it, which filters out unrelated concepts that share the ID. If the "
              "name finds nothing in common, the ID's concepts are kept, because the ID is the more exact of the two."),
    Rule(title="Condition text is matched loosely, but not too loosely",
         text="The sponsor's own condition text is matched against every UMLS vocabulary, ignoring case, "
              "plurals and word order. Looser word-by-word matching was tried and pulled in unrelated "
              "concepts, so text that doesn't match closely adds nothing rather than a guess."),
    Rule(title="Codes are cut to their 3-character category",
         text="A mapped code like O24.91 becomes O24, so members with any code in that category pass "
              "(O24.0, O24.4, O24.911 and so on). Concept mappings are often more specific than the codes "
              "real patients carry, and cutting to the category avoids missing them."),
    Rule(title="R and Z codes stay whole",
         text="R (symptoms) and Z (circumstances such as drug use or pregnancy status) categories group "
              "unrelated things. Z79 covers every kind of long-term medication, so these codes keep their "
              "full length, for example R73.03 stays R73.03."),
    Rule(title="Any one prefix is enough",
         text="The prefixes are combined with OR: a member passes with at least one diagnosis code starting "
              "with any of them."),
    Rule(title="This is a wide net",
         text="A trial's conditions describe what it studies, not exactly who can join. This step keeps "
              "everyone with a related diagnosis; later steps narrow it down."),
]


def to_prefix(code: str) -> str:
    """Dot-free, upper-case code cut to its 3-character category (R and Z codes kept whole)."""
    code = code.replace(".", "").strip().upper()
    return code if code.startswith(FULL_LENGTH_CHAPTERS) else code[:3]


def with_dot(prefix: str) -> str:
    """'R7303' -> 'R73.03'; 'O24' stays."""
    return f"{prefix[:3]}.{prefix[3:]}" if len(prefix) > 3 else prefix


def plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def cui_list(cuis: list[CuiRef]) -> str:
    return ", ".join(f"{c.cui} ({c.name})" if c.name else c.cui for c in cuis)


async def build_diagnosis_filter(trial: TrialDetail, umls) -> DiagnosisFilter:
    warnings: list[str] = []
    mesh_ids, mesh_terms = trial.mesh_ids, trial.mesh_terms
    if len(mesh_ids) != len(mesh_terms):
        warnings.append("ClinicalTrials.gov returned a different number of MeSH IDs and terms, "
                        "so only the matching pairs were used.")
    pairs = list(zip(mesh_ids, mesh_terms))

    # 1. Look up every input at once
    mesh_results = await asyncio.gather(*(umls.mesh_lookup(i, t) for i, t in pairs))
    condition_results = await asyncio.gather(*(umls.condition_lookup(c) for c in trial.conditions))

    # 2. ICD-10-CM codes for every concept we keep, then their names and their categories' names
    cuis = list(dict.fromkeys([c.cui for r in mesh_results for c in r["kept"]] +
                              [c.cui for r in condition_results for c in r]))
    codes_by_cui = dict(zip(cuis, await asyncio.gather(*(umls.icd_codes(c) for c in cuis))))
    all_codes = list(dict.fromkeys(code for codes in codes_by_cui.values() for code in codes))
    prefixes = list(dict.fromkeys(to_prefix(c) for c in all_codes))
    names = dict(zip(all_codes + [with_dot(p) for p in prefixes],
                     await asyncio.gather(*(umls.icd_name(c) for c in all_codes + [with_dot(p) for p in prefixes]))))

    def trace(c: CuiRef) -> CuiTrace:
        codes = codes_by_cui.get(c.cui, [])
        icd = [IcdCode(code=code, name=names.get(code), prefix=to_prefix(code)) for code in codes]
        why = (f"UMLS links {plural(len(codes), 'ICD-10-CM code')} to this concept." if codes
               else "UMLS links no ICD-10-CM code to this concept, so it adds nothing.")
        return CuiTrace(cui=c.cui, name=c.name, icd_codes=icd, why=why)

    # 3. Traces, one per input
    contributors: dict[str, list[str]] = {}

    def credit(label: str, t: CuiTrace):
        for code in t.icd_codes:
            contributors.setdefault(code.prefix, [])
            if label not in contributors[code.prefix]:
                contributors[code.prefix].append(label)

    mesh_traces = []
    for (mesh_id, term), r in zip(pairs, mesh_results):
        kept_ids = {c.cui for c in r["kept"]}
        dropped = [c for c in r["by_id"] if c.cui not in kept_ids]
        if r["match"] == "id+term":
            why = f"Both lookups returned {cui_list(r['kept'])}, so {'it was' if len(r['kept']) == 1 else 'they were'} kept."
            if dropped:
                why += f" Dropped {cui_list(dropped)}: the ID found {'it' if len(dropped) == 1 else 'them'}, but the name didn't."
        elif r["match"] == "id only":
            why = ("The name had no exact match in common with the ID, so the ID's "
                   f"{plural(len(r['kept']), 'concept')} {'was' if len(r['kept']) == 1 else 'were'} kept.")
        else:
            why = "Neither the ID nor the name found a UMLS concept, so this condition adds no ICD codes."
        kept = [trace(c) for c in r["kept"]]
        label = f"MeSH {mesh_id} {term}"
        for t in kept:
            credit(label, t)
        mesh_traces.append(MeshTrace(mesh_id=mesh_id, term=term, by_id=r["by_id"], by_term=r["by_term"],
                                     match=r["match"], why=why, kept=kept, dropped=dropped))

    condition_traces = []
    for text, found in zip(trial.conditions, condition_results):
        traces = [trace(c) for c in found]
        if not found:
            why = "No UMLS concept matches this text closely (ignoring case, plurals and word order), so it adds nothing."
        elif any(t.icd_codes for t in traces):
            why = f"Matched {cui_list(found)}."
        else:
            why = f"Matched {cui_list(found)}, but UMLS links no ICD-10-CM code to {'it' if len(found) == 1 else 'them'}."
        for t in traces:
            credit(f"Condition text “{text}”", t)
        condition_traces.append(ConditionTrace(text=text, why=why, found=traces))

    # 4. Final prefixes
    rows = []
    for p in prefixes:
        codes = [c for c in all_codes if to_prefix(c) == p]
        full = p.startswith(FULL_LENGTH_CHAPTERS)
        why = (f"{'An' if p[0] == 'R' else 'A'} {p[0]} code, so it's kept whole: only {with_dot(p)} and codes under it match." if full
               else f"Cut to the category {p}, so any {p} code matches"
                    + (f", not only {', '.join(codes)}." if codes != [p] else "."))
        rows.append(PrefixRow(prefix=p, display=with_dot(p), category_name=names.get(with_dot(p)),
                              icd_codes=codes, contributors=contributors.get(p, []), full_length=full, why=why))
    rows.sort(key=lambda r: r.prefix)

    skipped = not rows
    if skipped:
        summary = ("None of the trial's conditions led to an ICD-10-CM code, so this step is skipped: "
                   "it doesn't remove any members.")
        sql = "SELECT member_id\nFROM members  -- no ICD codes for this trial: everyone passes this step"
    else:
        summary = (f"Members pass if they have at least one ICD-10-CM diagnosis starting with "
                   f"{' or '.join(r.display for r in rows)}.")
        likes = "\n    OR ".join(f"icd_code LIKE '{r.prefix}%'" for r in rows)
        sql = ("SELECT DISTINCT member_id\nFROM member_diagnoses\n"
               f"WHERE code_system = 'ICD10CM'\n  AND (\n    {likes}\n  )")

    definition = {
        "step": "diagnoses",
        "trial_id": trial.id,
        "code_system": "ICD10CM",
        "match": "any prefix (OR)",
        "prefixes": [r.prefix for r in rows],
        "sources": {
            "mesh": [{"mesh_id": t.mesh_id, "term": t.term, "kept_cuis": [k.cui for k in t.kept]} for t in mesh_traces],
            "condition_text": [{"text": t.text, "cuis": [f.cui for f in t.found]} for t in condition_traces],
        },
    }
    return DiagnosisFilter(
        trial_id=trial.id, title=trial.title, status=trial.status, skipped=skipped, summary=summary,
        mesh=mesh_traces, conditions=condition_traces, prefixes=rows, rules=RULES, warnings=warnings,
        sql=sql, definition=definition,
    )

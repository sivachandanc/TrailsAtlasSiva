import pytest

from webapp.backend.models import CuiRef, TrialDetail
from webapp.backend.services.diagnosis_filter import build_diagnosis_filter, to_prefix, with_dot

pytestmark = pytest.mark.anyio


class FakeUmls:
    """Answers like UMLS for a handful of made-up inputs."""

    MESH = {
        # (by ID, by term)
        "D1": (["C1", "C9"], ["C1"]),      # ID and term agree on C1; C9 dropped
        "D2": (["C2"], []),                # term finds nothing: keep the ID's concept
        "D3": ([], []),                    # nothing at all
        "D4": (["C4"], ["C4"]),            # concept with no ICD code
        "D5": (["C5"], ["C5"]),            # R code: kept whole
    }
    TEXT = {"Gestational Diabetes": ["C6"], "Type2diabetes": [], "High-Risk Pregnancy": ["C4"]}
    ICD = {"C1": ["O24.91", "O24.919"], "C2": ["E11"], "C4": [], "C5": ["R73.03"], "C6": ["O24.4"], "C9": ["Z99.9"]}

    def __init__(self):
        self.calls = []

    def ref(self, cui):
        return CuiRef(cui=cui, name=f"Concept {cui}")

    async def mesh_lookup(self, mesh_id, term):
        by_id, by_term = self.MESH[mesh_id]
        both = [c for c in by_id if c in by_term]
        kept, match = (both, "id+term") if both else (by_id, "id only") if by_id else ([], "none")
        return {"by_id": [self.ref(c) for c in by_id], "by_term": [self.ref(c) for c in by_term],
                "kept": [self.ref(c) for c in kept], "match": match}

    async def condition_lookup(self, text):
        return [self.ref(c) for c in self.TEXT[text]]

    async def icd_codes(self, cui):
        self.calls.append(cui)
        return self.ICD[cui]

    async def icd_name(self, code):
        return f"Name of {code}"


def trial(mesh=(), terms=None, conditions=()):
    mesh = list(mesh)
    return TrialDetail(id="NCT00000001", status="RECRUITING", mesh_ids=mesh,
                       mesh_terms=terms if terms is not None else [f"Term {m}" for m in mesh], conditions=list(conditions))


def test_prefix_rules():
    assert to_prefix("O24.91") == "O24" and to_prefix("e11") == "E11"
    assert to_prefix("R73.03") == "R7303" and to_prefix("Z79.4") == "Z794"
    assert with_dot("R7303") == "R73.03" and with_dot("O24") == "O24"


async def test_id_and_term_agree_and_dropped_concepts_are_listed():
    d = await build_diagnosis_filter(trial(["D1"]), FakeUmls())
    (m,) = d.mesh
    assert m.match == "id+term" and [k.cui for k in m.kept] == ["C1"] and [x.cui for x in m.dropped] == ["C9"]
    assert "Dropped C9" in m.why
    assert [(c.code, c.prefix) for c in m.kept[0].icd_codes] == [("O24.91", "O24"), ("O24.919", "O24")]
    assert [p.prefix for p in d.prefixes] == ["O24"]  # the dropped concept's Z99.9 never appears


async def test_term_miss_keeps_the_id_and_nothing_found():
    d = await build_diagnosis_filter(trial(["D2", "D3"]), FakeUmls())
    by_id = {m.mesh_id: m for m in d.mesh}
    assert by_id["D2"].match == "id only" and "ID's" in by_id["D2"].why
    assert by_id["D3"].match == "none" and by_id["D3"].kept == []
    assert [p.prefix for p in d.prefixes] == ["E11"]


async def test_concept_without_icd_and_r_code():
    d = await build_diagnosis_filter(trial(["D4", "D5"]), FakeUmls())
    no_icd = d.mesh[0].kept[0]
    assert no_icd.icd_codes == [] and "no ICD-10-CM code" in no_icd.why
    (row,) = d.prefixes
    assert row.prefix == "R7303" and row.display == "R73.03" and row.full_length and "kept whole" in row.why


async def test_condition_text_traces():
    d = await build_diagnosis_filter(trial(conditions=["Gestational Diabetes", "Type2diabetes", "High-Risk Pregnancy"]), FakeUmls())
    by_text = {c.text: c for c in d.conditions}
    assert by_text["Gestational Diabetes"].found[0].icd_codes[0].code == "O24.4"
    assert by_text["Type2diabetes"].found == [] and "No UMLS concept" in by_text["Type2diabetes"].why
    assert "links no ICD-10-CM code" in by_text["High-Risk Pregnancy"].why


async def test_prefixes_merge_contributors_and_build_sql():
    d = await build_diagnosis_filter(trial(["D1", "D2"], conditions=["Gestational Diabetes"]), FakeUmls())
    rows = {p.prefix: p for p in d.prefixes}
    assert set(rows) == {"E11", "O24"}
    assert rows["O24"].icd_codes == ["O24.91", "O24.919", "O24.4"]
    assert rows["O24"].contributors == ["MeSH D1 Term D1", "Condition text “Gestational Diabetes”"]
    assert rows["O24"].category_name == "Name of O24"
    assert "icd_code LIKE 'E11%'" in d.sql and "icd_code LIKE 'O24%'" in d.sql and "code_system = 'ICD10CM'" in d.sql
    assert d.definition["prefixes"] == ["E11", "O24"] and not d.skipped


async def test_skipped_when_nothing_maps():
    d = await build_diagnosis_filter(trial(["D3"], conditions=["Type2diabetes"]), FakeUmls())
    assert d.skipped and d.prefixes == [] and "skipped" in d.summary and "everyone passes" in d.sql


async def test_each_concept_is_looked_up_once():
    umls = FakeUmls()
    await build_diagnosis_filter(trial(["D1"], conditions=["Gestational Diabetes"]), umls)
    assert sorted(umls.calls) == ["C1", "C6"]


async def test_mismatched_mesh_lists_warn():
    d = await build_diagnosis_filter(trial(["D1", "D2"], terms=["Term D1"]), FakeUmls())
    assert len(d.mesh) == 1 and d.warnings

import httpx
import pytest

from webapp.backend.terminology.umls import UmlsClient, UmlsError, UmlsNotConfigured

pytestmark = pytest.mark.anyio


def client_for(handler, key="test-key"):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), key


def search_body(*cuis):
    return {"result": {"results": [{"ui": c, "name": f"Name {c}"} for c in cuis] or [{"ui": "NONE", "name": "NO RESULTS"}]}}


async def test_mesh_lookup_sends_both_searches_and_intersects():
    seen = []

    def handler(request):
        seen.append(dict(request.url.params))
        p = request.url.params
        return httpx.Response(200, json=search_body("C1", "C9") if p["inputType"] == "sourceUi" else search_body("C1"))

    http, key = client_for(handler)
    async with http:
        r = await UmlsClient(http, key).mesh_lookup("D003922", "Diabetes Mellitus, Type 1")
    assert [c.cui for c in r["kept"]] == ["C1"] and r["match"] == "id+term"
    by_type = {p["inputType"]: p for p in seen}
    assert by_type["sourceUi"]["string"] == "D003922" and by_type["sourceUi"]["sabs"] == "MSH"
    assert by_type["atom"]["searchType"] == "exact" and by_type["atom"]["apiKey"] == "test-key"


async def test_condition_lookup_uses_normalized_string_across_vocabularies():
    seen = []

    def handler(request):
        seen.append(dict(request.url.params))
        return httpx.Response(200, json=search_body())

    http, key = client_for(handler)
    async with http:
        assert await UmlsClient(http, key).condition_lookup("Type2diabetes") == []  # "NONE" is ignored
    assert seen[0]["searchType"] == "normalizedString" and "sabs" not in seen[0]


async def test_icd_codes_from_atoms_and_404():
    def handler(request):
        if "C404" in request.url.path:
            return httpx.Response(404)
        return httpx.Response(200, json={"result": [
            {"code": "https://uts-ws.nlm.nih.gov/rest/content/2025AA/source/ICD10CM/O24.91"},
            {"code": "https://uts-ws.nlm.nih.gov/rest/content/2025AA/source/ICD10CM/O24.91"},
            {"code": "https://uts-ws.nlm.nih.gov/rest/content/2025AA/source/ICD10CM/O24.919"},
        ]})

    http, key = client_for(handler)
    async with http:
        umls = UmlsClient(http, key)
        assert await umls.icd_codes("C0032969") == ["O24.91", "O24.919"]
        assert await umls.icd_codes("C404") == []


async def test_results_are_cached():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(200, json={"result": {"name": "Type 2 diabetes mellitus"}})

    http, key = client_for(handler)
    async with http:
        umls = UmlsClient(http, key)
        assert await umls.icd_name("E11") == "Type 2 diabetes mellitus"
        await umls.icd_name("E11")
    assert len(calls) == 1


async def test_missing_key_and_rejected_key():
    http, _ = client_for(lambda r: httpx.Response(401))
    async with http:
        with pytest.raises(UmlsNotConfigured):
            await UmlsClient(http, None).icd_codes("C1")
        with pytest.raises(UmlsError, match="rejected the API key"):
            await UmlsClient(http, "bad").icd_codes("C1")

import httpx
import pytest

from webapp.backend.terminology.umls import UmlsClient, UmlsError, UmlsNotConfigured

pytestmark = pytest.mark.anyio


def client_for(handler, key="test-key"):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), key


def search_body(*codes):
    return {"result": {"results": [{"ui": c, "name": f"Name {c}"} for c in codes] or [{"ui": "NONE", "name": "NO RESULTS"}]}}


async def test_search_icd10cm_uses_words_search_in_icd10cm():
    seen = []

    def handler(request):
        seen.append(dict(request.url.params))
        return httpx.Response(200, json=search_body("O24.11") if request.url.params["string"] == "diabetes" else search_body())

    http, key = client_for(handler)
    async with http:
        umls = UmlsClient(http, key)
        assert await umls.search_icd10cm("diabetes") == [{"code": "O24.11", "name": "Name O24.11"}]
        assert await umls.search_icd10cm("nothing") == []  # "NONE" is ignored
    assert seen[0]["sabs"] == "ICD10CM" and seen[0]["searchType"] == "words" and seen[0]["returnIdType"] == "code"


async def test_results_are_cached():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(200, json={"result": {"ui": "E11", "name": "Type 2 diabetes mellitus", "children": "NONE"}})

    http, key = client_for(handler)
    async with http:
        umls = UmlsClient(http, key)
        assert await umls.icd10cm_code("E11") == {"code": "E11", "name": "Type 2 diabetes mellitus", "has_children": False}
        await umls.icd10cm_code("e11")
    assert len(calls) == 1


async def test_missing_key_and_rejected_key():
    http, _ = client_for(lambda r: httpx.Response(401))
    async with http:
        with pytest.raises(UmlsNotConfigured):
            await UmlsClient(http, None).icd10cm_code("E11")
        with pytest.raises(UmlsError, match="rejected the API key"):
            await UmlsClient(http, "bad").icd10cm_code("E11")

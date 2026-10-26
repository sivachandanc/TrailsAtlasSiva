import httpx
import pytest

from webapp.backend.terminology.rxnorm import RxNormClient

pytestmark = pytest.mark.anyio

PRODUCTS = {"relatedGroup": {"conceptGroup": [
    {"tty": "SBD", "conceptProperties": [{"rxcui": "P1", "name": "empagliflozin 10 MG [Jardiance]"}]},
    {"tty": "SCD", "conceptProperties": [{"rxcui": "P2", "name": "empagliflozin 25 MG"}]},
]}}
BRANDS = {"relatedGroup": {"conceptGroup": [
    {"tty": "BN", "conceptProperties": [{"rxcui": "B1", "name": "Synjardy"}, {"rxcui": "B2", "name": "Jardiance"}]},
]}}
CURRENT = {"P1": ["00597015207"], "P2": []}
HISTORICAL = {"P1": [["00597015207"], ["00597015230"]], "P2": [["00597015299"]]}  # P2's NDC is discontinued


def handler(request):
    path, params = request.url.path, request.url.params
    if path.endswith("/related.json"):
        return httpx.Response(200, json=BRANDS if params["tty"] == "BN" else PRODUCTS)
    product = path.split("/")[-2]
    if path.endswith("/allhistoricalndcs.json"):
        times = [{"ndcTime": [{"ndc": n, "startDate": "201409", "endDate": "202001"} for n in HISTORICAL[product]]}]
        return httpx.Response(200, json={"historicalNdcConcept": {"historicalNdcTime": times}})
    if path.endswith("/ndcs.json"):
        return httpx.Response(200, json={"ndcGroup": {"ndcList": {"ndc": CURRENT[product]}}})
    return httpx.Response(404)


async def test_ndcs_include_current_and_discontinued_ndcs_of_every_product():
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        rx = RxNormClient(http)
        assert await rx.products_for_ingredient("1545653") == ["P1", "P2"]
        assert await rx.historical_ndcs("P2") == ["00597015299"]
        assert await rx.ndcs_for_ingredient("1545653") == ["00597015207", "00597015230", "00597015299"]


async def test_brand_names_are_sorted_and_unique():
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        assert await RxNormClient(http).brand_names("1545653") == ["Jardiance", "Synjardy"]


async def test_a_product_with_no_history_has_no_historical_ndcs():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={}))) as http:
        assert await RxNormClient(http).historical_ndcs("P9") == []

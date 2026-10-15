import json
from pathlib import Path

import httpx
import pytest

from webapp.backend import config
from webapp.backend.sources.base import SourceError
from webapp.backend.sources.clinicaltrials_gov import ClinicalTrialsGovSource, get_path, to_trial

PAGE = json.loads((Path(__file__).parent / "fixtures" / "ctgov_page.json").read_text())


def test_get_path_follows_lists():
    study = {"a": {"items": [{"id": "x"}, {"id": "y"}]}}
    assert get_path(study, ("a", "items", "id")) == ["x", "y"]
    assert get_path(study, ("a", "missing", "id")) is None


def test_maps_every_study_in_a_real_page():
    trials = [to_trial(s) for s in PAGE["studies"]]
    assert len(trials) == 5
    for t in trials:
        assert t.id.startswith("NCT")
        assert t.status in config.OPEN_STATUSES
        assert t.url == f"https://clinicaltrials.gov/study/{t.id}"
        assert any(s.country == "United States" for s in t.sites)  # the fetch requires a US site
        assert t.intervention_types == sorted(set(t.intervention_types))


def test_missing_fields_get_defaults():
    t = to_trial({"protocolSection": {"identificationModule": {"nctId": "NCT00000001"}}})
    assert t.phases == [] and t.sites == [] and t.enrollment is None and t.mesh_terms == []


def page(studies, total=None, token=None):
    body = {"studies": studies}
    if total is not None:
        body["totalCount"] = total
    if token:
        body["nextPageToken"] = token
    return body


def study(i):
    return {"protocolSection": {"identificationModule": {"nctId": f"NCT{i:08d}"},
                                "statusModule": {"overallStatus": "RECRUITING"}}}


@pytest.mark.anyio
async def test_search_follows_pages_and_sends_filters():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(dict(request.url.params))
        if "pageToken" not in request.url.params:
            return httpx.Response(200, json=page([study(1), study(2)], total=3, token="next"))
        return httpx.Response(200, json=page([study(3)]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        trials, total = await ClinicalTrialsGovSource(client).search("diabetes", "United States")

    assert [t.id for t in trials] == ["NCT00000001", "NCT00000002", "NCT00000003"] and total == 3
    first = seen[0]
    assert first["query.cond"] == "diabetes"
    assert first["filter.overallStatus"] == "RECRUITING,NOT_YET_RECRUITING"
    assert first["filter.advanced"] == 'AREA[LocationCountry]"United States"'
    assert seen[1]["pageToken"] == "next"


@pytest.mark.anyio
async def test_search_stops_at_the_cap(monkeypatch):
    monkeypatch.setattr(config, "MAX_TRIALS", 2)

    def handler(request):
        return httpx.Response(200, json=page([study(1), study(2), study(3)], total=50, token="more"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        trials, total = await ClinicalTrialsGovSource(client).search("x", "United States")
    assert len(trials) == 2 and total == 50


@pytest.mark.anyio
async def test_http_errors_become_source_errors():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(503))) as client:
        with pytest.raises(SourceError):
            await ClinicalTrialsGovSource(client).search("diabetes", "United States")

import json
from pathlib import Path

import httpx
import pytest

from webapp.backend.sources.base import SourceError, TrialNotFound
from webapp.backend.sources.clinicaltrials_gov import ClinicalTrialsGovSource, to_trial_detail

STUDY = json.loads((Path(__file__).parent / "fixtures" / "ctgov_study.json").read_text())


def test_maps_a_real_study():
    t = to_trial_detail(STUDY)
    assert t.id == "NCT06143566" and t.status == "RECRUITING"
    assert t.sponsor_name == "University of Texas Southwestern Medical Center"
    assert [a.type for a in t.arms] == ["EXPERIMENTAL", "ACTIVE_COMPARATOR"]
    assert t.arms[0].interventions == ["Drug: Polypill"]
    assert t.interventions[0].name == "Polypill" and t.interventions[0].other_names
    assert t.primary_outcomes[0].measure.startswith("Oxygen uptake") and t.primary_outcomes[0].time_frame
    assert t.contacts and t.officials[0].role == "PRINCIPAL_INVESTIGATOR"
    assert t.completion_date == "2027-12-01" and t.first_posted == "2023-11-22"
    assert t.eligibility_criteria.startswith("Inclusion Criteria")
    assert t.sites and t.sites[0].country == "United States"


@pytest.mark.anyio
@pytest.mark.parametrize("status, error", [(404, TrialNotFound), (400, TrialNotFound), (503, SourceError)])
async def test_get_trial_errors(status, error):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(status))) as client:
        with pytest.raises(error):
            await ClinicalTrialsGovSource(client).get_trial("NCT00000001")


@pytest.mark.anyio
async def test_get_trial_requests_fields_and_maps():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=STUDY)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        t = await ClinicalTrialsGovSource(client).get_trial("NCT06143566")
    assert t.id == "NCT06143566"
    assert seen[0].url.path.endswith("/studies/NCT06143566")
    assert "EligibilityCriteria" in seen[0].url.params["fields"]


@pytest.mark.anyio
async def test_count_open():
    def handler(request):
        assert request.url.params["filter.overallStatus"] == "RECRUITING,NOT_YET_RECRUITING"
        return httpx.Response(200, json={"totalCount": 25266, "studies": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await ClinicalTrialsGovSource(client).count_open("United States") == 25266

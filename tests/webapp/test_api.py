import pytest
from fastapi.testclient import TestClient

from webapp.backend.api import routes
from webapp.backend.main import app
from webapp.backend.models import Site, Trial
from webapp.backend.sources.base import SourceError


class FakeSource:
    id = "clinicaltrials_gov"
    label = "ClinicalTrials.gov"
    countries = ["United States"]

    def __init__(self, fail=False):
        self.calls = 0
        self.fail = fail

    async def search(self, condition, country):
        self.calls += 1
        if self.fail:
            raise SourceError("timed out")
        if condition == "nothing":
            return [], 0
        return [Trial(id="NCT1", status="RECRUITING",
                      sites=[Site(city="Boston", state="Massachusetts", country="United States")])], 1


@pytest.fixture
def client_with():
    def make(source):
        routes._cache._items.clear()
        app.dependency_overrides[routes.get_sources] = lambda: {source.id: source}
        return TestClient(app)
    yield make
    app.dependency_overrides.clear()
    routes._cache._items.clear()


def test_sources(client_with):
    assert client_with(FakeSource()).get("/api/sources").json() == [
        {"id": "clinicaltrials_gov", "label": "ClinicalTrials.gov", "countries": ["United States"]}
    ]


def test_search_returns_summary_and_caches(client_with):
    source = FakeSource()
    c = client_with(source)
    r = c.get("/api/search", params={"condition": "diabetes"})
    assert r.status_code == 200
    body = r.json()
    assert body["kpis"]["open_trials"] == 1 and body["by_state"][0]["state"] == "Massachusetts"
    c.get("/api/search", params={"condition": "  Diabetes "})  # same search, different spacing/case
    assert source.calls == 1


@pytest.mark.parametrize("params, status", [
    ({"condition": "d"}, 422),
    ({}, 422),
    ({"condition": "diabetes", "country": "Canada"}, 422),
    ({"condition": "diabetes", "source": "who_ictrp"}, 422),
])
def test_bad_requests(client_with, params, status):
    assert client_with(FakeSource()).get("/api/search", params=params).status_code == status


def test_source_failure_is_502(client_with):
    r = client_with(FakeSource(fail=True)).get("/api/search", params={"condition": "diabetes"})
    assert r.status_code == 502 and "ClinicalTrials.gov" in r.json()["detail"]


def test_no_results(client_with):
    body = client_with(FakeSource()).get("/api/search", params={"condition": "nothing"}).json()
    assert body["kpis"]["open_trials"] == 0 and body["trials"] == []


def test_frontend_is_served(client_with):
    r = client_with(FakeSource()).get("/")
    assert r.status_code == 200 and "Open clinical trials in the United States" in r.text

import pytest
from fastapi.testclient import TestClient

from webapp.backend.api import routes
from webapp.backend.main import app
from webapp.backend.models import Site, Trial, TrialDetail
from webapp.backend.sources.base import SourceError, TrialNotFound
from webapp.backend.terminology.umls import UmlsNotConfigured
import json

from tests.webapp.fakes_agent import ELIGIBILITY, FakeIcd, FakeRx, ScriptedChatModel, scripted_turns
from tests.webapp.test_diagnosis_filter import FakeUmls
from webapp.backend.agents.llm import LlmNotConfigured


class FullFakeUmls(FakeUmls, FakeIcd):
    """Step 1 lookups plus the ICD-10-CM tools the agent uses."""


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

    async def get_trial(self, trial_id):
        self.calls += 1
        if self.fail:
            raise SourceError("timed out")
        if trial_id != "NCT05909046":
            raise TrialNotFound(trial_id)
        return TrialDetail(id=trial_id, status="RECRUITING", title="DIP", min_age="18 Years", sex="FEMALE",
                           mesh_ids=["D1"], mesh_terms=["Term D1"], conditions=["Gestational Diabetes"],
                           eligibility_criteria=ELIGIBILITY,
                           sites=[Site(state="Ohio", country="United States", status="RECRUITING")])

    async def count_open(self, country):
        if self.fail:
            raise SourceError("timed out")
        return 25266


@pytest.fixture
def client_with():
    def make(source, umls=None, llm_factory=None):
        umls = umls or FullFakeUmls()
        app.dependency_overrides[routes.get_rxnorm] = lambda: FakeRx()
        app.dependency_overrides[routes.get_llm_factory] = lambda: (
            llm_factory or (lambda: ScriptedChatModel(turns=scripted_turns())))
        for cache in (routes._cache, routes._trial_cache, routes._count_cache, routes._diagnosis_cache,
                      routes._criteria_cache):
            cache._items.clear()
        routes._criteria_running.clear()
        app.dependency_overrides[routes.get_sources] = lambda: {source.id: source}
        app.dependency_overrides[routes.get_umls] = lambda: umls
        return TestClient(app)
    yield make
    app.dependency_overrides.clear()
    for cache in (routes._cache, routes._trial_cache, routes._count_cache, routes._diagnosis_cache, routes._criteria_cache):
        cache._items.clear()


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


@pytest.mark.parametrize("path, marker", [
    ("/", "Every open clinical trial in the US"),
    ("/search?condition=diabetes", "/js/search.js"),
    ("/trials/NCT05909046", "/js/trial.js"),
    ("/js/common.js", "mountSearchBox"),
    ("/trials/NCT05909046/eligibility", "/js/eligibility.js"),
    ("/data-schema", "/js/data-schema.js"),
])
def test_pages_are_served(client_with, path, marker):
    r = client_with(FakeSource()).get(path)
    assert r.status_code == 200 and marker in r.text


@pytest.mark.parametrize("trial_id", ["NCT05909046", "nct05909046", "05909046"])
def test_trial_ids_are_normalized(client_with, trial_id):
    body = client_with(FakeSource()).get(f"/api/trials/{trial_id}").json()
    assert body["trial"]["id"] == "NCT05909046"
    assert body["eligibility"]["parsed"] and body["kpis"]["open_us_sites"] == 1


def test_trial_is_cached(client_with):
    source = FakeSource()
    c = client_with(source)
    c.get("/api/trials/NCT05909046")
    c.get("/api/trials/nct05909046")
    assert source.calls == 1


@pytest.mark.parametrize("trial_id, status", [("NCT09999999", 404), ("abc", 422), ("NCT123", 422)])
def test_trial_errors(client_with, trial_id, status):
    r = client_with(FakeSource()).get(f"/api/trials/{trial_id}")
    assert r.status_code == status and isinstance(r.json()["detail"], str)


def test_trial_source_failure_is_502(client_with):
    assert client_with(FakeSource(fail=True)).get("/api/trials/NCT05909046").status_code == 502


def test_open_trial_count(client_with):
    assert client_with(FakeSource()).get("/api/stats/open-trials").json()["count"] == 25266


def test_demographic_filter(client_with):
    r = client_with(FakeSource()).get("/api/trials/nct05909046/filters/demographics",
                                      params={"reference_date": "2026-10-15"})
    body = r.json()
    assert r.status_code == 200 and body["trial_id"] == "NCT05909046"
    assert body["age"]["rule"] == "18 years and older" and body["sex"]["allowed"] == ["FEMALE"]
    assert body["age"]["born_on_or_before"] == "2008-10-15" and body["age"]["reasons"]


@pytest.mark.parametrize("trial_id, params, status", [
    ("NCT09999999", {}, 404), ("abc", {}, 422), ("NCT05909046", {"reference_date": "nope"}, 422),
])
def test_demographic_filter_errors(client_with, trial_id, params, status):
    assert client_with(FakeSource()).get(f"/api/trials/{trial_id}/filters/demographics", params=params).status_code == status


def test_diagnosis_filter(client_with):
    body = client_with(FakeSource()).get("/api/trials/nct05909046/filters/diagnoses").json()
    assert body["trial_id"] == "NCT05909046" and [p["prefix"] for p in body["prefixes"]] == ["O24"]
    assert body["mesh"][0]["match"] == "id+term" and body["conditions"][0]["text"] == "Gestational Diabetes"
    assert body["rules"] and "LIKE 'O24%'" in body["sql"]


class NoKeyUmls(FakeUmls):
    async def mesh_lookup(self, mesh_id, term):
        raise UmlsNotConfigured("ICD lookups need a UMLS API key. Add UMLS_API_KEY to the .env file.")


@pytest.mark.parametrize("trial_id, status", [("NCT09999999", 404), ("abc", 422)])
def test_diagnosis_filter_errors(client_with, trial_id, status):
    assert client_with(FakeSource()).get(f"/api/trials/{trial_id}/filters/diagnoses").status_code == status


def test_diagnosis_filter_without_key(client_with):
    r = client_with(FakeSource(), NoKeyUmls()).get("/api/trials/NCT05909046/filters/diagnoses")
    assert r.status_code == 503 and "UMLS_API_KEY" in r.json()["detail"]


def sse_events(text):
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


def test_criteria_agent_streams_and_caches(client_with):
    c = client_with(FakeSource())
    assert c.get("/api/trials/NCT05909046/filters/criteria").status_code == 404  # not built yet
    r = c.post("/api/trials/NCT05909046/filters/criteria/run")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    events = sse_events(r.text)
    types = [e["type"] for e in events]
    assert types[0] == "status" and types[-1] == "done"
    assert types.count("tool_call") == 3 and types.count("tool_result") == 3 and "submitting" in types and "note" in types
    result = events[-1]["result"]
    assert {c["group"] for c in result["criteria"]} == {"covered", "codable", "needs_review", "flagged"}
    cached = c.get("/api/trials/NCT05909046/filters/criteria").json()
    assert cached["criteria"] == result["criteria"]


def test_criteria_agent_without_key(client_with):
    def no_key():
        raise LlmNotConfigured("The AI pharmacist needs an Anthropic API key.")
    r = client_with(FakeSource(), llm_factory=no_key).post("/api/trials/NCT05909046/filters/criteria/run")
    assert r.status_code == 503 and "API key" in r.json()["detail"]


def test_criteria_agent_rejects_a_second_run_in_progress(client_with):
    c = client_with(FakeSource())
    routes._criteria_running.add(("clinicaltrials_gov", "NCT05909046"))
    assert c.post("/api/trials/NCT05909046/filters/criteria/run").status_code == 409


def test_criteria_agent_reports_model_errors(client_with):
    from langchain_core.messages import AIMessage
    refusing = lambda: ScriptedChatModel(turns=[AIMessage(content="", response_metadata={"stop_reason": "refusal"})])
    events = sse_events(client_with(FakeSource(), llm_factory=refusing).post(
        "/api/trials/NCT05909046/filters/criteria/run").text)
    assert events[-1] == {"type": "error", "message": "The model declined to review these criteria."}


def test_member_schema_matches_what_the_sql_uses(client_with):
    body = client_with(FakeSource()).get("/api/member-schema").json()
    by_name = {t["name"]: t for t in body["tables"]}
    assert {n for n, t in by_name.items() if t["status"] == "required"} == \
        {"members", "member_diagnoses", "member_prescriptions"}
    assert [c["name"] for c in by_name["member_prescriptions"]["columns"]] == ["member_id", "ndc", "last_fill_date"]
    assert by_name["member_pharmacy_fills"]["status"] == "preferred"
    assert "CREATE TABLE member_diagnoses" in body["ddl"]

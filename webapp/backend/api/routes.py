"""JSON API, mounted at /api."""

import json
from collections.abc import Callable
from datetime import date
from typing import Annotated

import anthropic
from fastapi.responses import StreamingResponse
from langgraph.errors import GraphRecursionError

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from webapp.backend import config
from webapp.backend.models import (
    CriteriaFilter, DemographicFilter, MemberColumn, MemberCount, MemberList, MemberSchema, MemberTable, ReviewFilter,
    WarehouseStatus,
    SearchResult, SourceOption, TrialDashboard,
)
from webapp.backend.services.eligibility_filters import build_demographic_filter
from webapp.backend.services.review import build_review, count_sql, members_sql
from webapp.backend.services.cache import TTLCache
from webapp.backend.services.summary import summarize
from webapp.backend.services.trial_summary import summarize_trial
from webapp.backend.sources import NCT_ID, SourceError, TrialNotFound, TrialSource
from webapp.backend.terminology.umls import UmlsClient, UmlsError, UmlsNotConfigured
from webapp.backend.agents import member_schema
from webapp.backend.agents.criteria_graph import RECURSION_LIMIT, build_criteria_graph, failure_text, retryable
from webapp.backend.agents.llm import LlmNotConfigured
from webapp.backend.terminology.rxnorm import RxNormClient, RxNormError
from webapp.backend.warehouse.databricks import DatabricksClient, WarehouseError, WarehouseNotConfigured

router = APIRouter(tags=["search"])
_cache: TTLCache[SearchResult] = TTLCache(config.CACHE_TTL_SECONDS)
_trial_cache: TTLCache[TrialDashboard] = TTLCache(config.CACHE_TTL_SECONDS)
_count_cache: TTLCache[int] = TTLCache(60 * 60)
# Agent results cost money to produce, so they're kept for a day (in memory only)
_criteria_cache: TTLCache[CriteriaFilter] = TTLCache(24 * 60 * 60)
_criteria_running: set[tuple] = set()


def get_sources(request: Request) -> dict[str, TrialSource]:
    """Built once at startup (see main.py); overridden in tests."""
    return request.app.state.sources


Sources = Annotated[dict[str, TrialSource], Depends(get_sources)]


def get_umls(request: Request) -> UmlsClient:
    """Built once at startup (see main.py); overridden in tests."""
    return request.app.state.umls


Umls = Annotated[UmlsClient, Depends(get_umls)]


def get_rxnorm(request: Request) -> RxNormClient:
    return request.app.state.rxnorm


def get_llm_factory(request: Request) -> Callable:
    """Returns a function that builds the chat model; overridden in tests with a scripted fake."""
    return request.app.state.criteria_llm_factory


RxNorm = Annotated[RxNormClient, Depends(get_rxnorm)]


def get_warehouse(request: Request) -> DatabricksClient:
    return request.app.state.warehouse


Warehouse = Annotated[DatabricksClient, Depends(get_warehouse)]
LlmFactory = Annotated[Callable, Depends(get_llm_factory)]


@router.get("/sources")
def list_sources(sources: Sources) -> list[SourceOption]:
    return [SourceOption(id=s.id, label=s.label, countries=s.countries) for s in sources.values()]


@router.get("/member-schema")
def get_member_schema() -> MemberSchema:
    """The member tables the filters' SQL runs against, with DDL. Describes the shape only; no data."""
    tables = [MemberTable(
        name=t["name"], status=t["status"], grain=t["grain"], key=t["key"], used_for=t["used_for"],
        columns=[MemberColumn(name=c, type=typ, description=d, nullable=c in t.get("optional", []))
                 for c, typ, d in t["columns"]]
                + [MemberColumn(name=c, type=typ, description=f"{d} (member list only)", nullable=True)
                   for c, typ, d in t.get("list_columns", [])],
    ) for t in member_schema.TABLES]
    return MemberSchema(tables=tables, ddl=member_schema.ddl())


def pick_source(sources: dict[str, TrialSource], source: str) -> TrialSource:
    trial_source = sources.get(source)
    if trial_source is None:
        raise HTTPException(422, f"Unknown source '{source}'. Choose one of: {', '.join(sources)}.")
    return trial_source


def normalize_nct_id(value: str) -> str:
    """'nct05909046', ' NCT05909046 ' and '05909046' all become 'NCT05909046'."""
    value = value.strip().upper()
    return f"NCT{value}" if value.isdigit() else value


@router.get("/search")
async def search(
    sources: Sources,
    condition: Annotated[str, Query(min_length=2, max_length=200, description="e.g. diabetes")],
    source: str = "clinicaltrials_gov",
    country: str = "United States",
) -> SearchResult:
    trial_source = pick_source(sources, source)
    if country not in trial_source.countries:
        raise HTTPException(422, f"{trial_source.label} search supports: {', '.join(trial_source.countries)}.")

    condition = " ".join(condition.split())
    key = (source, country, condition.lower())
    if (cached := _cache.get(key)) is not None:
        return cached
    try:
        trials, total = await trial_source.search(condition, country)
    except SourceError as exc:
        raise HTTPException(502, f"Couldn't get trials from {trial_source.label}. {exc}") from exc
    result = summarize(trials, source=source, country=country, condition=condition, total_matching=total)
    _cache.set(key, result)
    return result


async def load_trial(sources: dict[str, TrialSource], trial_id: str, source: str) -> TrialDashboard:
    """Fetch (or reuse from the cache) one trial, with the shared ID checks and error messages."""
    trial_source = pick_source(sources, source)
    nct_id = normalize_nct_id(trial_id)
    if not NCT_ID.match(nct_id):
        raise HTTPException(422, f"'{trial_id}' isn't an NCT ID. They look like NCT05909046: NCT followed by 8 digits.")
    key = (source, nct_id)
    if (cached := _trial_cache.get(key)) is not None:
        return cached
    try:
        detail = await trial_source.get_trial(nct_id)
    except TrialNotFound as exc:
        raise HTTPException(404, f"{trial_source.label} has no trial {nct_id}. Check the ID and try again.") from exc
    except SourceError as exc:
        raise HTTPException(502, f"Couldn't get {nct_id} from {trial_source.label}. {exc}") from exc
    result = summarize_trial(detail, source=source)
    _trial_cache.set(key, result)
    return result


@router.get("/trials/{trial_id}")
async def get_trial(sources: Sources, trial_id: str, source: str = "clinicaltrials_gov") -> TrialDashboard:
    """One trial in any status, with the derived values the trial dashboard shows."""
    return await load_trial(sources, trial_id, source)


@router.get("/trials/{trial_id}/filters/demographics", tags=["eligibility filters"])
async def demographic_filter(sources: Sources, trial_id: str, source: str = "clinicaltrials_gov",
                             reference_date: date | None = None) -> DemographicFilter:
    """Step 1 of the filter builder: the trial's age and sex limits as a member filter, with reasons.
    Ages are measured on reference_date (default: today)."""
    dashboard = await load_trial(sources, trial_id, source)
    return build_demographic_filter(dashboard.trial, reference_date or date.today())


@router.get("/stats/open-trials")
async def open_trial_count(sources: Sources, source: str = "clinicaltrials_gov",
                           country: str = "United States") -> dict:
    trial_source = pick_source(sources, source)
    if country not in trial_source.countries:
        raise HTTPException(422, f"{trial_source.label} search supports: {', '.join(trial_source.countries)}.")
    key = (source, country)
    if (cached := _count_cache.get(key)) is None:
        try:
            cached = await trial_source.count_open(country)
        except SourceError as exc:
            raise HTTPException(502, f"Couldn't get the count from {trial_source.label}. {exc}") from exc
        _count_cache.set(key, cached)
    return {"source": source, "country": country, "count": cached}


@router.get("/trials/{trial_id}/filters/criteria", tags=["eligibility filters"])
async def criteria_filter(sources: Sources, trial_id: str, source: str = "clinicaltrials_gov") -> CriteriaFilter:
    """Step 2 result from an earlier run (kept in memory for a day). 404 until it's been built."""
    dashboard = await load_trial(sources, trial_id, source)
    if (cached := _criteria_cache.get((source, dashboard.trial.id))) is None:
        raise HTTPException(404, "Step 2 hasn't been built for this trial yet.")
    return cached


@router.get("/trials/{trial_id}/filters/review", tags=["eligibility filters"])
async def review_filter(sources: Sources, trial_id: str, source: str = "clinicaltrials_gov") -> ReviewFilter:
    """Step 3: steps 1 and 2 as one Databricks SQL query, with a comment on every condition quoting
    where it came from. Before step 2 is built, the query has age and sex only."""
    return await review_for(sources, trial_id, source)


async def review_for(sources: dict[str, TrialSource], trial_id: str, source: str) -> ReviewFilter:
    dashboard = await load_trial(sources, trial_id, source)
    criteria = _criteria_cache.get((source, dashboard.trial.id))
    # Measure ages from the same day step 2 measured its time windows, so the two steps agree
    reference = criteria.reference_date if criteria else date.today()
    return build_review(build_demographic_filter(dashboard.trial, reference), criteria)


@router.get("/warehouse", tags=["warehouse"])
def warehouse_status(warehouse: Warehouse) -> WarehouseStatus:
    """Whether a Databricks SQL warehouse is set up for running the step 3 query. Never returns the token."""
    return WarehouseStatus(configured=warehouse.configured, host=warehouse.host, warehouse_id=warehouse.warehouse_id,
                           catalog=warehouse.catalog, schema_name=warehouse.schema, missing=warehouse.missing(),
                           member_list_limit=config.MEMBER_LIST_LIMIT)


async def run_on_warehouse(warehouse: DatabricksClient, statement: str, row_limit: int | None = None) -> dict:
    try:
        return await warehouse.query(statement, row_limit=row_limit)
    except WarehouseNotConfigured as exc:
        raise HTTPException(503, str(exc)) from exc
    except WarehouseError as exc:
        raise HTTPException(502, str(exc)) from exc


# The browser only names the trial: the SQL is rebuilt here from the checked rules, so nothing the
# page sends can reach the warehouse. Member data is never cached; no-store keeps browsers from caching it too.
@router.post("/trials/{trial_id}/filters/review/count", tags=["warehouse"])
async def count_members(sources: Sources, warehouse: Warehouse, response: Response, trial_id: str,
                        source: str = "clinicaltrials_gov") -> MemberCount:
    """Run the step 3 query on the Databricks warehouse and return how many members it matches."""
    review = await review_for(sources, trial_id, source)
    statement = count_sql(review)
    result = await run_on_warehouse(warehouse, statement)
    response.headers["Cache-Control"] = "no-store"
    return MemberCount(trial_id=review.trial_id, count=int(result["rows"][0][0]), criteria_built=review.criteria_built,
                       statement=statement, elapsed_ms=result["elapsed_ms"])


@router.post("/trials/{trial_id}/filters/review/members", tags=["warehouse"])
async def list_members(sources: Sources, warehouse: Warehouse, response: Response, trial_id: str,
                       source: str = "clinicaltrials_gov") -> MemberList:
    """Run the step 3 query and return the matching members (up to MEMBER_LIST_LIMIT), ordered by member_id."""
    review = await review_for(sources, trial_id, source)
    limit = config.MEMBER_LIST_LIMIT
    statement = members_sql(review, limit)
    result = await run_on_warehouse(warehouse, statement, row_limit=limit + 1)
    response.headers["Cache-Control"] = "no-store"
    rows = result["rows"]
    return MemberList(trial_id=review.trial_id, columns=result["columns"], rows=rows[:limit], limit=limit,
                      truncated=len(rows) > limit, criteria_built=review.criteria_built, statement=statement,
                      elapsed_ms=result["elapsed_ms"])


def sse(event: dict) -> str:
    return f"data: {json.dumps(event, default=str)}\n\n"


def agent_events(node: str, payload: dict) -> list[dict]:
    """What the page shows for one graph step: notes, tool calls and their results."""
    events = []
    for m in payload.get("messages", []) or []:
        if node == "pharmacist":
            blocks = m.content if isinstance(m.content, list) else [{"type": "text", "text": m.content}]
            for b in blocks:
                if not isinstance(b, dict):
                    continue
                text = (b.get("thinking") if b.get("type") == "thinking" else b.get("text") if b.get("type") == "text" else "") or ""
                if text.strip():
                    events.append({"type": "note", "text": text.strip()})
            for c in m.tool_calls:
                if c["name"] == "submit_criteria":
                    events.append({"type": "submitting", "count": len(c["args"].get("criteria", []))})
                else:
                    events.append({"type": "tool_call", "id": c["id"], "name": c["name"], "args": c["args"]})
        elif node == "tools" and m.name != "submit_criteria":
            text = str(m.content)
            events.append({"type": "tool_result", "id": m.tool_call_id, "name": m.name,
                           "result": text if len(text) <= 600 else text[:600] + "…",
                           "is_error": getattr(m, "status", "") == "error"})
        elif node == "tools" and getattr(m, "status", "") == "error":
            events.append({"type": "note", "text": "The submission didn't match the required format; asked the model to fix it."})
    if node == "remind":
        events.append({"type": "note", "text": "Reminded the model to submit its rules."})
    if node == "collect" and payload.get("submitted"):
        events.append({"type": "status", "message": "Checking every code, resolving drugs to NDCs and writing the SQL…"})
    return events


@router.post("/trials/{trial_id}/filters/criteria/run", tags=["eligibility filters"])
async def run_criteria_agent(sources: Sources, umls: Umls, rxnorm: RxNorm, make_llm: LlmFactory, trial_id: str,
                             source: str = "clinicaltrials_gov") -> StreamingResponse:
    """Run the AI pharmacist on the trial's eligibility criteria. Streams progress as server-sent
    events and ends with {"type": "done", "result": ...}. Each run calls the Anthropic API (costs money)."""
    dashboard = await load_trial(sources, trial_id, source)
    trial = dashboard.trial
    key = (source, trial.id)
    if not trial.eligibility_criteria:
        raise HTTPException(422, "This trial has no eligibility criteria text to read.")
    if key in _criteria_running:
        raise HTTPException(409, "Step 2 is already being built for this trial. Wait for it to finish.")
    try:
        llm = make_llm()
    except LlmNotConfigured as exc:
        raise HTTPException(503, str(exc)) from exc

    demographics = build_demographic_filter(trial, date.today())
    context = {
        "trial_id": trial.id, "title": trial.title, "conditions": trial.conditions,
        "age_rule": demographics.age.rule, "sex_rule": demographics.sex.rule,
        "study_population": trial.study_population, "eligibility_text": trial.eligibility_criteria,
        # Background that helps interpret the criteria ("the study drug" etc.); never a source of rules
        "official_title": trial.official_title, "brief_summary": trial.brief_summary,
        "interventions": [i.model_dump() for i in trial.interventions],
        "arms": [a.model_dump() for a in trial.arms],
    }
    graph = build_criteria_graph(llm, umls, rxnorm, model_name=config.CRITERIA_MODEL)

    async def stream():
        _criteria_running.add(key)
        try:
            yield sse({"type": "status", "message": f"The AI pharmacist is reading {trial.id}'s criteria…"})
            # "custom" carries events nodes send mid-step, e.g. "retrying in 5 s"
            async for mode, chunk in graph.astream({"context": context}, {"recursion_limit": RECURSION_LIMIT},
                                                   stream_mode=["updates", "custom"]):
                if mode == "custom":
                    yield sse(chunk)
                    continue
                for node, payload in chunk.items():
                    for event in agent_events(node, payload or {}):
                        yield sse(event)
                    if (payload or {}).get("error"):
                        yield sse({"type": "error", "message": payload["error"]})
                        return
                    if node == "finalize":
                        result = payload["result"]
                        _criteria_cache.set(key, result)
                        yield sse({"type": "done", "result": result.model_dump(mode="json")})
        except GraphRecursionError:
            yield sse({"type": "error", "message": "The agent used too many lookups without finishing. Try running it again."})
        except anthropic.AuthenticationError:
            yield sse({"type": "error", "message": "Anthropic rejected the API key. Check ANTHROPIC_API_KEY in .env."})
        except (anthropic.APIStatusError, anthropic.APIConnectionError) as exc:
            # Retryable failures were already retried in the graph; say so, so it's clear waiting didn't help
            tried = (f" after {config.CRITERIA_RETRIES} retr{'y' if config.CRITERIA_RETRIES == 1 else 'ies'} "
                     f"{config.CRITERIA_RETRY_SECONDS:g} s apart") if retryable(exc) and config.CRITERIA_RETRIES else ""
            if isinstance(exc, anthropic.RateLimitError):
                message = f"The model endpoint is rate limiting requests{tried}. Try again in a minute."
            else:
                message = f"The model endpoint call failed ({failure_text(exc)}){tried}."
            yield sse({"type": "error", "message": message})
        except (UmlsError, UmlsNotConfigured, RxNormError) as exc:
            yield sse({"type": "error", "message": f"A code lookup failed: {exc}"})
        finally:
            _criteria_running.discard(key)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

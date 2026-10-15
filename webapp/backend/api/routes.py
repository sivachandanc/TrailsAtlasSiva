"""JSON API, mounted at /api."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from webapp.backend import config
from webapp.backend.models import SearchResult, SourceOption
from webapp.backend.services.cache import TTLCache
from webapp.backend.services.summary import summarize
from webapp.backend.sources import SourceError, TrialSource

router = APIRouter(tags=["search"])
_cache: TTLCache[SearchResult] = TTLCache(config.CACHE_TTL_SECONDS)


def get_sources(request: Request) -> dict[str, TrialSource]:
    """Built once at startup (see main.py); overridden in tests."""
    return request.app.state.sources


Sources = Annotated[dict[str, TrialSource], Depends(get_sources)]


@router.get("/sources")
def list_sources(sources: Sources) -> list[SourceOption]:
    return [SourceOption(id=s.id, label=s.label, countries=s.countries) for s in sources.values()]


@router.get("/search")
async def search(
    sources: Sources,
    condition: Annotated[str, Query(min_length=2, max_length=200, description="e.g. diabetes")],
    source: str = "clinicaltrials_gov",
    country: str = "United States",
) -> SearchResult:
    trial_source = sources.get(source)
    if trial_source is None:
        raise HTTPException(422, f"Unknown source '{source}'. Choose one of: {', '.join(sources)}.")
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

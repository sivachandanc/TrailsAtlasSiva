"""Trial sources. Add a new registry here and in build_sources()."""

import httpx

from webapp.backend.sources.base import NCT_ID, SourceError, TrialNotFound, TrialSource
from webapp.backend.sources.clinicaltrials_gov import ClinicalTrialsGovSource

__all__ = ["NCT_ID", "SourceError", "TrialNotFound", "TrialSource", "build_sources"]


def build_sources(client: httpx.AsyncClient) -> dict[str, TrialSource]:
    sources: list[TrialSource] = [ClinicalTrialsGovSource(client)]
    return {s.id: s for s in sources}

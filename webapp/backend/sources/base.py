"""The interface every trial source implements."""

from typing import Protocol

from webapp.backend.models import Trial


class SourceError(Exception):
    """The source couldn't be reached or returned something unusable."""


class TrialSource(Protocol):
    id: str
    label: str
    countries: list[str]

    async def search(self, condition: str, country: str) -> tuple[list[Trial], int]:
        """Open trials for a condition in a country: (trials, total the source reports).
        May return fewer trials than the total when capped."""
        ...

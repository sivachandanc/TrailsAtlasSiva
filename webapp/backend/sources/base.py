"""The interface every trial source implements."""

import re
from typing import Protocol

from webapp.backend.models import Trial, TrialDetail

NCT_ID = re.compile(r"^NCT\d{8}$")


class SourceError(Exception):
    """The source couldn't be reached or returned something unusable."""


class TrialNotFound(Exception):
    """The source has no trial with this ID."""


class TrialSource(Protocol):
    id: str
    label: str
    countries: list[str]

    async def search(self, condition: str, country: str) -> tuple[list[Trial], int]:
        """Open trials for a condition in a country: (trials, total the source reports).
        May return fewer trials than the total when capped."""
        ...

    async def get_trial(self, trial_id: str) -> TrialDetail:
        """One trial in any status. Raises TrialNotFound."""
        ...

    async def count_open(self, country: str) -> int:
        """How many open trials have a site in the country."""
        ...

"""ClinicalTrials.gov v2 source.

The field list and path logic are ported from internal_core/explore_ctgov.py (copied, not
imported, so the app doesn't depend on the sandbox).
"""

import httpx

from webapp.backend import config
from webapp.backend.models import Site, Trial
from webapp.backend.sources.base import SourceError

PROTOCOL = "protocolSection"
DERIVED = "derivedSection"

# Trial field -> (API field name, path of keys from the top of the study JSON)
TRIAL_FIELDS = {
    "id":                      ("NCTId",                 (PROTOCOL, "identificationModule", "nctId")),
    "title":                   ("BriefTitle",            (PROTOCOL, "identificationModule", "briefTitle")),
    "status":                  ("OverallStatus",         (PROTOCOL, "statusModule", "overallStatus")),
    "start_date":              ("StartDate",             (PROTOCOL, "statusModule", "startDateStruct", "date")),
    "primary_completion_date": ("PrimaryCompletionDate", (PROTOCOL, "statusModule", "primaryCompletionDateStruct", "date")),
    "last_update":             ("LastUpdatePostDate",    (PROTOCOL, "statusModule", "lastUpdatePostDateStruct", "date")),
    "sponsor_class":           ("LeadSponsorClass",      (PROTOCOL, "sponsorCollaboratorsModule", "leadSponsor", "class")),
    "conditions":              ("Condition",             (PROTOCOL, "conditionsModule", "conditions")),
    "study_type":              ("StudyType",             (PROTOCOL, "designModule", "studyType")),
    "phases":                  ("Phase",                 (PROTOCOL, "designModule", "phases")),
    "primary_purpose":         ("DesignPrimaryPurpose",  (PROTOCOL, "designModule", "designInfo", "primaryPurpose")),
    "enrollment":              ("EnrollmentCount",       (PROTOCOL, "designModule", "enrollmentInfo", "count")),
    "intervention_types":      ("InterventionType",      (PROTOCOL, "armsInterventionsModule", "interventions", "type")),
    "sex":                     ("Sex",                   (PROTOCOL, "eligibilityModule", "sex")),
    "min_age":                 ("MinimumAge",            (PROTOCOL, "eligibilityModule", "minimumAge")),
    "max_age":                 ("MaximumAge",            (PROTOCOL, "eligibilityModule", "maximumAge")),
    "std_ages":                ("StdAge",                (PROTOCOL, "eligibilityModule", "stdAges")),
    "mesh_terms":              ("ConditionMeshTerm",     (DERIVED, "conditionBrowseModule", "meshes", "term")),
}
SITES_PATH = (PROTOCOL, "contactsLocationsModule", "locations")
SITE_API_FIELDS = ["LocationFacility", "LocationStatus", "LocationCity", "LocationState",
                   "LocationZip", "LocationCountry", "LocationGeoPoint"]


def get_path(value, path: tuple[str, ...]):
    """Follow `path` into nested dicts; on a list, apply the rest of the path to each item."""
    for i, key in enumerate(path):
        if value is None:
            return None
        if isinstance(value, list):
            return [get_path(item, path[i:]) for item in value]
        value = value.get(key)
    return value


def to_trial(study: dict) -> Trial:
    values = {name: get_path(study, path) for name, (_, path) in TRIAL_FIELDS.items()}
    # A trial lists one type per intervention; keep each type once
    values["intervention_types"] = sorted({t for t in values["intervention_types"] or [] if t})
    values = {k: v for k, v in values.items() if v is not None}
    sites = [
        Site(
            facility=s.get("facility"), city=s.get("city"), state=s.get("state"), zip=s.get("zip"),
            country=s.get("country"), status=s.get("status"),
            lat=(s.get("geoPoint") or {}).get("lat"), lon=(s.get("geoPoint") or {}).get("lon"),
        )
        for s in get_path(study, SITES_PATH) or []
    ]
    return Trial(**values, sites=sites, url=f"https://clinicaltrials.gov/study/{values['id']}")


class ClinicalTrialsGovSource:
    id = "clinicaltrials_gov"
    label = "ClinicalTrials.gov"
    countries = ["United States"]

    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    async def search(self, condition: str, country: str) -> tuple[list[Trial], int]:
        fields = [api for api, _ in TRIAL_FIELDS.values()] + SITE_API_FIELDS
        params = {
            "query.cond": condition,
            "filter.overallStatus": ",".join(config.OPEN_STATUSES),
            # Matches site countries only, unlike query.locn which also searches other text
            "filter.advanced": f'AREA[LocationCountry]"{country}"',
            "fields": "|".join(fields),
            "sort": "@relevance",  # so a capped result keeps the most relevant trials
            "countTotal": "true",
            "pageSize": config.PAGE_SIZE,
        }
        trials: list[Trial] = []
        total: int | None = None
        token: str | None = None
        while len(trials) < config.MAX_TRIALS:
            try:
                response = await self.client.get(
                    f"{config.CTGOV_BASE_URL}/studies",
                    params={**params, **({"pageToken": token} if token else {})},
                )
                response.raise_for_status()
                data = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                raise SourceError(f"ClinicalTrials.gov request failed: {exc}") from exc
            if total is None:
                total = data.get("totalCount", 0)  # only sent on the first page
            trials.extend(to_trial(s) for s in data.get("studies", []))
            token = data.get("nextPageToken")
            if not token:
                break
        return trials[: config.MAX_TRIALS], total or 0

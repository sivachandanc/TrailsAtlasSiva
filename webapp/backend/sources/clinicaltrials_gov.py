"""ClinicalTrials.gov v2 source.

The field list and path logic are ported from internal_core/explore_ctgov.py (copied, not
imported, so the app doesn't depend on the sandbox).
"""

import httpx

from webapp.backend import config
from webapp.backend.models import Arm, Contact, Intervention, Official, Outcome, Site, Trial, TrialDetail
from webapp.backend.sources.base import SourceError, TrialNotFound

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


# Extra fields for the single-trial dashboard: TrialDetail field -> (API field, path)
DETAIL_FIELDS = {
    "official_title":       ("OfficialTitle",       (PROTOCOL, "identificationModule", "officialTitle")),
    "acronym":              ("Acronym",             (PROTOCOL, "identificationModule", "acronym")),
    "brief_summary":        ("BriefSummary",        (PROTOCOL, "descriptionModule", "briefSummary")),
    "sponsor_name":         ("LeadSponsorName",     (PROTOCOL, "sponsorCollaboratorsModule", "leadSponsor", "name")),
    "collaborators":        ("CollaboratorName",    (PROTOCOL, "sponsorCollaboratorsModule", "collaborators", "name")),
    "keywords":             ("Keyword",             (PROTOCOL, "conditionsModule", "keywords")),
    "mesh_ids":             ("ConditionMeshId",     (DERIVED, "conditionBrowseModule", "meshes", "id")),
    "completion_date":      ("CompletionDate",      (PROTOCOL, "statusModule", "completionDateStruct", "date")),
    "first_posted":         ("StudyFirstPostDate",  (PROTOCOL, "statusModule", "studyFirstPostDateStruct", "date")),
    "eligibility_criteria": ("EligibilityCriteria", (PROTOCOL, "eligibilityModule", "eligibilityCriteria")),
    "study_population":     ("StudyPopulation",     (PROTOCOL, "eligibilityModule", "studyPopulation")),
    "gender_based":         ("GenderBased",         (PROTOCOL, "eligibilityModule", "genderBased")),
    "gender_description":   ("GenderDescription",   (PROTOCOL, "eligibilityModule", "genderDescription")),
}
# Lists of objects: TrialDetail field -> (API fields, path to the list, model, {model field: key in item})
DETAIL_LISTS = {
    "arms": (["ArmGroupLabel", "ArmGroupType", "ArmGroupDescription", "ArmGroupInterventionName"],
             (PROTOCOL, "armsInterventionsModule", "armGroups"), Arm,
             {"label": "label", "type": "type", "description": "description", "interventions": "interventionNames"}),
    "interventions": (["InterventionType", "InterventionName", "InterventionDescription", "InterventionOtherName"],
                      (PROTOCOL, "armsInterventionsModule", "interventions"), Intervention,
                      {"type": "type", "name": "name", "description": "description", "other_names": "otherNames"}),
    "primary_outcomes": (["PrimaryOutcomeMeasure", "PrimaryOutcomeTimeFrame", "PrimaryOutcomeDescription"],
                         (PROTOCOL, "outcomesModule", "primaryOutcomes"), Outcome,
                         {"measure": "measure", "time_frame": "timeFrame", "description": "description"}),
    "secondary_outcomes": (["SecondaryOutcomeMeasure", "SecondaryOutcomeTimeFrame", "SecondaryOutcomeDescription"],
                           (PROTOCOL, "outcomesModule", "secondaryOutcomes"), Outcome,
                           {"measure": "measure", "time_frame": "timeFrame", "description": "description"}),
    "contacts": (["CentralContactName", "CentralContactRole", "CentralContactPhone", "CentralContactPhoneExt",
                  "CentralContactEMail"],
                 (PROTOCOL, "contactsLocationsModule", "centralContacts"), Contact,
                 {"name": "name", "role": "role", "phone": "phone", "phone_ext": "phoneExt", "email": "email"}),
    "officials": (["OverallOfficialName", "OverallOfficialAffiliation", "OverallOfficialRole"],
                  (PROTOCOL, "contactsLocationsModule", "overallOfficials"), Official,
                  {"name": "name", "affiliation": "affiliation", "role": "role"}),
}
DETAIL_API_FIELDS = (
    [api for api, _ in TRIAL_FIELDS.values()] + SITE_API_FIELDS
    + [api for api, _ in DETAIL_FIELDS.values()]
    + [f for api_fields, *_ in DETAIL_LISTS.values() for f in api_fields]
)


def to_trial_detail(study: dict) -> TrialDetail:
    base = to_trial(study).model_dump()
    extra = {name: get_path(study, path) for name, (_, path) in DETAIL_FIELDS.items()}
    for name, (_, path, model, keys) in DETAIL_LISTS.items():
        extra[name] = [model(**{f: item.get(k) for f, k in keys.items() if item.get(k) is not None})
                       for item in get_path(study, path) or []]
    return TrialDetail(**base, **{k: v for k, v in extra.items() if v is not None})


class ClinicalTrialsGovSource:
    id = "clinicaltrials_gov"
    label = "ClinicalTrials.gov"
    countries = ["United States"]

    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    async def _get(self, path: str, params: dict) -> httpx.Response:
        try:
            return await self.client.get(f"{config.CTGOV_BASE_URL}{path}", params=params)
        except httpx.HTTPError as exc:
            raise SourceError(f"ClinicalTrials.gov request failed: {exc}") from exc

    def _open_filters(self, country: str) -> dict:
        return {
            "filter.overallStatus": ",".join(config.OPEN_STATUSES),
            # Matches site countries only, unlike query.locn which also searches other text
            "filter.advanced": f'AREA[LocationCountry]"{country}"',
        }

    async def search(self, condition: str, country: str) -> tuple[list[Trial], int]:
        fields = [api for api, _ in TRIAL_FIELDS.values()] + SITE_API_FIELDS
        params = {
            "query.cond": condition,
            **self._open_filters(country),
            "fields": "|".join(fields),
            "sort": "@relevance",  # so a capped result keeps the most relevant trials
            "countTotal": "true",
            "pageSize": config.PAGE_SIZE,
        }
        trials: list[Trial] = []
        total: int | None = None
        token: str | None = None
        while len(trials) < config.MAX_TRIALS:
            response = await self._get("/studies", {**params, **({"pageToken": token} if token else {})})
            data = self._json(response)
            if total is None:
                total = data.get("totalCount", 0)  # only sent on the first page
            trials.extend(to_trial(s) for s in data.get("studies", []))
            token = data.get("nextPageToken")
            if not token:
                break
        return trials[: config.MAX_TRIALS], total or 0

    async def get_trial(self, trial_id: str) -> TrialDetail:
        response = await self._get(f"/studies/{trial_id}", {"fields": "|".join(DETAIL_API_FIELDS)})
        # 404 = no such trial; 400 = the ID format was rejected (callers validate first)
        if response.status_code in (400, 404):
            raise TrialNotFound(trial_id)
        return to_trial_detail(self._json(response))

    async def count_open(self, country: str) -> int:
        params = {**self._open_filters(country), "fields": "NCTId", "pageSize": 1, "countTotal": "true"}
        return int(self._json(await self._get("/studies", params)).get("totalCount", 0))

    @staticmethod
    def _json(response: httpx.Response) -> dict:
        try:
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise SourceError(f"ClinicalTrials.gov returned an error: {exc}") from exc

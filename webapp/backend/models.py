"""Canonical data models.

Sources map their own formats into Trial / Site; everything after that (summary, API,
frontend) only ever sees these models.
"""

from datetime import date, datetime

from typing import Literal

from pydantic import BaseModel, Field


class SiteContact(BaseModel):
    name: str | None = None
    role: str | None = None
    phone: str | None = None
    email: str | None = None


class Site(BaseModel):
    facility: str | None = None
    city: str | None = None
    state: str | None = None
    zip: str | None = None
    country: str | None = None
    lat: float | None = None
    lon: float | None = None
    status: str | None = None  # site-level recruitment status; often missing
    contacts: list[SiteContact] = []  # only on a single trial's detail fetch


class Trial(BaseModel):
    id: str
    title: str | None = None
    status: str | None = None
    phases: list[str] = []
    study_type: str | None = None
    primary_purpose: str | None = None
    conditions: list[str] = []
    mesh_terms: list[str] = []
    sex: str | None = None
    min_age: str | None = None
    max_age: str | None = None
    std_ages: list[str] = []
    enrollment: int | None = None
    sponsor_class: str | None = None
    intervention_types: list[str] = []
    start_date: str | None = None  # partial dates such as "2027-05" are common
    primary_completion_date: str | None = None
    last_update: str | None = None
    sites: list[Site] = []
    url: str | None = None


class SourceOption(BaseModel):
    id: str
    label: str
    countries: list[str]


class Count(BaseModel):
    label: str
    count: int


class StateCount(BaseModel):
    state: str
    trials: int
    open_sites: int


class CityCount(BaseModel):
    city: str
    state: str | None
    trials: int


class Kpis(BaseModel):
    open_trials: int
    recruiting: int
    not_yet_recruiting: int
    open_us_sites: int
    states: int
    cities: int
    median_enrollment: float | None
    industry_share: float | None  # 0..1


class TrialRow(BaseModel):
    id: str
    title: str | None
    status: str
    phases: list[str]
    conditions: list[str]
    open_us_sites: int
    states: list[str]
    enrollment: int | None
    start_date: str | None
    primary_completion_date: str | None
    url: str | None


class SearchResult(BaseModel):
    source: str
    country: str
    condition: str
    total_matching: int  # what the source reports
    shown: int  # trials included below (may be capped)
    capped: bool
    fetched_at: datetime
    kpis: Kpis
    by_state: list[StateCount]
    top_cities: list[CityCount]
    top_conditions: list[Count]
    phases: list[Count]
    study_types: list[Count]
    purposes: list[Count]
    intervention_types: list[Count]
    sponsor_classes: list[Count]
    sex: list[Count]
    age_groups: list[Count]
    starts_by_month: list[Count]  # label = "YYYY-MM"
    completions_by_month: list[Count]
    trials: list[TrialRow]


# ---- single-trial dashboard ----

class Arm(BaseModel):
    label: str | None = None
    type: str | None = None  # EXPERIMENTAL, ACTIVE_COMPARATOR, PLACEBO_COMPARATOR, ...
    description: str | None = None
    interventions: list[str] = []  # e.g. "Drug: Polypill"


class Intervention(BaseModel):
    type: str | None = None
    name: str | None = None
    description: str | None = None
    other_names: list[str] = []


class Outcome(BaseModel):
    measure: str | None = None
    time_frame: str | None = None
    description: str | None = None


class Contact(BaseModel):
    name: str | None = None
    role: str | None = None
    phone: str | None = None
    phone_ext: str | None = None
    email: str | None = None


class Official(BaseModel):
    name: str | None = None
    affiliation: str | None = None
    role: str | None = None


class TrialDetail(Trial):
    """Everything shown on the single-trial dashboard."""
    official_title: str | None = None
    acronym: str | None = None
    brief_summary: str | None = None
    sponsor_name: str | None = None
    collaborators: list[str] = []
    keywords: list[str] = []
    mesh_ids: list[str] = []
    completion_date: str | None = None
    first_posted: str | None = None
    eligibility_criteria: str | None = None
    study_population: str | None = None
    gender_based: bool | None = None
    gender_description: str | None = None
    arms: list[Arm] = []
    interventions: list[Intervention] = []
    primary_outcomes: list[Outcome] = []
    secondary_outcomes: list[Outcome] = []
    contacts: list[Contact] = []
    officials: list[Official] = []


class Criterion(BaseModel):
    text: str
    level: int  # 0 = top-level item, 1+ = nested under the item above


class Eligibility(BaseModel):
    inclusion: list[Criterion]
    exclusion: list[Criterion]
    parsed: bool  # False -> show raw_text instead of the two lists
    raw_text: str | None


class Milestone(BaseModel):
    label: str
    date: str | None  # as given by the source (may be "2027-05")
    position: float | None  # 0..1 along the timeline bar


class SiteStateCount(BaseModel):
    state: str
    sites: int
    open_sites: int


class TrialKpis(BaseModel):
    enrollment: int | None
    sites: int
    us_sites: int
    open_us_sites: int
    states: int
    countries: int
    ages: str
    sex: str


class TrialDashboard(BaseModel):
    source: str
    fetched_at: datetime
    trial: TrialDetail
    status_label: str
    phase_labels: list[str]
    is_open: bool
    banners: list[str]  # plain-language warnings, e.g. not recruiting
    kpis: TrialKpis
    by_state: list[SiteStateCount]
    other_countries: list[Count]
    timeline: list[Milestone]
    today_position: float | None
    timeline_note: str | None
    eligibility: Eligibility


# ---- eligibility filter builder ----

class AgeLimit(BaseModel):
    raw: str | None  # exactly as ClinicalTrials.gov sent it, e.g. "18 Years"; None when not set
    value: int | None = None
    unit: str | None = None  # years / months / weeks / days
    valid: bool = True  # False when raw text couldn't be read


class AgeFilter(BaseModel):
    minimum: AgeLimit
    maximum: AgeLimit
    applies: bool
    rule: str  # e.g. "18 to 65 years", "Any age"
    born_on_or_before: date | None  # from the minimum age
    born_after: date | None  # from the maximum age
    reasons: list[str]
    sql: str


class SexFilter(BaseModel):
    raw: str | None  # ALL / FEMALE / MALE, or None when not set
    allowed: list[str]  # member sex values that pass; empty = no filter
    applies: bool
    rule: str
    gender_based: bool | None
    gender_description: str | None
    reasons: list[str]
    sql: str


class DemographicFilter(BaseModel):
    trial_id: str
    title: str | None
    status: str | None
    reference_date: date
    age: AgeFilter
    sex: SexFilter
    warnings: list[str]
    sql: str  # combined WHERE clause against the members table (birth_dte, gender_cde)
    definition: dict  # the same filter as structured JSON


# ---- eligibility step: criteria (built by the pharmacist agent) ----

class AgentDrug(BaseModel):
    """A drug the agent named: an RxNorm ingredient (rxcui) and/or an ATC class, both from tool results."""
    name: str = Field(description="Ingredient or class name as returned by find_drug / drug_class")
    rxcui: str | None = Field(default=None, description="RxNorm ingredient RxCUI from find_drug, or null for a whole class")
    atc_class: str | None = Field(default=None, description="ATC class ID from find_drug / drug_class (e.g. A10BK), or null")


class TimeWindow(BaseModel):
    """When the code or fill must be dated, counted back from today. One shape for every trial:
    "in the last N days", "at least N days ago" and "between N and M days ago" are all a range."""
    type: Literal["ever", "days_ago", "event", "not_stated"] = Field(
        description="ever: any time in history; days_ago: dated between max_days_ago and min_days_ago days "
                    "before today (either limit may be empty); event: tied to an event such as a pregnancy, "
                    "surgery or screening (give the limits too when the standard approximations cover it); "
                    "not_stated: the text gives no window")
    min_days_ago: int | None = Field(default=None, ge=0, description="At least this many days ago "
                                     "(\"diagnosed at least 6 months ago\" = 180). Empty for no lower limit.")
    max_days_ago: int | None = Field(default=None, ge=0, description="At most this many days ago "
                                     "(\"in the past 3 months\" = 90). Empty for no upper limit.")
    event: str | None = Field(default=None, description="For event: the event, quoted or paraphrased from the text")
    approximation: str | None = Field(default=None, description="For event with limits: one plain sentence for a "
                                      "care coordinator on why this window stands in for the event, e.g. "
                                      "\"A pregnancy and the months after delivery fit in the last year.\" "
                                      "No field names.")

    @property
    def bounded(self) -> bool:
        return self.min_days_ago is not None or self.max_days_ago is not None


class AgentCriterion(BaseModel):
    """One atomic eligibility rule, as submitted by the agent."""
    source_text: str = Field(description="The exact sentence or phrase from the eligibility text, copied verbatim")
    section: Literal["inclusion", "exclusion"] = Field(description="The section the text appears under, as written")
    requirement: Literal["must_have", "must_not_have"] = Field(
        description="What a member needs for this rule: must_have or must_not_have (an inclusion line can contain a must_not_have)")
    type: Literal["diagnosis", "medication", "age_sex", "lab", "procedure", "other"]
    codable: bool = Field(description="True only if claims (diagnosis codes or pharmacy fills) can answer this rule")
    icd10cm_codes: list[str] = Field(default_factory=list,
                                     description="ICD-10-CM codes from search_icd10cm / icd10cm_code / icd10cm_children; "
                                                 "each also covers its more specific codes")
    drugs: list[AgentDrug] = Field(default_factory=list)
    window: TimeWindow
    covered_by_step: Literal["age_sex"] | None = Field(
        default=None, description="Set when an earlier step already handles this rule")
    reason_not_codable: str | None = Field(default=None, description="Why claims can't answer it, when codable is false")
    rationale: str = Field(description="One or two plain sentences: how you read the rule and why you chose these codes, drugs and window")


class CriterionCode(BaseModel):
    code: str
    name: str | None
    valid: bool


class CriterionDrugOut(BaseModel):
    name: str
    rxcui: str | None
    atc_class: str | None
    atc_name: str | None
    ingredients: list[str]  # ingredient names covered (one for an ingredient, several for a class)
    ndc_count: int
    ndcs: list[str]  # every NDC, current and historical, of every product containing the ingredients
    names: list[str]  # ingredient and brand names, matched against the Rx claims' drug name
    valid: bool


class CriterionOut(BaseModel):
    id: str
    group: Literal["codable", "covered", "needs_review", "flagged"]
    source_text: str
    quote_found: bool  # the quote really appears in the trial's eligibility text
    section: str
    requirement: str
    type: str
    codes: list[CriterionCode]
    drugs: list[CriterionDrugOut]
    window: TimeWindow
    window_text: str
    approximate: bool  # the window stands in for an event, so a reviewer confirms the timing
    partial: bool = False  # applied on the drug alone (any fill, any time); an expert checks timing, duration or dose
    covered_by_step: str | None
    reason_not_codable: str | None
    rationale: str
    problems: list[str]  # why it's flagged
    tool_call_ids: list[str]  # tool calls whose results back the codes or drugs
    sql: str | None


class ToolCallLog(BaseModel):
    id: str
    name: str
    args: dict
    result: str  # short summary of what the tool returned
    is_error: bool


class CriteriaFilter(BaseModel):
    trial_id: str
    model: str
    reference_date: date
    criteria: list[CriterionOut]
    tool_calls: list[ToolCallLog]
    notes: list[str]  # the agent's progress notes, in order
    agent_summary: str | None
    sql: str
    definition: dict
    usage: dict  # input/output tokens across the run


# ---- eligibility step: review (steps 1 and 2 combined) ----

class ReviewItem(BaseModel):
    id: str  # the rule's ID from step 2 ("C3"), or "AGE" / "SEX" for step 1
    source_text: str  # the criteria text it came from, or the ClinicalTrials.gov field
    section: str | None  # inclusion / exclusion; None for step 1
    requirement: str | None  # must_have / must_not_have; None for step 1
    note: str  # what it does, what the expert checks, or why it isn't in the SQL


class ReviewCondition(BaseModel):
    id: str  # "AGE", "SEX" or the rule's ID
    label: str  # short text for the funnel, e.g. "R3 · No prior SGLT2 inhibitor"
    sql: str  # the condition exactly as in the WHERE
    expert: bool  # an expert confirms the members it returns


class ReviewFilter(BaseModel):
    trial_id: str
    title: str | None
    status: str | None
    reference_date: date
    criteria_built: bool  # False until step 2 has been run: the SQL then has age and sex only
    model: str | None  # the model that built step 2
    applied: list[ReviewItem]  # every condition in the SQL
    expert_checks: list[ReviewItem]  # in the SQL, but an expert confirms the members it returns
    not_applied: list[ReviewItem]  # claims can't answer these: a person checks them; they remove no one
    flagged: list[ReviewItem]  # failed a check (bad quote, unknown code or drug), so left out of the SQL
    covered: list[ReviewItem]  # criteria that only restate the age or sex limits
    warnings: list[str]
    sql: str  # one Databricks SQL query, with a comment on every condition quoting its source
    query: str  # the same SELECT without the closing ; and the trailing notes: what the count and list wrap
    conditions: list[ReviewCondition] = []  # the WHERE's conditions in order: the dashboard's funnel steps


class WarehouseStatus(BaseModel):
    configured: bool
    host: str | None
    warehouse_id: str | None
    catalog: str | None
    schema_name: str | None
    missing: list[str]  # the .env settings still needed
    member_list_limit: int


class MemberCount(BaseModel):
    trial_id: str
    count: int
    criteria_built: bool
    statement: str  # exactly what ran on the warehouse
    elapsed_ms: int


class MemberList(BaseModel):
    trial_id: str
    columns: list[str]
    rows: list[list[str | None]]
    limit: int
    truncated: bool  # more members match than the limit
    criteria_built: bool
    statement: str
    elapsed_ms: int


# ---------- member dashboard (aggregates from the warehouse plus trial-site proximity) ----------

class FunnelStep(BaseModel):
    id: str  # "ALL", "AGE", "SEX" or a rule ID
    label: str
    members: int  # members left after this step
    dropped: int  # removed by this step
    expert: bool


class AgeBand(BaseModel):
    label: str  # "40–44"
    female: int
    male: int
    other: int  # gender_cde not M or F


class DistanceBand(BaseModel):
    label: str  # "10–25 mi", "Unknown"
    members: int
    cumulative_share: float | None  # 0..1 of members with a known location; None for "Unknown"


class DashboardSite(BaseModel):
    index: int
    facility: str | None
    city: str | None
    state: str | None
    zip: str | None
    status: str | None
    status_label: str
    open: bool
    lat: float | None
    lon: float | None
    located_by: Literal["geopoint", "zip", "none"]
    contacts: list[SiteContact]
    within: dict[str, int]  # radius in miles ("25") -> eligible members within it
    catchment: int  # members for whom this is the nearest open site (0 for sites not recruiting)


class DashboardZip(BaseModel):
    zip: str
    state: str | None
    lat: float
    lon: float
    members: int
    nearest_site: int | None  # DashboardSite.index
    miles: float | None


class StateMembers(BaseModel):
    state: str
    members: int
    open_sites: int


class DashboardKpis(BaseModel):
    eligible: int
    all_members: int
    enrollment: int | None  # the trial's enrollment target
    us_sites: int
    open_us_sites: int
    within: dict[str, int]  # radius -> members within it of an open site
    median_miles: float | None
    located: int  # members whose ZIP was found
    unknown_location: int
    expert_checks: int
    not_applied: int
    flagged: int


class MemberDashboard(BaseModel):
    trial_id: str
    title: str | None
    status_label: str
    phase_labels: list[str]
    conditions: list[str]
    reference_date: date
    criteria_built: bool
    model: str | None
    ran_at: datetime
    radii: list[int]
    distance_to: Literal["open", "all"]  # "all" when the trial has no open US site
    kpis: DashboardKpis
    banners: list[str]
    funnel: list[FunnelStep]
    age_bands: list[AgeBand]
    median_age: float | None
    sex: list[Count]
    distance_bands: list[DistanceBand]
    sites: list[DashboardSite]
    zips: list[DashboardZip]
    by_state: list[StateMembers]
    statements: dict[str, str]  # exactly what ran on the warehouse, by name
    elapsed_ms: int


# ---------- member data schema (what the filters expect to run against) ----------

class MemberColumn(BaseModel):
    name: str
    type: str
    description: str
    nullable: bool


class MemberTable(BaseModel):
    name: str
    status: Literal["required", "reference", "preferred"]
    grain: str
    key: list[str]
    used_for: str
    columns: list[MemberColumn]


class MemberSchema(BaseModel):
    tables: list[MemberTable]
    ddl: str

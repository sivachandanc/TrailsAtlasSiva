"""Canonical data models.

Sources map their own formats into Trial / Site; everything after that (summary, API,
frontend) only ever sees these models.
"""

from datetime import datetime

from pydantic import BaseModel


class Site(BaseModel):
    facility: str | None = None
    city: str | None = None
    state: str | None = None
    zip: str | None = None
    country: str | None = None
    lat: float | None = None
    lon: float | None = None
    status: str | None = None  # site-level recruitment status; often missing


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

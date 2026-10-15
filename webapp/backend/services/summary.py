"""Turn a list of canonical trials into the dashboard summary. Pure functions, no I/O."""

import statistics
from collections import Counter
from datetime import date, datetime, timezone

from webapp.backend import config
from webapp.backend.models import (
    CityCount, Count, Kpis, SearchResult, Site, StateCount, Trial, TrialRow,
)

US = "United States"
TOP_N = 15

LABELS = {
    "RECRUITING": "Recruiting", "NOT_YET_RECRUITING": "Not yet recruiting",
    "EARLY_PHASE1": "Early phase 1", "PHASE1": "Phase 1", "PHASE2": "Phase 2",
    "PHASE3": "Phase 3", "PHASE4": "Phase 4", "NA": "No phase",
    "ALL": "Any sex", "FEMALE": "Female only", "MALE": "Male only",
    "CHILD": "Children (under 18)", "ADULT": "Adults (18–64)", "OLDER_ADULT": "Older adults (65+)",
    "UNKNOWN": "Unknown",
}
# Sponsor classes reuse codes like OTHER that mean something else elsewhere, so they get their own labels
SPONSOR_LABELS = {
    "INDUSTRY": "Industry", "NIH": "NIH", "FED": "Other US federal", "OTHER_GOV": "Other government",
    "NETWORK": "Research network", "INDIV": "Individual", "OTHER": "Academic and other",
    "AMBIG": "Unclear", "UNKNOWN": "Unknown",
}
PHASE_ORDER = ["EARLY_PHASE1", "PHASE1", "PHASE2", "PHASE3", "PHASE4", "NA"]


def label(value: str, labels: dict[str, str] = LABELS) -> str:
    """Readable label; unknown codes become sentence case (SUPPORTIVE_CARE -> Supportive care)."""
    return labels.get(value) or value.replace("_", " ").capitalize()


def is_open_us_site(site: Site) -> bool:
    # Many sites have no status of their own; inside an open trial, treat them as open
    return site.country == US and (site.status is None or site.status in config.OPEN_STATUSES)


def open_us_sites(trial: Trial) -> list[Site]:
    return [s for s in trial.sites if is_open_us_site(s)]


def counts(counter: Counter, top: int | None = None, order: list[str] | None = None,
           labels: dict[str, str] = LABELS) -> list[Count]:
    if order:
        items = [(k, counter[k]) for k in order if counter[k]] + \
                [(k, v) for k, v in counter.most_common() if k not in order]
    else:
        items = counter.most_common(top)
    return [Count(label=label(k, labels), count=v) for k, v in items]


def month_range(start: date, months: int) -> list[str]:
    out, y, m = [], start.year, start.month
    for _ in range(months):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def add_months(d: date, months: int) -> date:
    total = d.year * 12 + d.month - 1 + months
    return date(total // 12, total % 12 + 1, 1)


def summarize(trials: list[Trial], *, source: str, country: str, condition: str,
              total_matching: int, today: date | None = None) -> SearchResult:
    today = today or date.today()
    sites_by_trial = {t.id: open_us_sites(t) for t in trials}

    state_trials, state_sites, city_trials = Counter(), Counter(), Counter()
    for t in trials:
        sites = sites_by_trial[t.id]
        for s in sites:
            if s.state:
                state_sites[s.state] += 1
        state_trials.update({s.state for s in sites if s.state})
        city_trials.update({(s.city, s.state) for s in sites if s.city})

    # Prefer NLM's standard MeSH terms; fall back to the sponsor's own condition text
    condition_counter = Counter()
    for t in trials:
        condition_counter.update(set(t.mesh_terms or t.conditions))

    status = Counter(t.status for t in trials)
    enrollments = [t.enrollment for t in trials if t.enrollment is not None]

    # Timing: starts from 24 months ago to 6 months ahead; completions over the next 12 months
    start_months = month_range(add_months(today, -24), 31)
    completion_months = month_range(add_months(today, 0), 12)
    starts = Counter(t.start_date[:7] for t in trials if t.start_date)
    completions = Counter(t.primary_completion_date[:7] for t in trials if t.primary_completion_date)

    rows = []
    for t in trials:
        sites = sites_by_trial[t.id]
        rows.append(TrialRow(
            id=t.id, title=t.title, status=label(t.status or "UNKNOWN"),
            phases=[label(p) for p in t.phases], conditions=(t.mesh_terms or t.conditions)[:3],
            open_us_sites=len(sites), states=sorted({s.state for s in sites if s.state}),
            enrollment=t.enrollment, start_date=t.start_date,
            primary_completion_date=t.primary_completion_date, url=t.url,
        ))

    return SearchResult(
        source=source, country=country, condition=condition,
        total_matching=total_matching, shown=len(trials), capped=len(trials) < total_matching,
        fetched_at=datetime.now(timezone.utc),
        kpis=Kpis(
            open_trials=len(trials),
            recruiting=status["RECRUITING"],
            not_yet_recruiting=status["NOT_YET_RECRUITING"],
            open_us_sites=sum(len(s) for s in sites_by_trial.values()),
            states=len(state_trials),
            cities=len(city_trials),
            median_enrollment=statistics.median(enrollments) if enrollments else None,
            industry_share=(sum(t.sponsor_class == "INDUSTRY" for t in trials) / len(trials)) if trials else None,
        ),
        by_state=[StateCount(state=s, trials=n, open_sites=state_sites[s]) for s, n in state_trials.most_common()],
        top_cities=[CityCount(city=c, state=s, trials=n) for (c, s), n in city_trials.most_common(TOP_N)],
        # Condition names are already readable text, so they're shown as written
        top_conditions=[Count(label=k, count=v) for k, v in condition_counter.most_common(TOP_N)],
        phases=counts(Counter(p for t in trials for p in t.phases), order=PHASE_ORDER),
        study_types=counts(Counter(t.study_type for t in trials if t.study_type)),
        purposes=counts(Counter(t.primary_purpose for t in trials if t.primary_purpose)),
        intervention_types=counts(Counter(i for t in trials for i in t.intervention_types)),
        sponsor_classes=counts(Counter(t.sponsor_class for t in trials if t.sponsor_class), labels=SPONSOR_LABELS),
        sex=counts(Counter(t.sex for t in trials if t.sex), order=["ALL", "FEMALE", "MALE"]),
        age_groups=counts(Counter(a for t in trials for a in t.std_ages), order=["CHILD", "ADULT", "OLDER_ADULT"]),
        starts_by_month=[Count(label=m, count=starts[m]) for m in start_months],
        completions_by_month=[Count(label=m, count=completions[m]) for m in completion_months],
        trials=rows,
    )

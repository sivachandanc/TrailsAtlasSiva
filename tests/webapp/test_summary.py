from datetime import date

from webapp.backend.models import Site, Trial
from webapp.backend.services.summary import summarize

US = "United States"


def make_trials():
    return [
        Trial(id="NCT1", status="RECRUITING", phases=["PHASE2"], sponsor_class="INDUSTRY", enrollment=100,
              mesh_terms=["Diabetes Mellitus, Type 2"], conditions=["T2D"], sex="ALL", std_ages=["ADULT"],
              intervention_types=["DRUG"], start_date="2026-09", primary_completion_date="2027-02-15",
              sites=[
                  Site(city="Boston", state="Massachusetts", country=US, status="RECRUITING"),
                  Site(city="Boston", state="Massachusetts", country=US, status=None),        # no status: open
                  Site(city="Austin", state="Texas", country=US, status="COMPLETED"),         # closed
                  Site(city="Toronto", state="Ontario", country="Canada", status="RECRUITING"),  # not US
              ]),
        Trial(id="NCT2", status="NOT_YET_RECRUITING", phases=["NA"], sponsor_class="OTHER", enrollment=40,
              conditions=["Obesity"], sex="FEMALE", std_ages=["ADULT", "OLDER_ADULT"],
              intervention_types=["BEHAVIORAL", "OTHER"], start_date="2027-01-01",
              sites=[Site(city="Austin", state="Texas", country=US, status="NOT_YET_RECRUITING")]),
    ]


def run(trials, total=None):
    return summarize(trials, source="clinicaltrials_gov", country=US, condition="diabetes",
                     total_matching=len(trials) if total is None else total, today=date(2026, 10, 14))


def test_kpis_count_only_open_us_sites():
    k = run(make_trials()).kpis
    assert (k.open_trials, k.recruiting, k.not_yet_recruiting) == (2, 1, 1)
    assert k.open_us_sites == 3            # 2 in Boston (one has no status) + 1 open in Austin
    assert (k.states, k.cities) == (2, 2)
    assert k.median_enrollment == 70
    assert k.industry_share == 0.5


def test_state_and_city_breakdowns():
    r = run(make_trials())
    states = {s.state: (s.trials, s.open_sites) for s in r.by_state}
    assert states == {"Massachusetts": (1, 2), "Texas": (1, 1)}  # NCT1's Austin site is closed
    assert {(c.city, c.trials) for c in r.top_cities} == {("Boston", 1), ("Austin", 1)}


def test_conditions_prefer_mesh_then_sponsor_text():
    labels = {c.label for c in run(make_trials()).top_conditions}
    assert labels == {"Diabetes Mellitus, Type 2", "Obesity"}  # NCT1's "T2D" is replaced by its MeSH term


def test_labels_are_readable_and_dimension_specific():
    r = run(make_trials())
    assert [(c.label, c.count) for c in r.phases] == [("Phase 2", 1), ("No phase", 1)]
    assert {c.label for c in r.sponsor_classes} == {"Industry", "Academic and other"}
    assert {c.label for c in r.intervention_types} == {"Drug", "Behavioral", "Other"}  # OTHER isn't the sponsor label
    assert [c.label for c in r.age_groups] == ["Adults (18–64)", "Older adults (65+)"]


def test_timing_windows():
    r = run(make_trials())
    starts = {c.label: c.count for c in r.starts_by_month}
    assert starts["2026-09"] == 1 and starts["2027-01"] == 1
    assert r.starts_by_month[0].label == "2024-10" and r.starts_by_month[-1].label == "2027-04"
    completions = {c.label: c.count for c in r.completions_by_month}
    assert completions["2027-02"] == 1 and len(r.completions_by_month) == 12


def test_table_rows_and_capping():
    r = run(make_trials(), total=10)
    assert r.capped and r.shown == 2 and r.total_matching == 10
    row = next(t for t in r.trials if t.id == "NCT1")
    assert row.open_us_sites == 2 and row.states == ["Massachusetts"] and row.status == "Recruiting"


def test_empty_result():
    r = run([])
    assert r.kpis.open_trials == 0 and r.kpis.median_enrollment is None and r.trials == []

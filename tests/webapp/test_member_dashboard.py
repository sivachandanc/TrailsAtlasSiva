from datetime import date, datetime, timezone

import pytest

from tests.webapp.test_review import criteria, demographics, rule
from webapp.backend.models import Site, TrialDetail
from webapp.backend.services.eligibility_filters import build_demographic_filter
from webapp.backend.services.member_dashboard import (
    Proximity, demographics_sql, funnel_sql, geography_sql, member_distances, summarize,
)
from webapp.backend.services.review import build_review
from webapp.backend.services.trial_summary import summarize_trial

REF = date(2026, 10, 26)
RADII = (10, 25, 50, 100)
COLUMBUS = dict(city="Columbus", state="Ohio", country="United States", lat=39.96118, lon=-82.99879)


def trial(sites, status="RECRUITING"):
    return TrialDetail(id="NCT00000001", title="A test trial", status=status, min_age="18 Years", sex="FEMALE",
                       enrollment=40, sites=sites)


def run(t, *, funnel=None, demo=(), geo_rows=()):
    dashboard = summarize_trial(t, source="test", today=REF)
    review = build_review(build_demographic_filter(t, REF), None)
    funnel = funnel or [[str(k), str(100 - 10 * k)] for k in range(len(review.conditions) + 1)]
    return summarize(dashboard, review, funnel_rows=funnel, demo_rows=list(demo), geo_rows=list(geo_rows), radii=RADII,
                     ran_at=datetime.now(timezone.utc), statements={}, elapsed_ms=1)


SITES = [
    Site(facility="OSU", status="RECRUITING", zip="43210", **COLUMBUS),
    Site(facility="NYC Clinic", city="New York", state="New York", country="United States", zip="10001"),  # no geoPoint
    Site(facility="Closed Columbus", status="SUSPENDED", **{**COLUMBUS, "lat": 39.99, "lon": -83.0}),
    Site(facility="Nowhere", city="X", state="Ohio", country="United States", status="RECRUITING"),  # no location at all
    Site(facility="Toronto", city="Toronto", country="Canada", status="RECRUITING", lat=43.65, lon=-79.38),
]


@pytest.mark.anyio
async def test_the_funnel_writes_each_condition_once_in_order():
    c = await criteria([rule("Type 2 diabetes", "inclusion", "must_have", "diagnosis", icd10cm_codes=["E11"])])
    r = build_review(demographics(), c)
    sql = funnel_sql(r)
    assert [x.id for x in r.conditions] == ["AGE", "SEX", "C1"] and r.conditions[2].label == "C1 · Type 2 diabetes"
    assert sql.count("FROM mx_claims") == 1 and sql.count("UNION ALL") == 3
    assert "step_0 AS (\n  SELECT * FROM members\n)" in sql and "step_3 AS (\n  -- C1 · Type 2 diabetes\n  SELECT * FROM step_2" in sql
    # A condition ending in a -- comment can't swallow the closing parenthesis
    assert "-- at least 18 years old\n)" in sql
    assert sql.endswith("SELECT 3 AS step, COUNT(*) AS members FROM step_3")
    for wrapped in (demographics_sql(r), geography_sql(r)):
        assert f"WHERE m.member_id IN (\n{r.query}\n)\nGROUP BY 1, 2" in wrapped and "first_nme" not in wrapped
    assert "MONTHS_BETWEEN(DATE '2026-10-26', m.birth_dte)" in demographics_sql(r)


def test_funnel_steps_report_what_each_condition_removed():
    d = run(trial(SITES), funnel=[["0", "100"], ["1", "70"], ["2", "70"]])
    assert [(s.id, s.members, s.dropped) for s in d.funnel] == [("ALL", 100, 0), ("AGE", 70, 30), ("SEX", 70, 0)]
    assert d.kpis.eligible == 70 and d.kpis.all_members == 100


def test_sites_are_placed_by_geopoint_or_zip_and_only_us_sites_count():
    d = run(trial(SITES))
    assert [(s.facility, s.located_by, s.open) for s in d.sites] == [
        ("OSU", "geopoint", True), ("NYC Clinic", "zip", True),  # no status: inherits the open trial's
        ("Closed Columbus", "geopoint", False), ("Nowhere", "none", True)]
    assert d.kpis.us_sites == 4 and d.kpis.open_us_sites == 3 and d.distance_to == "open"
    assert any("1 US site has no location" in b for b in d.banners)


def test_members_are_measured_to_the_nearest_open_site():
    d = run(trial(SITES), funnel=[["0", "20"], ["1", "12"], ["2", "12"]],
            geo_rows=[["43004", "OH", "5"], ["10001", "NY", "4"], ["2139", "MA", "1"], ["", None, "1"], ["99999", "XX", "1"]])
    osu, nyc, closed, _ = d.sites
    # 10001 is the NYC site's own ZIP, so 0 miles; 43004 is ~11 miles from OSU; Cambridge is nearest to NYC
    z = {x.zip: x for x in d.zips}
    assert z["10001"].miles == 0 and z["10001"].nearest_site == nyc.index
    assert z["43004"].nearest_site == osu.index and 10 < z["43004"].miles < 12
    assert z["02139"].nearest_site == nyc.index and 180 < z["02139"].miles < 200
    assert osu.catchment == 5 and nyc.catchment == 5 and closed.catchment == 0
    # A site that isn't recruiting still shows who's in reach "if it opens"
    assert closed.within == {"10": 0, "25": 5, "50": 5, "100": 5}
    assert d.kpis.within == {"10": 4, "25": 9, "50": 9, "100": 9} and d.kpis.located == 10
    assert d.kpis.unknown_location == 2 and any("2 members have a ZIP code" in b for b in d.banners)
    assert [(b.label, b.members) for b in d.distance_bands] == [
        ("Under 10 mi", 4), ("10–25 mi", 5), ("25–50 mi", 0), ("50–100 mi", 0), ("100–250 mi", 1),
        ("250 mi or more", 0), ("Unknown", 2)]
    assert d.distance_bands[1].cumulative_share == 0.9
    assert [(s.state, s.members, s.open_sites) for s in d.by_state][:2] == [("Ohio", 5, 2), ("New York", 4, 1)]


def test_with_no_open_site_distances_are_to_every_us_site():
    d = run(trial([Site(facility="Closed", status="COMPLETED", **COLUMBUS)], status="ACTIVE_NOT_RECRUITING"),
            geo_rows=[["43004", "OH", "3"]])
    assert d.distance_to == "all" and d.sites[0].catchment == 3
    assert any("distances are to every US site" in b for b in d.banners)


def test_ages_are_banded_and_sexes_counted():
    d = run(trial(SITES), demo=[["18", "F", "2"], ["24", "F", "1"], ["24", "M", "1"], ["44", None, "1"], [None, "F", "9"]])
    assert [(b.label, b.female, b.male, b.other) for b in d.age_bands] == [
        ("15–19", 2, 0, 0), ("20–24", 1, 1, 0), ("25–29", 0, 0, 0), ("30–34", 0, 0, 0), ("35–39", 0, 0, 0),
        ("40–44", 0, 0, 1)]
    assert d.median_age == 24
    assert [(s.label, s.count) for s in d.sex] == [("Female", 12), ("Male", 1), ("Other or not recorded", 1)]


def test_the_member_list_gets_each_members_nearest_open_site():
    prox = Proximity(summarize_trial(trial(SITES), source="test", today=REF))
    assert member_distances(prox, "10001") == ("NYC Clinic, New York, New York", "0.0")
    assert member_distances(prox, "") == (None, None) and member_distances(prox, "99999") == (None, None)

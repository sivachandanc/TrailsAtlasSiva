from datetime import date

from webapp.backend.models import Site, TrialDetail
from webapp.backend.services.trial_summary import age_text, parse_eligibility, summarize_trial

US = "United States"


# ---------- eligibility parser ----------

def test_standard_headings_and_nested_bullets():
    e = parse_eligibility(
        "Inclusion Criteria:\n\n* Adults\n* Diagnosed by:\n\n  * test A\n  * test B\n\n"
        "Exclusion Criteria:\n\n1. Pregnancy\n2. eGFR \\< 30"
    )
    assert e.parsed
    assert [(c.text, c.level) for c in e.inclusion] == [("Adults", 0), ("Diagnosed by:", 0), ("test A", 1), ("test B", 1)]
    assert [c.text for c in e.exclusion] == ["Pregnancy", "eGFR < 30"]  # markdown escape removed


def test_group_prefixed_headings_and_inline_content():
    e = parse_eligibility(
        "DIP - Inclusion Criteria:\n* Postpartum\n\nDIP- Exclusion Criteria:\n* No OGTT\n\n"
        "PP CARE Inclusion criteria: Enrolled DIP participants who speak English.\n\n"
        "PP CARE Exclusion criteria: Spanish speakers."
    )
    assert [c.text for c in e.inclusion] == ["Postpartum", "Enrolled DIP participants who speak English."]
    assert [c.text for c in e.exclusion] == ["No OGTT", "Spanish speakers."]


def test_sentence_mentioning_criteria_is_not_a_heading():
    e = parse_eligibility("Inclusion Criteria:\nPatients meeting the inclusion criteria will be screened\n")
    assert [c.text for c in e.inclusion] == ["Patients meeting the inclusion criteria will be screened"]
    assert e.exclusion == []


def test_no_headings_falls_back_to_raw_text():
    e = parse_eligibility("Adults with asthma who can attend weekly visits.")
    assert not e.parsed and e.raw_text.startswith("Adults with asthma")


def test_empty_criteria():
    e = parse_eligibility(None)
    assert not e.parsed and e.raw_text is None


# ---------- trial summary ----------

def trial(**kw):
    base = dict(id="NCT1", status="RECRUITING", sex="ALL", min_age="18 Years", max_age="65 Years",
                first_posted="2023-11-22", start_date="2024-03-11", primary_completion_date="2027-09",
                completion_date="2027-12-01")
    return TrialDetail(**{**base, **kw})


def test_sites_kpis_and_states():
    t = trial(sites=[
        Site(state="Texas", country=US, status="RECRUITING"),
        Site(state="Texas", country=US, status=None),           # no status in an open trial: open
        Site(state="Ohio", country=US, status="COMPLETED"),
        Site(state="Ontario", country="Canada", status="RECRUITING"),
    ])
    d = summarize_trial(t, source="clinicaltrials_gov", today=date(2026, 10, 14))
    k = d.kpis
    assert (k.sites, k.us_sites, k.open_us_sites, k.states, k.countries) == (4, 3, 2, 2, 2)
    assert {s.state: (s.sites, s.open_sites) for s in d.by_state} == {"Texas": (2, 2), "Ohio": (1, 0)}
    assert [(c.label, c.count) for c in d.other_countries] == [("Canada", 1)]
    assert k.ages == "18 to 65 years" and k.sex == "Any"
    assert d.is_open and d.banners == []


def test_closed_trial_banners_and_unlisted_sites_are_not_open():
    t = trial(status="COMPLETED", sites=[Site(state="Texas", country=US, status=None)])
    d = summarize_trial(t, source="clinicaltrials_gov", today=date(2026, 10, 14))
    assert not d.is_open and d.kpis.open_us_sites == 0
    assert d.banners == ["This trial is completed, so it isn't enrolling new participants."]


def test_no_us_sites_banner():
    d = summarize_trial(trial(sites=[Site(country="Thailand")]), source="x", today=date(2026, 10, 14))
    assert "This trial has no sites in the United States." in d.banners


def test_timeline_positions_and_note():
    d = summarize_trial(trial(), source="x", today=date(2026, 10, 14))
    pos = {m.label: m.position for m in d.timeline}
    assert pos["First posted"] == 0 and pos["Completion"] == 1
    assert pos["Start"] < d.today_position < pos["Primary completion"]  # partial date "2027-09" parsed
    assert d.timeline_note == "Started 31 months ago, primary completion expected in 11 months"


def test_age_text():
    assert age_text("18 Years", None) == "18 years and over"
    assert age_text(None, "17 Years") == "Up to 17 years"
    assert age_text("6 Months", "17 Years") == "6 months to 17 years"
    assert age_text(None, None) == "Any age"

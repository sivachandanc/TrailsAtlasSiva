from datetime import date

import pytest

from tests.webapp.fakes_agent import CONTEXT, FakeIcd, FakeRx
from webapp.backend.models import TrialDetail
from webapp.backend.services.criteria_filter import build_criteria_filter
from webapp.backend.services.eligibility_filters import build_demographic_filter
from webapp.backend.services.review import build_review

pytestmark = pytest.mark.anyio
REF = date(2026, 10, 26)
TEXT = """Inclusion Criteria:
* Type 2 diabetes
* On Jardiance for at least 3 months
* HbA1c between 7% and 10%
Exclusion Criteria:
* Use of an SGLT2 inhibitor in the past 3 months"""


def rule(text, section, requirement, type_, **kw):
    return {"source_text": text, "section": section, "requirement": requirement, "type": type_, "codable": True,
            "window": {"type": "ever"}, "rationale": f"Why {text}.", **kw}


async def criteria(rules):
    return await build_criteria_filter(submitted={"criteria": rules}, context={**CONTEXT, "eligibility_text": TEXT},
                                       messages=[], umls=FakeIcd(), rxnorm=FakeRx(), model="claude-opus-5-5",
                                       reference_date=REF)


def demographics(**kw):
    return build_demographic_filter(TrialDetail(id="NCT00000001", title="A test trial", min_age="18 Years",
                                                sex="FEMALE", **kw), REF)


async def test_every_condition_is_commented_with_its_source():
    c = await criteria([
        rule("Type 2 diabetes", "inclusion", "must_have", "diagnosis", icd10cm_codes=["E11"]),
        rule("Use of an SGLT2 inhibitor in the past 3 months", "exclusion", "must_not_have", "medication",
             drugs=[{"name": "SGLT2", "atc_class": "A10BK"}], window={"type": "days_ago", "max_days_ago": 90}),
    ])
    r = build_review(demographics(), c)
    sql = r.sql
    assert sql.startswith("-- Trial NCT00000001: A test trial") and r.criteria_built
    assert '  -- Source: ClinicalTrials.gov minimumAge "18 Years", maximumAge not set\n  birth_dte <=' in sql
    assert '  -- Source: ClinicalTrials.gov sex "FEMALE"\n  AND gender_cde = \'F\'' in sql
    assert '  -- C1 · Inclusion · must have · diagnosis\n  -- Criteria: "Type 2 diabetes"\n  -- Why: Why Type 2 diabetes.' in sql
    assert "  -- Codes: ICD-10-CM E11 (Type 2 diabetes mellitus), each with its more specific codes" in sql
    assert '  -- C2 · Exclusion · must not have · medication\n  -- Criteria: "Use of an SGLT2 inhibitor in the past 3 months"' in sql
    assert "  -- Drugs: SGLT2 inhibitors (ATC A10BK): 3 NDCs and 5 drug names" in sql
    assert "  -- When: Filled in the last 90 days" in sql and "  AND member_id NOT IN (" in sql
    assert [i.id for i in r.applied] == ["AGE", "SEX", "C1", "C2"] and not r.expert_checks


async def test_expert_checks_and_rules_left_out_are_listed():
    c = await criteria([
        rule("On Jardiance for at least 3 months", "inclusion", "must_have", "medication", codable=False,
             drugs=[{"name": "empagliflozin", "rxcui": "1545653"}], reason_not_codable="Duration isn't in claims."),
        rule("HbA1c between 7% and 10%", "inclusion", "must_have", "lab", codable=False,
             reason_not_codable="Lab results aren't in claims."),
        rule("Made-up rule", "inclusion", "must_have", "diagnosis", icd10cm_codes=["E11"]),
    ])
    r = build_review(demographics(), c)
    assert [i.id for i in r.expert_checks] == ["C1"] and "Duration isn't in claims." in r.expert_checks[0].note
    assert "  -- EXPERT CHECK: Filtered on the drug alone. Duration isn't in claims." in r.sql
    assert [i.id for i in r.not_applied] == ["C2"] and [i.id for i in r.flagged] == ["C3"]
    # Rules left out are listed after the query, never inside it
    body, footer = r.sql.split(";\n\n")
    assert "HbA1c" not in body and "Made-up rule" not in body
    assert '-- C2 · Inclusion · "HbA1c between 7% and 10%": Lab results aren\'t in claims.' in footer
    assert "-- Left out after failing a check" in footer and "-- C3 · Inclusion · \"Made-up rule\"" in footer


async def test_a_line_break_in_the_criteria_text_cant_escape_its_comment():
    sneaky = "Type 2 diabetes\nDROP TABLE members"
    c = await criteria([rule(sneaky, "inclusion", "must_have", "diagnosis", icd10cm_codes=["E11"])])
    r = build_review(demographics(), c)
    assert all(line.lstrip().startswith("--") for line in r.sql.splitlines() if "DROP TABLE" in line)


def test_before_step_2_the_query_has_age_and_sex_only():
    r = build_review(demographics(), None)
    assert not r.criteria_built and r.warnings[0].startswith("Step 2 (eligibility criteria) hasn't been built")
    assert "member_id IN" not in r.sql and "gender_cde = 'F'" in r.sql and r.sql.rstrip().endswith(";")


def test_gender_based_eligibility_is_an_expert_check():
    r = build_review(demographics(gender_based=True), None)
    assert [i.id for i in r.expert_checks] == ["SEX"] and "  -- EXPERT CHECK: Eligibility is based on gender identity" in r.sql


def test_no_conditions_at_all():
    r = build_review(build_demographic_filter(TrialDetail(id="NCT1", sex="ALL"), REF), None)
    assert "WHERE" not in r.sql and "everyone passes" in r.sql and not r.applied


def test_the_semicolon_isnt_swallowed_by_a_trailing_comment():
    # The age condition ends with "-- at most 75 years old"; a ; on that line would be part of the comment
    r = build_review(demographics(max_age="75 Years"), None)
    assert r.sql.splitlines()[-1] == ";" and "75 years old;" not in r.sql

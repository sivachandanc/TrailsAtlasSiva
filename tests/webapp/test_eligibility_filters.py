from datetime import date

import pytest

from webapp.backend.models import TrialDetail
from webapp.backend.services.eligibility_filters import (
    build_demographic_filter, parse_age, subtract,
)

REF = date(2026, 10, 15)


def trial(**kw):
    return TrialDetail(**{"id": "NCT00000001", "status": "RECRUITING", **kw})


# ---------- parsing ----------

@pytest.mark.parametrize("raw, value, unit", [
    ("18 Years", 18, "years"), ("1 Year", 1, "years"), ("6 Months", 6, "months"),
    ("8 Weeks", 8, "weeks"), ("28 Days", 28, "days"), (" 65 years ", 65, "years"),
])
def test_parse_age(raw, value, unit):
    a = parse_age(raw)
    assert (a.value, a.unit, a.valid) == (value, unit, True)


@pytest.mark.parametrize("raw", [None, "", "  "])
def test_missing_age_is_no_limit(raw):
    a = parse_age(raw)
    assert a.value is None and a.valid and a.raw is None


@pytest.mark.parametrize("raw", ["6 Hours", "30 Minutes", "N/A", "eighteen"])
def test_unreadable_age_is_invalid(raw):
    assert not parse_age(raw).valid


def test_subtract_clamps_month_ends():
    assert subtract(date(2026, 3, 31), 1, "months") == date(2026, 2, 28)
    assert subtract(date(2028, 2, 29), 1, "years") == date(2027, 2, 28)
    assert subtract(REF, 2, "weeks") == date(2026, 10, 1)


# ---------- age filter ----------

def test_min_and_max_are_inclusive():
    f = build_demographic_filter(trial(min_age="18 Years", max_age="65 Years"), REF)
    assert f.age.applies and f.age.rule == "18 to 65 years"
    assert f.age.born_on_or_before == date(2008, 10, 15)   # turns 18 on the reference date: in
    assert f.age.born_after == date(1960, 10, 15)          # turns 66 on the reference date: out
    assert "birth_dte <= DATE '2008-10-15'" in f.sql and "birth_dte > DATE '1960-10-15'" in f.sql
    assert any("inclusive" in r for r in f.age.reasons)


def test_no_age_limits():
    f = build_demographic_filter(trial(), REF)
    assert not f.age.applies and f.age.rule == "Any age"
    assert f.age.born_on_or_before is None and f.age.born_after is None
    assert any("No minimum age" in r for r in f.age.reasons) and any("No maximum age" in r for r in f.age.reasons)


def test_min_only_and_max_only():
    assert build_demographic_filter(trial(min_age="18 Years"), REF).age.rule == "18 years and older"
    f = build_demographic_filter(trial(max_age="8 Years"), REF)
    assert f.age.rule == "Up to 8 years" and f.age.born_after == date(2017, 10, 15)


def test_zero_minimum_and_mixed_units():
    f = build_demographic_filter(trial(min_age="0 Months", max_age="8 Weeks"), REF)
    assert f.age.born_on_or_before is None and f.age.rule == "Up to 8 weeks"
    assert f.age.born_after == date(2026, 8, 13)  # 9 weeks (63 days) before
    assert any("newborns qualify" in r for r in f.age.reasons)
    assert build_demographic_filter(trial(min_age="6 Months", max_age="17 Years"), REF).age.rule == "6 months to 17 years"


def test_min_above_max_switches_age_off_with_warning():
    f = build_demographic_filter(trial(min_age="70 Years", max_age="18 Years"), REF)
    assert not f.age.applies and any("above the maximum" in w for w in f.warnings)


def test_unreadable_age_warns():
    f = build_demographic_filter(trial(min_age="6 Hours"), REF)
    assert not f.age.applies and any("6 Hours" in w for w in f.warnings)


# ---------- sex filter ----------

@pytest.mark.parametrize("sex, allowed, rule, reason, sql", [
    ("FEMALE", ["FEMALE"], "Female only", "only female members", "gender_cde = 'F'"),
    ("MALE", ["MALE"], "Male only", "only male members", "gender_cde = 'M'"),
    ("ALL", [], "Any sex", "any sex", "TRUE"),
    (None, [], "Any sex", "isn't stated", "TRUE"),
])
def test_sex(sex, allowed, rule, reason, sql):
    f = build_demographic_filter(trial(sex=sex), REF)
    assert f.sex.allowed == allowed and f.sex.rule == rule and f.sex.applies == bool(allowed)
    assert any(reason in r for r in f.sex.reasons) and f.sex.sql.startswith(sql)


def test_gender_based_warns_once_with_description():
    f = build_demographic_filter(trial(sex="FEMALE", gender_based=True, gender_description="Pregnant individuals."), REF)
    assert f.sex.applies
    (warning,) = [w for w in f.warnings if "gender identity" in w]
    assert warning.endswith("“Pregnant individuals.”")


def test_sql_without_any_filter():
    f = build_demographic_filter(trial(sex="ALL"), REF)
    assert "WHERE" not in f.sql and "everyone passes" in f.sql
    assert f.definition["sex"]["allowed"] == "any"

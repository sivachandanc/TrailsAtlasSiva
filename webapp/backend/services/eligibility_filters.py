"""Step 1 of the eligibility filter builder: age and sex.

Turns a trial's ClinicalTrials.gov fields (minimumAge, maximumAge, sex, genderBased) into a
filter on the members table (member_id, birth_dte, gender_cde), with a plain-language reason for every decision.
Pure functions, no I/O.
"""

import calendar
import re
from datetime import date, timedelta

from webapp.backend.agents.member_schema import GENDER_CODES, MEMBERS
from webapp.backend.models import AgeFilter, AgeLimit, DemographicFilter, SexFilter, TrialDetail

AGE_TEXT = re.compile(r"^\s*(\d+)\s*(year|month|week|day)s?\s*$", re.I)  # minutes/hours can't be checked
SEX_LABELS = {"FEMALE": "female", "MALE": "male"}


# ---------- ages ----------

def parse_age(raw: str | None) -> AgeLimit:
    """'18 Years' -> 18 years. Missing -> no limit. Anything else (e.g. '6 Hours') -> invalid."""
    if raw is None or not raw.strip():
        return AgeLimit(raw=None)
    m = AGE_TEXT.match(raw)
    if not m:
        return AgeLimit(raw=raw, valid=False)
    return AgeLimit(raw=raw, value=int(m.group(1)), unit=m.group(2).lower() + "s")


def subtract(d: date, value: int, unit: str) -> date:
    """d minus value units; month and year steps keep the day, clamped to the month's length."""
    if unit in ("years", "months"):
        months = value * 12 if unit == "years" else value
        total = d.year * 12 + (d.month - 1) - months
        y, m = divmod(total, 12)
        return date(y, m + 1, min(d.day, calendar.monthrange(y, m + 1)[1]))
    return d - timedelta(days=value * 7 if unit == "weeks" else value)


def amount(value: int, unit: str) -> str:
    """18, 'years' -> '18 years'; 1, 'years' -> '1 year'."""
    return f"{value} {unit[:-1] if value == 1 else unit}"


def fmt_date(d: date) -> str:
    return f"{d:%b} {d.day}, {d.year}"


def build_age_filter(trial: TrialDetail, ref: date) -> tuple[AgeFilter, list[str]]:
    lo, hi = parse_age(trial.min_age), parse_age(trial.max_age)
    reasons, warnings = [], []
    born_on_or_before = born_after = None

    if lo.value == 0:
        reasons.append(f"The minimum age is “{lo.raw}”, which is no lower limit: newborns qualify.")
    elif lo.value is not None:
        born_on_or_before = subtract(ref, lo.value, lo.unit)
        reasons.append(
            f"The sponsor set a minimum age of “{lo.raw}”, so a member must be at least "
            f"{amount(lo.value, lo.unit)} old on {fmt_date(ref)}: born on or before {fmt_date(born_on_or_before)}."
        )
    elif not lo.valid:
        warnings.append(f"The minimum age “{lo.raw}” isn't in a form that can be checked against a birth date, "
                        "so it's treated as not set. Check it on ClinicalTrials.gov.")
    else:
        reasons.append("No minimum age is set on ClinicalTrials.gov, so there's no lower age limit.")

    if hi.value is not None:
        born_after = subtract(ref, hi.value + 1, hi.unit)
        reasons.append(
            f"The sponsor set a maximum age of “{hi.raw}”. ClinicalTrials.gov treats the maximum as inclusive, "
            f"so a member can be {amount(hi.value, hi.unit)} old but not {amount(hi.value + 1, hi.unit)}: "
            f"born after {fmt_date(born_after)}."
        )
    elif not hi.valid:
        warnings.append(f"The maximum age “{hi.raw}” isn't in a form that can be checked against a birth date, "
                        "so it's treated as not set. Check it on ClinicalTrials.gov.")
    else:
        reasons.append("No maximum age is set on ClinicalTrials.gov, so there's no upper age limit.")

    if born_on_or_before and born_after and born_on_or_before <= born_after:
        warnings.append(f"The minimum age ({lo.raw}) is above the maximum age ({hi.raw}), so no one could pass. "
                        "The age filter is switched off until someone checks the trial's record.")
        born_on_or_before = born_after = None

    applies = bool(born_on_or_before or born_after)
    if born_on_or_before and born_after:
        rule = (f"{lo.value} to {amount(hi.value, hi.unit)}" if lo.unit == hi.unit
                else f"{amount(lo.value, lo.unit)} to {amount(hi.value, hi.unit)}")
    elif born_on_or_before:
        rule = f"{amount(lo.value, lo.unit)} and older"
    elif born_after:
        rule = f"Up to {amount(hi.value, hi.unit)}"
    else:
        rule = "Any age"

    conditions = []
    if born_on_or_before:
        conditions.append(f"birth_dte <= DATE '{born_on_or_before.isoformat()}'  -- at least {lo.raw.lower()} old")
    if born_after:
        conditions.append(f"birth_dte > DATE '{born_after.isoformat()}'  -- at most {hi.raw.lower()} old")
    sql = "\n  AND ".join(conditions) if conditions else "TRUE  -- no age limit"

    return AgeFilter(minimum=lo, maximum=hi, applies=applies, rule=rule, born_on_or_before=born_on_or_before,
                     born_after=born_after, reasons=reasons, sql=sql), warnings


# ---------- sex ----------

def build_sex_filter(trial: TrialDetail) -> tuple[SexFilter, list[str]]:
    raw, warnings = trial.sex, []
    if raw in SEX_LABELS:
        allowed = [raw]
        rule = f"{SEX_LABELS[raw].capitalize()} only"
        reasons = [f"The sponsor set sex to {raw}, so only {SEX_LABELS[raw]} members are included."]
        sql = f"gender_cde = '{GENDER_CODES[raw]}'"
    elif raw == "ALL":
        allowed, rule = [], "Any sex"
        reasons = ["The sponsor set sex to ALL, so members of any sex are included."]
        sql = "TRUE  -- any sex"
    else:
        allowed, rule = [], "Any sex"
        reasons = ["Sex isn't stated on ClinicalTrials.gov, so no sex filter is applied."]
        sql = "TRUE  -- sex not stated"

    if trial.gender_based:
        desc = (trial.gender_description or "").strip().rstrip(".")
        note = f" The sponsor's description: “{desc}.”" if desc else ""
        warnings.append("Eligibility is based on gender identity (genderBased is true). Claims data usually "
                        f"records sex, which may not match, so review these members by hand.{note}")
        reasons.append("Because eligibility is gender-based, the sex filter is a starting point, not a final answer.")

    return SexFilter(raw=raw, allowed=allowed, applies=bool(allowed), rule=rule, gender_based=trial.gender_based,
                     gender_description=trial.gender_description, reasons=reasons, sql=sql), warnings


# ---------- the step ----------

def build_demographic_filter(trial: TrialDetail, reference_date: date) -> DemographicFilter:
    age, age_warnings = build_age_filter(trial, reference_date)
    sex, sex_warnings = build_sex_filter(trial)
    parts = [p for p, f in ((age.sql, age), (sex.sql, sex)) if f.applies]
    sql = (f"SELECT member_id\nFROM {MEMBERS}\nWHERE " + "\n  AND ".join(parts)) if parts \
        else f"SELECT member_id\nFROM {MEMBERS}  -- no age or sex limits: everyone passes this step"
    definition = {
        "step": "age_sex",
        "trial_id": trial.id,
        "reference_date": reference_date.isoformat(),
        "age": {"minimum": age.minimum.raw, "maximum": age.maximum.raw,
                "born_on_or_before": age.born_on_or_before.isoformat() if age.born_on_or_before else None,
                "born_after": age.born_after.isoformat() if age.born_after else None},
        "sex": {"source_value": sex.raw, "allowed": sex.allowed or "any", "gender_based": bool(sex.gender_based)},
    }
    return DemographicFilter(
        trial_id=trial.id, title=trial.title, status=trial.status, reference_date=reference_date,
        age=age, sex=sex, warnings=age_warnings + sex_warnings,
        sql=sql, definition=definition,
    )

"""Step 3 of the eligibility filter builder: review.

Combines step 1 (age and sex) and step 2 (eligibility criteria) into one Databricks SQL query.
Every condition carries a comment saying where it came from: the ClinicalTrials.gov field for age
and sex, or the criteria text, the rationale, the codes or drugs and the time window for a rule.
Rules a person has to check are listed at the end of the query and returned for the page.
Pure functions, no I/O.
"""

from webapp.backend.agents.member_schema import MEMBER_LIST_COLUMNS, MEMBERS
from webapp.backend.models import CriteriaFilter, CriterionOut, DemographicFilter, ReviewFilter, ReviewItem

SECTION = {"inclusion": "Inclusion", "exclusion": "Exclusion"}
REQUIREMENT = {"must_have": "must have", "must_not_have": "must not have"}
MAX_CODES = 6  # codes named in a rule's comment before "and N more"


def oneline(text: str | None) -> str:
    """Text safe for a -- comment: a line break would end the comment and run the rest as SQL."""
    return " ".join((text or "").split())


def quoted(raw: str | None) -> str:
    return f'"{raw}"' if raw is not None else "not set"


def indent(sql: str, by: str = "  ") -> str:
    """Indent every line after the first, so a multi-line condition lines up under its AND."""
    first, *rest = sql.split("\n")
    return "\n".join([first] + [by + line for line in rest])


def codes_text(c: CriterionOut) -> str | None:
    valid = [x for x in c.codes if x.valid]
    if not valid:
        return None
    named = [f"{x.code} ({x.name})" if x.name else x.code for x in valid[:MAX_CODES]]
    more = f" and {len(valid) - MAX_CODES} more" if len(valid) > MAX_CODES else ""
    return "ICD-10-CM " + ", ".join(named) + more + ", each with its more specific codes"


def drugs_text(c: CriterionOut) -> str | None:
    valid = [d for d in c.drugs if d.valid]
    if not valid:
        return None
    parts = []
    for d in valid:
        label = f"{d.atc_name or d.name} (ATC {d.atc_class})" if d.atc_class else f"{d.name} (RxCUI {d.rxcui})"
        parts.append(f"{label}: {d.ndc_count} NDCs and {len(d.names)} drug names")
    return "; ".join(parts)


def expert_note(c: CriterionOut) -> str | None:
    if c.partial:
        return f"Filtered on the drug alone. {oneline(c.reason_not_codable)}".strip()
    if c.approximate:
        return f"The timing is approximated: {oneline(c.window_text)}"
    return None


def rule_comment(c: CriterionOut) -> list[str]:
    lines = [f"{c.id} · {SECTION.get(c.section, c.section)} · {REQUIREMENT.get(c.requirement, c.requirement)} · {c.type}",
             f'Criteria: "{oneline(c.source_text)}"',
             f"Why: {oneline(c.rationale)}"]
    if text := codes_text(c):
        lines.append(f"Codes: {text}")
    if text := drugs_text(c):
        lines.append(f"Drugs: {text}")
    lines.append(f"When: {oneline(c.window_text)}")
    if note := expert_note(c):
        lines.append(f"EXPERT CHECK: {note}")
    return lines


def where_block(blocks: list[tuple[list[str], str]]) -> str:
    """WHERE with each condition under its comment lines, joined with AND."""
    parts = []
    for i, (comment, condition) in enumerate(blocks):
        lines = [f"  -- {oneline(line)}" for line in comment]
        lines.append(f"  {'AND ' if i else ''}{indent(condition)}")
        parts.append("\n".join(lines))
    return "WHERE\n" + "\n\n".join(parts)


def build_review(demographics: DemographicFilter, criteria: CriteriaFilter | None) -> ReviewFilter:
    age, sex = demographics.age, demographics.sex
    blocks: list[tuple[list[str], str]] = []
    applied: list[ReviewItem] = []
    expert: list[ReviewItem] = []

    # Step 1: age and sex, sourced from ClinicalTrials.gov's structured fields
    if age.applies:
        source = f"ClinicalTrials.gov minimumAge {quoted(age.minimum.raw)}, maximumAge {quoted(age.maximum.raw)}"
        blocks.append(([f"Step 1 · Age: {age.rule}", f"Source: {source}"], age.sql))
        applied.append(ReviewItem(id="AGE", source_text=source, section=None, requirement=None, note=age.rule))
    if sex.applies:
        source = f"ClinicalTrials.gov sex {quoted(sex.raw)}"
        comment = [f"Step 1 · Sex: {sex.rule}", f"Source: {source}"]
        if sex.gender_based:
            note = ("Eligibility is based on gender identity, and claims record sex. "
                    "An expert reviews these members.")
            comment.append(f"EXPERT CHECK: {note}")
            expert.append(ReviewItem(id="SEX", source_text=source, section=None, requirement=None, note=note))
        blocks.append((comment, sex.sql))
        applied.append(ReviewItem(id="SEX", source_text=source, section=None, requirement=None, note=sex.rule))

    # Step 2: one condition per rule that claims can check
    rules = criteria.criteria if criteria else []
    for c in rules:
        if c.group == "codable" and c.sql:
            blocks.append((rule_comment(c), c.sql))
            item = dict(id=c.id, source_text=c.source_text, section=c.section, requirement=c.requirement)
            applied.append(ReviewItem(**item, note=oneline(c.rationale)))
            if note := expert_note(c):
                expert.append(ReviewItem(**item, note=note))

    not_applied = [ReviewItem(id=c.id, source_text=c.source_text, section=c.section, requirement=c.requirement,
                              note=oneline(c.reason_not_codable) or "Claims can't answer this.")
                   for c in rules if c.group == "needs_review"]
    flagged = [ReviewItem(id=c.id, source_text=c.source_text, section=c.section, requirement=c.requirement,
                          note=" ".join(c.problems)) for c in rules if c.group == "flagged"]
    covered = [ReviewItem(id=c.id, source_text=c.source_text, section=c.section, requirement=c.requirement,
                          note="Restates the age or sex limits, which step 1 applies.")
               for c in rules if c.group == "covered"]

    warnings = list(demographics.warnings)
    if not criteria:
        warnings.insert(0, "Step 2 (eligibility criteria) hasn't been built for this trial yet, so this "
                           "query applies age and sex only.")

    ref = criteria.reference_date if criteria else demographics.reference_date
    header = [f"Trial {demographics.trial_id}: {oneline(demographics.title)}",
              f"Eligibility filters from Trails-POC. Dates are measured from {ref.isoformat()}.",
              "Each condition quotes the eligibility text or ClinicalTrials.gov field it came from."]
    if criteria:
        header.append(f"Step 2 rules were read by {criteria.model} and checked against UMLS and RxNorm.")
    else:
        header.append("Step 2 hasn't been built yet: only age and sex are applied.")
    if expert:
        header.append(f"{len(expert)} condition{'s' if len(expert) != 1 else ''} marked EXPERT CHECK: "
                      "an expert confirms the members it returns.")
    body = (f"SELECT member_id\nFROM {MEMBERS}\n" + where_block(blocks)) if blocks else \
        f"SELECT member_id\nFROM {MEMBERS}  -- no condition can be checked from claims: everyone passes"
    footer = []
    for items, title in ((not_applied, "Not in this query: claims can't answer these, so a person checks them. They remove no one."),
                         (flagged, "Left out after failing a check: review these before relying on this query.")):
        if items:
            footer.append(title)
            footer += [f'{i.id} · {SECTION.get(i.section, i.section)} · "{oneline(i.source_text)}": {i.note}' for i in items]
    if covered:
        footer.append("Covered by step 1 (age and sex):")
        footer += [f'{i.id} · "{oneline(i.source_text)}"' for i in covered]
    query = "\n".join(f"-- {oneline(line)}" for line in header) + "\n" + body
    # The ; goes on its own line: a condition may end with a -- comment, which would swallow it
    sql = query + "\n;"
    if footer:
        sql += "\n\n" + "\n".join(f"-- {oneline(line)}" for line in footer)

    return ReviewFilter(
        trial_id=demographics.trial_id, title=demographics.title, status=demographics.status, reference_date=ref,
        criteria_built=criteria is not None, model=criteria.model if criteria else None, applied=applied,
        expert_checks=expert, not_applied=not_applied, flagged=flagged, covered=covered, warnings=warnings, sql=sql,
        query=query,
    )


# ---------- running it: the count and the member list wrap the query unchanged ----------

def count_sql(review: ReviewFilter) -> str:
    # The query may end with a -- comment, so the closing parenthesis goes on its own line
    return f"SELECT COUNT(*) AS member_count FROM (\n{review.query}\n) AS eligible"


def members_sql(review: ReviewFilter, limit: int) -> str:
    cols = ", ".join(f"m.{c}" for c in MEMBER_LIST_COLUMNS)
    # One more row than the limit, to tell whether the list was cut off
    return (f"SELECT {cols}\nFROM {MEMBERS} AS m\nWHERE m.member_id IN (\n{review.query}\n)\n"
            f"ORDER BY m.member_id\nLIMIT {int(limit) + 1}")

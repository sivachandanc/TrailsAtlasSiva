"""Derived values for the single-trial dashboard. Pure functions, no I/O."""

import re
from collections import Counter
from datetime import date, datetime, timezone

from webapp.backend import config
from webapp.backend.models import (
    Count, Criterion, Eligibility, Milestone, Site, SiteStateCount, TrialDashboard, TrialDetail, TrialKpis,
)
from webapp.backend.services.summary import US, label

# A heading is "inclusion/exclusion criteria" near the start of a line, optionally after a short
# group label and optionally followed by content on the same line:
#   "Inclusion Criteria:", "DIP - Exclusion Criteria:", "PP CARE Inclusion criteria: Enrolled ..."
HEADING = re.compile(r"^(.{0,40}?)\b(?:key\s+)?(inclusion|exclusion)\s+criteria\b\s*(?::\s*(.*))?$", re.I)
BULLET = re.compile(r"^(\s*)(?:[*\-•·]|\d{1,2}[.)]|[a-z][.)])\s+(.*)$", re.I)
# ClinicalTrials.gov text is markdown-escaped, e.g. "\\>= 18" for ">= 18"
ESCAPE = re.compile(r"\\([\\`*_{}\[\]()#+\-.!<>|~])")


def unescape(text: str) -> str:
    return ESCAPE.sub(r"\1", text).strip()


def parse_eligibility(text: str | None) -> Eligibility:
    """Split criteria text into inclusion and exclusion lists. Falls back to the raw text
    (parsed=False) when there are no recognizable headings or no items under them."""
    if not text or not text.strip():
        return Eligibility(inclusion=[], exclusion=[], parsed=False, raw_text=None)
    sections: dict[str, list[Criterion]] = {"inclusion": [], "exclusion": []}
    current: str | None = None
    for raw_line in text.splitlines():
        if not raw_line.strip():
            continue
        bullet = BULLET.match(raw_line)
        heading = None if bullet else HEADING.match(raw_line.strip())
        if heading:
            current = heading.group(2).lower()
            if rest := (heading.group(3) or "").strip():
                sections[current].append(Criterion(text=unescape(rest), level=0))
            continue
        if current is None:
            continue  # text before the first heading (e.g. a study-group title)
        if bullet:
            indent, body = bullet.groups()
            level = 1 if len(indent.expandtabs(4)) >= 2 else 0
            sections[current].append(Criterion(text=unescape(body), level=level))
        else:
            sections[current].append(Criterion(text=unescape(raw_line), level=0))
    parsed = bool(sections["inclusion"] or sections["exclusion"])
    return Eligibility(inclusion=sections["inclusion"], exclusion=sections["exclusion"],
                       parsed=parsed, raw_text=unescape(text))


def to_date(value: str | None) -> date | None:
    """ClinicalTrials.gov dates: '2027-12-01' or partial '2027-12' (taken as the 1st)."""
    if not value:
        return None
    try:
        parts = [int(p) for p in value.split("-")]
        return date(parts[0], parts[1] if len(parts) > 1 else 1, parts[2] if len(parts) > 2 else 1)
    except (ValueError, IndexError):
        return None


def months_between(a: date, b: date) -> int:
    return (b.year - a.year) * 12 + (b.month - a.month)


def plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def timeline(t: TrialDetail, today: date) -> tuple[list[Milestone], float | None, str | None]:
    points = [("First posted", t.first_posted), ("Start", t.start_date),
              ("Primary completion", t.primary_completion_date), ("Completion", t.completion_date)]
    dated = [d for _, v in points if (d := to_date(v))]
    if not dated:
        return [Milestone(label=lbl, date=v, position=None) for lbl, v in points], None, None
    lo, hi = min(dated + [today]), max(dated + [today])
    span = (hi - lo).days or 1
    pos = lambda d: round((d - lo).days / span, 4)  # noqa: E731
    milestones = [Milestone(label=lbl, date=v, position=pos(d) if (d := to_date(v)) else None) for lbl, v in points]

    notes = []
    start, primary = to_date(t.start_date), to_date(t.primary_completion_date)
    if start:
        m = months_between(start, today)
        notes.append(f"Started {plural(m, 'month')} ago" if m > 0 else
                     "Started this month" if m == 0 else f"Starts in {plural(-m, 'month')}")
    if primary:
        m = months_between(today, primary)
        notes.append(f"primary completion expected in {plural(m, 'month')}" if m > 0 else
                     "primary completion expected this month" if m == 0 else
                     f"primary completion was {plural(-m, 'month')} ago")
    note = ", ".join(notes)
    return milestones, pos(today), (note[:1].upper() + note[1:]) if note else None


def age_text(min_age: str | None, max_age: str | None) -> str:
    lo, hi = (min_age or "").lower(), (max_age or "").lower()
    if lo and hi:
        lo_n, lo_u = (lo.split(" ", 1) + [""])[:2]
        hi_n, hi_u = (hi.split(" ", 1) + [""])[:2]
        return f"{lo_n} to {hi}" if lo_u == hi_u else f"{lo} to {hi}"
    if lo:
        return f"{lo} and over"
    if hi:
        return f"Up to {hi}"
    return "Any age"


def summarize_trial(t: TrialDetail, *, source: str, today: date | None = None) -> TrialDashboard:
    today = today or date.today()
    is_open = t.status in config.OPEN_STATUSES

    def site_is_open(s: Site) -> bool:
        # Sites without their own status inherit the trial's: open only if the trial is
        return s.status in config.OPEN_STATUSES or (s.status is None and is_open)

    us_sites = [s for s in t.sites if s.country == US]
    open_us = [s for s in us_sites if site_is_open(s)]
    state_total = Counter(s.state for s in us_sites if s.state)
    state_open = Counter(s.state for s in open_us if s.state)
    countries = Counter(s.country for s in t.sites if s.country)

    banners = []
    if not is_open:
        banners.append(f"This trial is {label(t.status or 'UNKNOWN').lower()}, so it isn't enrolling new participants.")
    if not us_sites:
        banners.append("This trial has no sites in the United States.")
    elif is_open and not open_us:
        banners.append("None of this trial's US sites are listed as open.")

    milestones, today_pos, note = timeline(t, today)
    sex = {"ALL": "Any", "FEMALE": "Female", "MALE": "Male"}.get(t.sex or "", "Not stated")

    return TrialDashboard(
        source=source,
        fetched_at=datetime.now(timezone.utc),
        trial=t,
        status_label=label(t.status or "UNKNOWN"),
        phase_labels=[label(p) for p in t.phases],
        is_open=is_open,
        banners=banners,
        kpis=TrialKpis(
            enrollment=t.enrollment, sites=len(t.sites), us_sites=len(us_sites), open_us_sites=len(open_us),
            states=len(state_total), countries=len(countries), ages=age_text(t.min_age, t.max_age), sex=sex,
        ),
        by_state=[SiteStateCount(state=s, sites=n, open_sites=state_open[s]) for s, n in state_total.most_common()],
        other_countries=[Count(label=c, count=n) for c, n in countries.most_common() if c != US],
        timeline=milestones,
        today_position=today_pos,
        timeline_note=note,
        eligibility=parse_eligibility(t.eligibility_criteria),
    )

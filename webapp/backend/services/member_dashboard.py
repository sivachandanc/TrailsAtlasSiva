"""The member dashboard: what step 3's query finds on the warehouse, and how near the trial's sites it is.

Three aggregate statements run on Databricks (no names or IDs come back):
  - funnel: members left after each condition of the query, in order;
  - demographics: eligible members by whole years of age and sex;
  - geography: eligible members by ZIP code and state.
The server then places each ZIP at its US Census centroid and measures the straight-line distance to
each trial site. Distances are to the trial's open US sites; if none is open, to all its US sites.
Pure functions, no I/O.
"""

import math
from collections import Counter, defaultdict
from datetime import datetime

from webapp.backend import geo
from webapp.backend.agents.member_schema import MEMBERS
from webapp.backend.models import (
    AgeBand, Count, DashboardKpis, DashboardSite, DashboardZip, DistanceBand, FunnelStep, MemberDashboard,
    ReviewFilter, Site, StateMembers, TrialDashboard,
)
from webapp.backend.services.review import indent, oneline
from webapp.backend.services.summary import US, label
from webapp.backend.services.trial_summary import site_is_open

# (label, upper bound in miles); a distance on a bound falls in the lower band
DISTANCE_BANDS = [("Under 10 mi", 10), ("10–25 mi", 25), ("25–50 mi", 50), ("50–100 mi", 100),
                  ("100–250 mi", 250), ("250 mi or more", math.inf)]
AGE_BAND_YEARS = 5
OLDEST_BAND = 90  # ages from here up share one band ("90+")
SEX_LABELS = {"F": "Female", "M": "Male"}


# ---------- the statements ----------

def wrap(review: ReviewFilter) -> str:
    # The query may end with a -- comment, so the closing parenthesis goes on its own line
    return f"WHERE m.member_id IN (\n{review.query}\n)"


def funnel_sql(review: ReviewFilter) -> str:
    """Members left after each condition. Each step filters the one before it, so every condition is
    written once, exactly as in the query; the last step is the query's result."""
    ctes = [f"step_0 AS (\n  SELECT * FROM {MEMBERS}\n)"]
    for k, c in enumerate(review.conditions, start=1):
        ctes.append(f"step_{k} AS (\n  -- {oneline(c.label)}\n  SELECT * FROM step_{k - 1}\n"
                    f"  WHERE {indent(c.sql, '    ')}\n)")
    counts = [f"SELECT {k} AS step, COUNT(*) AS members FROM step_{k}" for k in range(len(review.conditions) + 1)]
    return (f"-- Trial {review.trial_id}: members left after each condition of the step 3 query\n"
            "WITH " + ",\n".join(ctes) + "\n" + "\nUNION ALL\n".join(counts))


def demographics_sql(review: ReviewFilter) -> str:
    # Age in whole years on the query's reference date, the same day its age limits are measured from
    return (f"SELECT CAST(FLOOR(MONTHS_BETWEEN(DATE '{review.reference_date.isoformat()}', m.birth_dte) / 12) AS INT)"
            " AS age_years,\n       m.gender_cde AS sex, COUNT(*) AS members\n"
            f"FROM {MEMBERS} AS m\n{wrap(review)}\nGROUP BY 1, 2")


def geography_sql(review: ReviewFilter) -> str:
    return ("SELECT m.zip_5_postal_cde AS zip, m.state_or_province_cde AS state, COUNT(*) AS members\n"
            f"FROM {MEMBERS} AS m\n{wrap(review)}\nGROUP BY 1, 2")


# ---------- proximity ----------

class Proximity:
    """A trial's US sites placed on the map, and distances from a point to them."""

    def __init__(self, dashboard: TrialDashboard):
        trial = dashboard.trial
        self.sites: list[DashboardSite] = []
        self._points: list[tuple[int, float, float, float]] = []  # (index, lat rad, lon rad, cos lat)
        for s in (s for s in trial.sites if s.country == US):
            point, how = locate(s)
            site = DashboardSite(
                index=len(self.sites), facility=s.facility, city=s.city, state=s.state, zip=s.zip, status=s.status,
                status_label=label(s.status) if s.status else f"{label(trial.status or 'UNKNOWN')} (trial status)",
                open=site_is_open(s, dashboard.is_open), lat=point[0] if point else None,
                lon=point[1] if point else None, located_by=how, contacts=s.contacts, within={}, catchment=0)
            self.sites.append(site)
            if point:
                lat, lon = map(math.radians, point)
                self._points.append((site.index, lat, lon, math.cos(lat)))
        located = {i for i, *_ in self._points}
        open_located = {s.index for s in self.sites if s.open and s.index in located}
        # Distances are to open sites; with none, to every located site (and the page says so)
        self.distance_to = "open" if open_located else "all"
        self.targets = open_located or located

    def distances(self, point: geo.Point) -> list[tuple[int, float]]:
        """(site index, miles) to every located site."""
        lat, lon = map(math.radians, point)
        cos_lat = math.cos(lat)
        out = []
        for i, slat, slon, scos in self._points:
            h = math.sin((slat - lat) / 2) ** 2 + cos_lat * scos * math.sin((slon - lon) / 2) ** 2
            out.append((i, 2 * geo.EARTH_RADIUS_MILES * math.asin(math.sqrt(min(1.0, h)))))
        return out

    def nearest(self, point: geo.Point | None) -> tuple[int, float] | None:
        if not point:
            return None
        return min(((i, d) for i, d in self.distances(point) if i in self.targets), key=lambda x: x[1], default=None)


def locate(s: Site) -> tuple[geo.Point | None, str]:
    if s.lat is not None and s.lon is not None:
        return (s.lat, s.lon), "geopoint"
    if point := geo.zip_point(s.zip):
        return point, "zip"
    return None, "none"


# ---------- summarizing ----------

def number(value) -> int:
    return int(value) if value not in (None, "") else 0


def age_bands(demo_rows: list[list]) -> tuple[list[AgeBand], float | None]:
    by_age: dict[int, Counter] = defaultdict(Counter)
    for age, sex, n in demo_rows:
        if age in (None, ""):
            continue
        by_age[int(age)][SEX_LABELS.get((sex or "").strip().upper(), "Other")] += number(n)
    if not by_age:
        return [], None
    band_of = lambda a: min(max(a, 0) // AGE_BAND_YEARS * AGE_BAND_YEARS, OLDEST_BAND)  # noqa: E731
    lo, hi = band_of(min(by_age)), band_of(max(by_age))
    totals: dict[int, Counter] = {b: Counter() for b in range(lo, hi + 1, AGE_BAND_YEARS)}
    for age, counts in by_age.items():
        totals[band_of(age)].update(counts)
    bands = [AgeBand(label=f"{b}+" if b == OLDEST_BAND else f"{b}–{b + AGE_BAND_YEARS - 1}",
                     female=c["Female"], male=c["Male"], other=c["Other"]) for b, c in totals.items()]
    return bands, weighted_median([(a, sum(c.values())) for a, c in by_age.items()])


def weighted_median(pairs: list[tuple[float, int]]) -> float | None:
    pairs = sorted(p for p in pairs if p[1] > 0)
    total = sum(n for _, n in pairs)
    if not total:
        return None
    seen = 0
    for value, n in pairs:
        seen += n
        if seen * 2 >= total:
            return value
    return pairs[-1][0]


def summarize(dashboard: TrialDashboard, review: ReviewFilter, *, funnel_rows: list[list], demo_rows: list[list],
              geo_rows: list[list], radii: tuple[int, ...], ran_at: datetime, statements: dict[str, str],
              elapsed_ms: int) -> MemberDashboard:
    trial = dashboard.trial

    # Funnel: step 0 is every member, then one step per condition
    counts = {number(step): number(n) for step, n in funnel_rows}
    funnel, before = [], None
    steps = [("ALL", "All members", False)] + [(c.id, c.label, c.expert) for c in review.conditions]
    for k, (cid, text, expert) in enumerate(steps):
        n = counts.get(k, 0)
        funnel.append(FunnelStep(id=cid, label=text, members=n, dropped=(before - n) if before is not None else 0,
                                 expert=expert))
        before = n
    all_members, eligible = funnel[0].members, funnel[-1].members

    bands, median_age = age_bands(demo_rows)
    sex = Counter()
    for _, s, n in demo_rows:
        sex[SEX_LABELS.get((s or "").strip().upper(), "Other or not recorded")] += number(n)

    # Geography: one point per ZIP, measured to the sites
    prox = Proximity(dashboard)
    by_zip: dict[str, Counter] = defaultdict(Counter)  # zip -> state -> members
    unknown = 0
    for z, state, n in geo_rows:
        n = number(n)
        z5 = geo.normalize_zip(z)
        if z5 and geo.zip_point(z5):
            by_zip[z5][geo.state_name(state)] += n
        else:
            unknown += n

    within = {str(r): 0 for r in radii}
    site_within = {s.index: {str(r): 0 for r in radii} for s in prox.sites}
    catchment = Counter()
    nearest_miles: list[tuple[float, int]] = []
    zips = []
    for z5, states in by_zip.items():
        n = sum(states.values())
        point = geo.zip_point(z5)
        dists = prox.distances(point)
        for i, d in dists:
            for r in radii:
                if d <= r:
                    site_within[i][str(r)] += n
        best = min(((i, d) for i, d in dists if i in prox.targets), key=lambda x: x[1], default=None)
        if best:
            catchment[best[0]] += n
            nearest_miles.append((best[1], n))
            for r in radii:
                if best[1] <= r:
                    within[str(r)] += n
        zips.append(DashboardZip(zip=z5, state=states.most_common(1)[0][0], lat=point[0], lon=point[1], members=n,
                                 nearest_site=best[0] if best else None,
                                 miles=round(best[1], 1) if best else None))
    zips.sort(key=lambda z: -z.members)
    for s in prox.sites:
        s.within = site_within[s.index]
        s.catchment = catchment[s.index]

    # Distance bands over members with a known distance; members without one are "Unknown"
    measured = sum(n for _, n in nearest_miles)
    band_counts = Counter()
    for d, n in nearest_miles:
        band_counts[next(text for text, upper in DISTANCE_BANDS if d <= upper)] += n
    distance_bands, running = [], 0
    for text, _ in DISTANCE_BANDS:
        running += band_counts[text]
        distance_bands.append(DistanceBand(label=text, members=band_counts[text],
                                           cumulative_share=running / measured if measured else None))
    no_distance = eligible - measured
    if no_distance > 0:
        distance_bands.append(DistanceBand(label="Unknown", members=no_distance, cumulative_share=None))

    # Members and open sites by state
    members_by_state = Counter()
    for states in by_zip.values():
        members_by_state.update(states)
    open_by_state = Counter(s.state for s in prox.sites if s.open and s.state)
    by_state = sorted((StateMembers(state=st, members=members_by_state[st], open_sites=open_by_state[st])
                       for st in set(members_by_state) | set(open_by_state) if st),
                      key=lambda x: (-x.members, -x.open_sites, x.state))

    banners = list(review.warnings)
    if review.expert_checks:
        n = len(review.expert_checks)
        banners.append(f"{n} condition{'s' if n != 1 else ''} in the query need{'' if n != 1 else 's'} an expert "
                       "check: an expert confirms the members it returns.")
    if review.not_applied:
        n = len(review.not_applied)
        banners.append(f"{n} rule{'s' if n != 1 else ''} can't be checked from claims, so these members still need "
                       "a person to check them.")
    if not prox.sites:
        banners.append("This trial has no sites in the United States, so distances can't be measured.")
    elif not prox.targets:
        banners.append("None of this trial's US sites has a location, so distances can't be measured.")
    elif prox.distance_to == "all":
        banners.append("None of this trial's US sites is listed as recruiting, so distances are to every US site.")
    if unknown:
        banners.append(f"{unknown:,} member{'s' if unknown != 1 else ''} have a ZIP code that isn't in the Census ZIP "
                       "list (blank, PO box or not a US ZIP), so their distance is unknown.")
    unlocated = sum(1 for s in prox.sites if s.located_by == "none")
    if unlocated:
        banners.append(f"{unlocated} US site{'s have' if unlocated != 1 else ' has'} no location on "
                       "ClinicalTrials.gov, so it isn't on the map or in the distances.")

    return MemberDashboard(
        trial_id=trial.id, title=trial.title, status_label=dashboard.status_label, phase_labels=dashboard.phase_labels,
        conditions=trial.conditions, reference_date=review.reference_date, criteria_built=review.criteria_built,
        model=review.model, ran_at=ran_at, radii=list(radii), distance_to=prox.distance_to,
        kpis=DashboardKpis(
            eligible=eligible, all_members=all_members, enrollment=trial.enrollment, us_sites=len(prox.sites),
            open_us_sites=sum(1 for s in prox.sites if s.open), within=within,
            median_miles=round(m, 1) if (m := weighted_median(nearest_miles)) is not None else None,
            located=measured, unknown_location=unknown, expert_checks=len(review.expert_checks),
            not_applied=len(review.not_applied), flagged=len(review.flagged)),
        banners=banners, funnel=funnel, age_bands=bands, median_age=median_age,
        sex=[Count(label=k, count=v) for k, v in sex.most_common() if v],
        distance_bands=distance_bands, sites=prox.sites, zips=zips, by_state=by_state,
        statements=statements, elapsed_ms=elapsed_ms,
    )


def member_distances(prox: Proximity, zip_value) -> tuple[str | None, str | None]:
    """The nearest site's name and the distance in miles, for one row of the member list."""
    best = prox.nearest(geo.zip_point(zip_value))
    if not best:
        return None, None
    site = prox.sites[best[0]]
    name = ", ".join(p for p in (site.facility, site.city, geo.state_name(site.state)) if p)
    return name or f"Site {best[0] + 1}", f"{best[1]:.1f}"

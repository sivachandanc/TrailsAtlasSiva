# Member dashboard

**Page:** `/trials/{id}/members` (`members.html`, `js/members.js`) ·
**Endpoints:** `POST …/filters/review/dashboard`, `POST …/filters/review/members` ·
**Code:** `services/member_dashboard.py`, `geo/` (ZIP centroids and distances)

You open it with **Open member dashboard** in step 3's *Run on Databricks* panel. It runs the
step 3 query on the Databricks warehouse and shows:
- how many members match,
- how each condition narrowed them,
- who they are,
- how far they live from each of the trial's sites, recruiting or not.

## What runs, and what comes back

The browser sends only the trial ID. The server rebuilds the step 3 query (`review_for()` in
`routes.py`) and runs three **aggregate** statements at once (`asyncio.gather`):

| Statement | Built by | Returns |
|---|---|---|
| **Funnel** | `funnel_sql()` | Members left after each condition of the query, in order |
| **Demographics** | `demographics_sql()` | Eligible members by whole years of age and `gender_cde` |
| **Geography** | `geography_sql()` | Eligible members by `zip_5_postal_cde` and `state_or_province_cde` |

None of them selects a name or member ID. Names reach the page only when you press **Load member
list**, which uses the existing `…/review/members` endpoint. The response is `Cache-Control: no-store`, and
nothing is cached or logged.

### The funnel
`build_review()` (`services/review.py`) records each WHERE condition as a `ReviewCondition`:
- an `id` (`AGE`, `SEX` or the rule's ID, e.g. `C3`),
- a short `label`,
- the `sql`, exactly as in the query,
- `expert`.

The funnel chains them as CTEs, so each condition is written once:

```sql
-- Trial NCT…: members left after each condition of the step 3 query
WITH step_0 AS (
  SELECT * FROM members
),
step_1 AS (
  -- Age: 3 to 29 years
  SELECT * FROM step_0
  WHERE birth_dte <= DATE '2023-10-27'  -- at least 3 years old
    AND birth_dte > DATE '1996-10-27'  -- at most 29 years old
),
step_2 AS (
  -- C1 · Type 2 diabetes
  SELECT * FROM step_1
  WHERE member_id IN (
      SELECT inferred_member_id FROM mx_claims …
    )
)
SELECT 0 AS step, COUNT(*) AS members FROM step_0
UNION ALL
SELECT 1 AS step, COUNT(*) AS members FROM step_1
UNION ALL
SELECT 2 AS step, COUNT(*) AS members FROM step_2
```

The last step is exactly the query's result, so it equals what **Count members** used to report.
The order is the query's order: age, sex, then step 2's rules. A step's drop therefore depends on
the steps before it. It is "removed at this point", not "removed by this rule alone".

### Demographics and geography
Both wrap the query unchanged, as the member list does:

```sql
SELECT CAST(FLOOR(MONTHS_BETWEEN(DATE '<reference date>', m.birth_dte) / 12) AS INT) AS age_years,
       m.gender_cde AS sex, COUNT(*) AS members
FROM members AS m
WHERE m.member_id IN (
<the step 3 query>
)
GROUP BY 1, 2
```

Age is measured on the query's reference date, the same day its age limits use. The geography
statement groups by ZIP and state in the same way.

All three were checked in local PySpark 3.5.3. The funnel's last step matched the query's own
count.

## Distances

### Where members are
Members only have a 5-digit ZIP code. Each ZIP is placed at the internal point of its **US Census
ZCTA** (ZIP Code Tabulation Area):
- **Data:** `webapp/backend/geo/zip_centroids.csv` holds ~33.8k rows from the 2024 Gazetteer and ships with the app.
- **Regenerating it:** `uv run python -m webapp.backend.geo.build_zip_centroids [year]`.
- **ZIP clean-up:** `normalize_zip()` handles ZIPs with a lost leading zero (`2139` → `02139`), ZIP+4 and numbers.
- **ZIPs not found:** a ZIP that isn't in the file (blank, PO-box-only, not a US ZIP) is counted as **location unknown**, and is never guessed. A banner gives the count.

### Where sites are
Only US sites count, because members are in the US. Each site is placed:
1. by its ClinicalTrials.gov `geoPoint`; or
2. by its own ZIP's centroid (marked "Placed by ZIP"); or
3. nowhere: "no location", listed but not measured. A banner gives the count.

A site is **open** when its status is RECRUITING or NOT_YET_RECRUITING. A site with no status
of its own is open when the trial is (`site_is_open()` in `trial_summary.py`, shared with the
trial page).

### What's measured
- **Distance** is a straight line (haversine, miles) from the ZIP's point to the site, not driving
  distance.
- **The nearest site** is the nearest open site. If the trial has no open US site, it's the nearest of all US sites,
  and a banner says so (`distance_to: "all"`).
- **Within N miles** of a site: counted for every site, open or not. For a site that isn't
  recruiting, it's who would be in reach "if it opens".
- **Catchment:** the members whose nearest open site is this one. Each member belongs to one site.
- **Radii** are 10, 25, 50 and 100 mi (`DASHBOARD_RADII_MILES` in `config.py`).
- **Distance bands:** under 10, 10–25, 25–50, 50–100, 100–250 and 250+ mi, plus Unknown.

`Proximity` in `member_dashboard.py` holds the placed sites and does the measuring. It's also used
for the member list's `nearest_site` and `distance_miles` columns.

**Cost:** every ZIP is measured to every site. The worst case is all ~33.8k US ZIPs × 97 sites,
which takes ~1.6 s. It runs off the event loop (`asyncio.to_thread`).

## The page, top to bottom

1. **Header:** the trial ID, status, phase and title, and when the query ran. Buttons: **Run again**,
   **View the trial** and **Review the query** (step 3).
2. **Banners:**
   - step 2 not built,
   - expert checks and rules not in the query,
   - no open or located sites,
   - members with an unknown ZIP.
3. **KPIs:**
   - eligible members (% of all members);
   - eligible ÷ enrollment target;
   - open US sites;
   - within 25 mi of an open site;
   - median distance to the nearest site;
   - rules a person checks.
4. **Map (Leaflet 1.9.4):**
   - The basemap is Esri's light or dark grey canvas, following the system theme; it needs no key. Its small credit line (Esri, OpenStreetMap) is required by their terms; the "Leaflet" credit is removed.
   - Member ZIP bubbles are sized by count and colored by distance band.
   - Open sites are filled green dots. Sites that aren't recruiting are dashed hollow dots.
   - A ring toggle (Off, 10, 25, 50, 100 mi) draws rings around open sites.
   - A site popup shows its address, status, members within each radius, catchment, PI and public contact.
   - It frames the lower 48 states. **Sites** and **Members** re-frame the map.
   - Scroll-zoom turns on after you click the map.
5. **Distance to the nearest site:** bars per band, with a cumulative-share line.
6. **How the query narrows the members:** one bar per funnel step, with the count and the drop.
   Expert-check steps are striped and tagged.
7. **Who they are:** the female/male split bar, and age in 5-year bands (90+ shares one band) stacked by sex, with the median age.
8. **Recruitment potential by site:** a sortable table showing the status, members within each radius
   and catchment (with a mini bar).
   - By default, open sites come first, ranked by catchment.
   - A text filter and the state chip narrow it.
   - Clicking a row (or pressing Enter) shows the site on the map.
9. **Members by state:** the shared tile map (`renderTileMap`). Clicking a state filters the site table.
10. **What the count does and doesn't check:** counts of conditions in the query, expert checks, rules not in the
    query and flagged rules, linking to step 3. **Show exactly what ran** shows the three statements.
11. **Member list (on demand):** up to `MEMBER_LIST_LIMIT` members, with their nearest open site and
    distance.
    - **Within** filters the list by distance.
    - **Download CSV** saves the filtered rows. The file is built in the browser.

## Response shape (`MemberDashboard`)

`trial_id`, `title`, `status_label`, `phase_labels`, `conditions`, `reference_date`,
`criteria_built`, `model`, `ran_at`, `radii`, `distance_to` (`open` | `all`), plus:

| Field | Contents |
|---|---|
| `kpis` | `eligible`, `all_members`, `enrollment`, `us_sites`, `open_us_sites`, `within` (radius → members), `median_miles`, `located`, `unknown_location`, `expert_checks`, `not_applied`, `flagged` |
| `banners` | the review's warnings plus the dashboard's own |
| `funnel[]` | `id`, `label`, `members`, `dropped`, `expert` |
| `age_bands[]`, `median_age` | `label`, `female`, `male`, `other` |
| `sex[]` | `label`, `count` |
| `distance_bands[]` | `label`, `members`, `cumulative_share` (of members with a known distance) |
| `sites[]` | `index`, `facility`, `city`, `state`, `zip`, `status`, `status_label`, `open`, `lat`, `lon`, `located_by` (`geopoint` / `zip` / `none`), `contacts`, `within`, `catchment` |
| `zips[]` | `zip`, `state`, `lat`, `lon`, `members`, `nearest_site`, `miles` (largest first) |
| `by_state[]` | `state` (full name), `members`, `open_sites` |
| `statements`, `elapsed_ms` | what ran, and the slowest statement's time |

**ZIP-level counts** (ZIP code and number of eligible members) reach the browser. Names and IDs don't.

## Limits

- Distances are straight lines from the ZIP's center. A member in a large rural ZIP may be miles
  from that point, and road distance is longer.
- ZCTAs aren't exactly ZIP codes: PO-box-only and some business ZIPs have none, so they count as unknown.
- `state_or_province_cde` is read as a code (`OH`) or a name. Anything else is shown as stored.
- Only US sites are measured.
- Each run uses warehouse compute: three statements, each scanning the member tables.

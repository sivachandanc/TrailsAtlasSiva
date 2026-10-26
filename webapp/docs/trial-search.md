# Finding trials: home, search and trial dashboards

## The trial source (`sources/`)

### Interface (`sources/base.py`)
Every registry implements `TrialSource`:

| Member | Meaning |
|---|---|
| `id`, `label`, `countries` | e.g. `clinicaltrials_gov`, `ClinicalTrials.gov`, `["United States"]` |
| `search(condition, country)` | open trials → `(list[Trial], total the source reports)` |
| `get_trial(trial_id)` | one trial in any status → `TrialDetail`; raises `TrialNotFound` |
| `count_open(country)` | number of open trials with a site in the country |

Errors: `SourceError` (unreachable or bad response) and `TrialNotFound`. `NCT_ID` is the regex
`^NCT\d{8}$`. To add a registry: implement the protocol and add it to `build_sources()` in
`sources/__init__.py`.

### ClinicalTrials.gov adapter (`sources/clinicaltrials_gov.py`)
Uses the public API v2 (no key).

**Field mapping.** `TRIAL_FIELDS` maps each `Trial` field to an API field name (requested with
`fields=`) and a key path into the study JSON, e.g. `min_age` →
`protocolSection.eligibilityModule.minimumAge`. `get_path()` follows a path, and on a list applies
the rest of the path to each item (so `interventions.type` gives one type per intervention).
`DETAIL_FIELDS` and `DETAIL_LISTS` add the single-trial fields: titles, summary, sponsor,
collaborators, keywords, MeSH IDs, dates, **eligibility criteria**, study population,
`genderBased`/`genderDescription`, arms, interventions, outcomes, contacts and officials.

**Search** (`search`):
- `query.cond=<condition>`
- `filter.overallStatus=RECRUITING,NOT_YET_RECRUITING`
- `filter.advanced=AREA[LocationCountry]"United States"` (site countries only; `query.locn`
  would also match other text)
- `sort=@relevance`, `countTotal=true`, `pageSize=1000`
- Follows `nextPageToken` until there are no more pages or `MAX_TRIALS` (10,000) is reached.
  `totalCount` comes only on the first page. A capped result keeps the most relevant trials.

**One trial** (`get_trial`): `GET /studies/{id}` with all detail fields. HTTP 400 or 404 →
`TrialNotFound`.

**Count** (`count_open`): the open filters with `pageSize=1` and `countTotal=true`.

## Search dashboard (`services/summary.py`)

`summarize(trials, source, country, condition, total_matching)` builds a `SearchResult`:

| Output | How it's computed |
|---|---|
| Open US site | `country == "United States"` and status open **or missing** (many sites have no status of their own; inside an open trial they're treated as open) |
| `kpis.open_trials` | trials returned (`shown`) |
| `kpis.recruiting` / `not_yet_recruiting` | by trial status |
| `kpis.open_us_sites` | open US sites across all trials |
| `kpis.states`, `kpis.cities` | distinct states / (city, state) with an open site |
| `kpis.median_enrollment` | median of trials with an enrollment number |
| `kpis.industry_share` | share of trials whose lead sponsor class is `INDUSTRY` |
| `by_state` | per state: trials with an open site there, and open sites |
| `top_cities` | top 15 (city, state) by trials |
| `top_conditions` | top 15; NLM MeSH terms if the trial has them, else the sponsor's text |
| `phases` | in order Early phase 1 → Phase 4 → No phase |
| `study_types`, `purposes`, `intervention_types` | counts, most common first |
| `sponsor_classes` | with their own labels (`OTHER` = "Academic and other") |
| `sex`, `age_groups` | Any / Female / Male; Children / Adults / Older adults |
| `starts_by_month` | 24 months back to 6 months ahead (31 months) |
| `completions_by_month` | primary completions over the next 12 months |
| `trials` | one row per trial: status, phases, top 3 conditions, open US sites, states, enrollment, dates, link |
| `capped` | fewer trials shown than the source reported |

`label()` turns codes into readable text; unknown codes become sentence case
(`SUPPORTIVE_CARE` → "Supportive care").

## Single-trial dashboard (`services/trial_summary.py`)

`summarize_trial(trial, source)` builds a `TrialDashboard`:

- **Open?** `status` in `OPEN_STATUSES`. A site with no status inherits the trial's.
- **Banners** (plain-language warnings): the trial isn't enrolling; no US sites; no US site is open.
- **KPIs:** enrollment, all sites, US sites, open US sites, states, countries, ages (`age_text`,
  e.g. "18 to 75 years", "18 years and over", "Up to 17 years", "Any age"), sex.
- **Sites by state** (all and open), **other countries**.
- **Timeline** (`timeline()`): first posted, start, primary completion and completion placed on a
  0–1 bar with today's position, plus a note such as "Started 14 months ago, primary completion
  expected in 9 months". Partial dates like `2027-05` count as the 1st.
- **Eligibility** (`parse_eligibility()`): splits the criteria text into inclusion and exclusion
  lists.
  - A heading is "inclusion/exclusion criteria" near the start of a line, optionally after a short
    group label ("DIP - Exclusion Criteria:") or followed by content on the same line.
  - Bullets: `*`, `-`, `•`, `·`, `1.`, `1)`, `a.`; indented bullets become level 1.
  - Text before the first heading is skipped. ClinicalTrials.gov's markdown escapes are removed
    (`\>=` → `>=`).
  - With no recognizable headings, `parsed=false` and the page shows the raw text.

## Pages

- **Home** (`/`, `home.js`): search box with two modes (condition or trial ID), a decorative state
  tile map, and the live open-trial count (`/api/stats/open-trials`; hidden if it fails).
  `/?mode=nct` opens in trial-ID mode.
- **Search** (`/search?condition=…`, `search.js`): loading skeleton, then KPIs, a US tile map
  (click a state to filter the table), top cities, conditions, phase, who can join, timing charts,
  interventions and sponsors, and a sortable, filterable trials table rendered 100 rows at a time
  as you scroll. Empty and error states explain what happened.
- **Trial** (`/trials/{id}`, `trial.js`): header with status, phases and conditions; banners;
  KPIs; about; timeline; where it's running (tile map); site status; who can join (parsed
  criteria); what's tested (arms, interventions); what's measured (outcomes); conditions and
  keywords; contacts; a filterable sites table; and a **Build eligibility filters** button that
  opens `/trials/{id}/eligibility`.

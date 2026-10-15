"""Settings for the web app."""

import os

from dotenv import load_dotenv

load_dotenv()  # reads .env at the repo root

CTGOV_BASE_URL = "https://clinicaltrials.gov/api/v2"
HTTP_TIMEOUT_SECONDS = 60

# Statuses of trials that can still enroll people; fixed for now
OPEN_STATUSES = ("RECRUITING", "NOT_YET_RECRUITING")

# A search stops after this many trials (sorted by relevance) to keep response times sane.
# "cancer" returns ~7,700 open US trials (~6 s), so this cap is rarely hit.
MAX_TRIALS = 10_000
PAGE_SIZE = 1_000

# Search results are kept in memory (never on disk) for this long
CACHE_TTL_SECONDS = 30 * 60

# UMLS (US National Library of Medicine) for MeSH -> CUI -> ICD-10-CM lookups.
# The key comes from .env (UMLS_API_KEY=...); it's never sent to the browser.
UMLS_BASE_URL = "https://uts-ws.nlm.nih.gov/rest"
UMLS_API_KEY = os.environ.get("UMLS_API_KEY") or None
# Terminology changes rarely, so lookups are kept in memory for a day
UMLS_CACHE_TTL_SECONDS = 24 * 60 * 60
UMLS_MAX_CONCURRENT = 8  # stay well under UMLS's request rate limit
ICD_SOURCE = "ICD10CM"

# The eligibility-criteria agent (LangGraph + Claude). The key comes from .env (ANTHROPIC_API_KEY=...).
CRITERIA_MODEL = "claude-opus-5-5"
CRITERIA_EFFORT = "high"
ANTHROPIC_API_KEY_SET = bool(os.environ.get("ANTHROPIC_API_KEY"))

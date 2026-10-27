"""Settings for the web app."""

import os
from pathlib import Path

from dotenv import load_dotenv

# .env at the repo root wins over variables already set in the shell or by Windows/macOS, so the
# app always runs with what's in the file. A blank value (KEY=) also overrides, and counts as unset.
ENV_FILE = Path(__file__).resolve().parents[2] / ".env"
load_dotenv(ENV_FILE, override=True)

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
# Optional overrides in .env: CRITERIA_MODEL and ANTHROPIC_BASE_URL (unset = defaults below).
CRITERIA_MODEL = os.environ.get("CRITERIA_MODEL") or "claude-opus-5-5"
CRITERIA_EFFORT = "high"
ANTHROPIC_API_KEY_SET = bool(os.environ.get("ANTHROPIC_API_KEY"))
ANTHROPIC_BASE_URL = os.environ.get("ANTHROPIC_BASE_URL") or None
# When a model call fails in a way waiting can fix (connection dropped or refused, timeout, 408, 409,
# 425, 429, 5xx), wait this long and try again, up to this many times. Some company endpoints and
# gateways refuse requests that arrive too soon after the last one.
CRITERIA_RETRIES = int(os.environ.get("CRITERIA_RETRIES") or 3)
CRITERIA_RETRY_SECONDS = float(os.environ.get("CRITERIA_RETRY_SECONDS") or 5)

# The member tables the filter SQL runs against (Databricks / Spark SQL). Override the names in .env.
MEMBERS_TABLE = os.environ.get("MEMBERS_TABLE") or "members"
MX_CLAIMS_TABLE = os.environ.get("MX_CLAIMS_TABLE") or "mx_claims"
RX_CLAIMS_TABLE = os.environ.get("RX_CLAIMS_TABLE") or "rx_claims"

# Optional: a Databricks SQL warehouse to run the step 3 query on (member count and list).
# All three of host, token and warehouse are needed; the warehouse can be given as its ID or as
# the HTTP path from the warehouse's connection details (/sql/1.0/warehouses/<id>). Results are
# sent to the browser only; nothing is stored, cached or logged.
DATABRICKS_HOST = os.environ.get("DATABRICKS_HOST") or None  # e.g. https://adb-123.azuredatabricks.net
DATABRICKS_TOKEN = os.environ.get("DATABRICKS_TOKEN") or None
DATABRICKS_WAREHOUSE_ID = (os.environ.get("DATABRICKS_WAREHOUSE_ID")
                           or (os.environ.get("DATABRICKS_HTTP_PATH") or "").rstrip("/").rsplit("/", 1)[-1] or None)
DATABRICKS_CATALOG = os.environ.get("DATABRICKS_CATALOG") or None  # default catalog for unqualified table names
DATABRICKS_SCHEMA = os.environ.get("DATABRICKS_SCHEMA") or None
DATABRICKS_TIMEOUT_SECONDS = 120  # longer than this and the statement is cancelled
MEMBER_LIST_LIMIT = int(os.environ.get("MEMBER_LIST_LIMIT") or 1000)
# The member dashboard counts eligible members within these straight-line distances (miles) of each site
DASHBOARD_RADII_MILES = (10, 25, 50, 100)

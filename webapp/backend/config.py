"""Settings for the web app."""

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

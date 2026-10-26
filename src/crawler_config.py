"""Shared constants of the arXiv crawler (Milestone 1)."""
import os

# Endpoints. Downloads must target the export mirror, never arxiv.org.
ARXIV_SRC_URL = "https://export.arxiv.org/src/{versioned_id}"
S2_PAPER_URL = "https://api.semanticscholar.org/graph/v1/paper/arXiv:{arxiv_id}"
S2_BATCH_URL = "https://api.semanticscholar.org/graph/v1/paper/batch"   # POST {"ids": ["ARXIV:..."]}
S2_FIELDS = ",".join([
    "references.title",
    "references.authors",
    "references.year",
    "references.publicationDate",
    "references.venue",
    "references.externalIds",
])

# Set the env var CRAWLER_USER_AGENT (see README) or edit the fallback below with a real contact email.
USER_AGENT = os.environ.get(
    "CRAWLER_USER_AGENT",
    "IntroDS-arXiv-crawler/1.0 (student project; contact: your_email@example.com)",
)

# ---- Pacing -------------------------------------------------------------------------------
# Base gaps (never go below them). arXiv asks for >= 3 s between calls.
ARXIV_DELAY_SECONDS = 3.0
S2_DELAY_WITH_KEY_SECONDS = 1.1      # 1 request/second with a free API key
S2_DELAY_NO_KEY_SECONDS = 3.1        # 100 requests / 5 minutes without a key
# Adaptive pacing: every throttle (HTTP 429/403) widens the gap by INTERVAL_GROWTH up to the
# maximum, every success shrinks it back by INTERVAL_DECAY until the base gap is reached again.
ARXIV_MAX_INTERVAL_SECONDS = 12.0
S2_MAX_INTERVAL_SECONDS = 15.0
INTERVAL_GROWTH = 1.3
INTERVAL_DECAY = 0.97

# ---- Error handling -----------------------------------------------------------------------
# Transient errors (network drop, HTTP 5xx, truncated body): short exponential backoff.
MAX_RETRIES = 4
BACKOFF_BASE_SECONDS = 3.0
# Throttling (HTTP 429/403, HTML block page): the WHOLE host is paused (shared by all threads).
# Cooldown = Retry-After if the server sends it, else 10, 20, 40, ... s capped at the maximum.
THROTTLE_COOLDOWN_BASE_SECONDS = 10.0
THROTTLE_COOLDOWN_MAX_SECONDS = 300.0
MAX_THROTTLE_RETRIES = 8             # per request, before it is queued for the next pass
HARD_BLOCK_THROTTLES = 10            # consecutive throttles without ONE success -> stop (resumable)
MAX_CONSECUTIVE_FAILURES = 15        # consecutive failed versions (e.g. no network) -> stop (resumable)
MAX_TOTAL_DOWNLOAD_ATTEMPTS = 80     # hard cap; only attempts WITHOUT progress count toward MAX_RETRIES

# Failed items are queued and retried automatically in later passes of the SAME run.
MAX_PASSES = 3
PASS_COOLDOWN_SECONDS = 60.0         # pass k waits (k-1) * this before it starts

REQUEST_TIMEOUT_SECONDS = 90
LOOKUP_BATCH_SIZE = 50               # ids per arXiv API call
DISCOVERY_BLOCK = 250                # ids per discovery checkpoint (discovery.json is saved per block)
DISCOVERY_CLIENT_RETRIES = 2         # arxiv.py internal retries; our own cooldown handles 429 above that

# Semantic Scholar: one POST resolves many papers (requests/s limit then stops being the bottleneck).
S2_USE_BATCH = True
S2_BATCH_SIZE = 50

# arXiv source downloads in parallel. All workers share ONE rate limiter, so the request rate
# is unchanged; workers only overlap transfer time. arXiv etiquette says one connection at a
# time, so keep 1 unless you measured a gain without throttling (see README).
DOWNLOAD_WORKERS = 1

KEPT_EXTENSIONS = (".tex", ".bib")   # the only file types the corpus keeps
MAX_MEMBER_BYTES = 100 * 1024 * 1024 # guard against decompression bombs

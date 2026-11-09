"""Shared constants of the arXiv crawler (Milestone 1)."""
import os

# Endpoints. Downloads must target the export mirror, never arxiv.org.
ARXIV_SRC_URL = "https://export.arxiv.org/src/{versioned_id}"
S2_PAPER_URL = "https://api.semanticscholar.org/graph/v1/paper/arXiv:{arxiv_id}"
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

# Politeness: arXiv asks for >= 3 s between calls and one connection at a time.
ARXIV_DELAY_SECONDS = 3.0
S2_DELAY_WITH_KEY_SECONDS = 2      # 1 request/second with a free API key
S2_DELAY_NO_KEY_SECONDS = 3.1        # 100 requests / 5 minutes without a key

MAX_RETRIES = 4
BACKOFF_BASE_SECONDS = 5.0
REQUEST_TIMEOUT_SECONDS = 90
LOOKUP_BATCH_SIZE = 50               # ids per arXiv API call

KEPT_EXTENSIONS = (".tex", ".bib")   # the only file types the corpus keeps
MAX_MEMBER_BYTES = 100 * 1024 * 1024 # guard against decompression bombs

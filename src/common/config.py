"""Shared constants of the crawler. Override through environment variables."""
import os
from pathlib import Path

USER_AGENT = os.environ.get(
    "CRAWLER_USER_AGENT",
    "FIT-HCMUS-IntroDS-arxiv-crawler/1.0 (student project; contact: CHANGE_ME@example.com)",
)
ARXIV_DELAY_SECONDS = 3.0
S2_DELAY_SECONDS_WITH_KEY = 1.1
S2_DELAY_SECONDS_WITHOUT_KEY = 3.1  # 100 requests per 5 minutes
MAX_RETRIES = 4
RETRY_BACKOFF_BASE_SECONDS = 5.0
REQUEST_TIMEOUT_SECONDS = 90
DISCOVERY_BATCH_SIZE = 100
DEFAULT_DATA_DIR = Path(os.environ.get("DATA_DIR", "data"))
ARXIV_SOURCE_URL = "https://export.arxiv.org/src/{arxiv_id}v{version}"
S2_PAPER_URL = "https://api.semanticscholar.org/graph/v1/paper/arXiv:{arxiv_id}"
S2_FIELDS = (
    "title,references.paperId,references.title,references.authors,"
    "references.year,references.publicationDate,references.venue,"
    "references.externalIds"
)
FINAL_STATUSES = {"ok", "empty", "not_found"}  # anything else is retried on rerun

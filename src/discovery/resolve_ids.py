"""Stage 1: resolve assigned IDs into paper metadata and per-version dates via the arXiv API."""
import logging
import re
from datetime import timezone
from pathlib import Path

import arxiv

from ..common.config import ARXIV_DELAY_SECONDS, DISCOVERY_BATCH_SIZE
from ..common.paths import paper_dir, write_json

logger = logging.getLogger(__name__)
VERSIONED_ID_PATTERN = re.compile(r"^(?P<base>.+)v(?P<version>\d+)$")


def split_versioned_id(short_id: str):
    match = VERSIONED_ID_PATTERN.match(short_id)
    return match.group("base"), int(match.group("version"))


def to_iso(moment) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def chunked(items, size):
    for start in range(0, len(items), size):
        yield items[start:start + size]


def fetch_latest_records(client, arxiv_ids):
    """Base-ID lookup: the result id suffix is the latest version number."""
    search = arxiv.Search(id_list=list(arxiv_ids), max_results=len(arxiv_ids))
    records = {}
    for result in client.results(search):
        base, latest_version = split_versioned_id(result.get_short_id())
        records[base] = (latest_version, result)
    return records


def fetch_version_dates(client, versioned_ids):
    """Versioned-ID lookup: `updated` is expected to be the date of that version (verify on a sample)."""
    dates = {}
    for batch in chunked(versioned_ids, DISCOVERY_BATCH_SIZE):
        search = arxiv.Search(id_list=batch, max_results=len(batch))
        for result in client.results(search):
            dates[result.get_short_id()] = to_iso(result.updated)
    return dates


def discover(arxiv_ids, data_dir, log_dir="logs"):
    """Write data/<paper>/metadata.json for every ID not yet resolved. Returns failed IDs."""
    client = arxiv.Client(page_size=DISCOVERY_BATCH_SIZE, delay_seconds=ARXIV_DELAY_SECONDS, num_retries=4)
    pending = [i for i in arxiv_ids if not (paper_dir(data_dir, i) / "metadata.json").exists()]
    logger.info("discovery: %d ids, %d pending", len(arxiv_ids), len(pending))
    failed_ids = []
    for batch in chunked(pending, DISCOVERY_BATCH_SIZE):
        try:
            records = fetch_latest_records(client, batch)
            versioned = [f"{base}v{v}" for base, (n, _) in records.items() for v in range(2, n + 1)]
            version_dates = fetch_version_dates(client, versioned)
        except Exception as error:  # network or API error: leave batch pending for the next run
            logger.error("batch failed (%s): %s", type(error).__name__, error)
            failed_ids.extend(batch)
            continue
        for arxiv_id in batch:
            if arxiv_id not in records:
                logger.error("not returned by API: %s", arxiv_id)
                failed_ids.append(arxiv_id)
                continue
            num_versions, result = records[arxiv_id]
            submission_date = to_iso(result.published)
            dates_by_version = {"1": submission_date}
            for version in range(2, num_versions + 1):
                dates_by_version[str(version)] = version_dates.get(f"{arxiv_id}v{version}")
            write_json(paper_dir(data_dir, arxiv_id) / "metadata.json", {
                "arxiv_id": arxiv_id,
                "title": " ".join(result.title.split()),
                "authors": [author.name for author in result.authors],
                "submission_date": submission_date,
                "revised_dates": [dates_by_version[str(v)] for v in range(2, num_versions + 1)],
                "venue": result.journal_ref,
                "num_versions": num_versions,
                "version_dates": dates_by_version,
                "primary_category": result.primary_category,
                "categories": result.categories,
            })
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    (Path(log_dir) / "discovery_failures.txt").write_text("\n".join(failed_ids), encoding="utf-8")
    logger.info("discovery done, %d failed", len(failed_ids))
    return failed_ids

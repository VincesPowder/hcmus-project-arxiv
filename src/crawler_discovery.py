"""Entry discovery: resolve assigned ids into paper metadata and version lists
with the arXiv API (arxiv.py 4.x, `client.results(search)` style)."""
import logging
import re
import time

import arxiv

import crawler_config as config
from crawler_utils import chunk_list, split_versioned_id

LOGGER = logging.getLogger("crawler.discovery")
ENTRY_ID_PATTERN = re.compile(r"abs/(?P<versioned_id>.+)$")
HTTP_STATUS_PATTERN = re.compile(r"HTTP (\d{3})")


def make_client():
    """arxiv.Client with the mandatory 3 s delay and a single connection.

    Its own retries stay small: throttling is handled by `lookup_with_cooldown` below.
    """
    return arxiv.Client(page_size=config.LOOKUP_BATCH_SIZE,
                        delay_seconds=config.ARXIV_DELAY_SECONDS,
                        num_retries=config.DISCOVERY_CLIENT_RETRIES)


def result_to_record(result):
    """Convert an arxiv.Result into a JSON-friendly dict."""
    match = ENTRY_ID_PATTERN.search(result.entry_id)
    base_id, version = split_versioned_id(match.group("versioned_id"))
    return {
        "arxiv_id": base_id,
        "version": version,
        "title": " ".join(result.title.split()),
        "authors": [author.name for author in result.authors],
        "published": result.published.isoformat(),   # date of v1
        "updated": result.updated.isoformat(),       # date of THIS version
        "journal_ref": result.journal_ref or None,
        "doi": result.doi or None,
        "primary_category": result.primary_category,
        "categories": list(result.categories),
    }


def is_retryable_http_error(error):
    """True for arXiv API errors that waiting can cure: HTTP 403/429 and 5xx."""
    status = getattr(error, "status", None)
    if not isinstance(status, int):
        match = HTTP_STATUS_PATTERN.search(str(error))
        status = int(match.group(1)) if match else None
    return status in (403, 429, 500, 502, 503, 504)


def lookup_with_cooldown(client, id_list):
    """One API call; on HTTP 429/5xx wait an escalating cooldown and repeat the SAME call."""
    for attempt in range(config.MAX_THROTTLE_RETRIES + 1):
        try:
            search = arxiv.Search(id_list=id_list, max_results=len(id_list))
            return [result_to_record(result) for result in client.results(search)]
        except Exception as error:  # noqa: BLE001
            if not is_retryable_http_error(error) or attempt == config.MAX_THROTTLE_RETRIES:
                raise
            cooldown = min(config.THROTTLE_COOLDOWN_BASE_SECONDS * 2 ** attempt,
                           config.THROTTLE_COOLDOWN_MAX_SECONDS)
            LOGGER.warning("arXiv API %s -> cooling down %.0f s (attempt %d/%d)",
                           error, cooldown, attempt + 1, config.MAX_THROTTLE_RETRIES)
            time.sleep(cooldown)


def query_ids(client, id_list):
    """Query a batch of ids. Returns (records, failed_ids).

    A throttled lookup is NEVER split into one call per id (that would multiply the requests that
    caused the 429): it is reported as failed and retried by the caller in a later pass. Only a
    non-HTTP failure (typically one malformed id) falls back to one id per call to isolate it.
    """
    try:
        return lookup_with_cooldown(client, id_list), []
    except Exception as error:  # noqa: BLE001
        if is_retryable_http_error(error):
            LOGGER.error("Lookup of %d ids kept failing (%s); queued for a later pass", len(id_list), error)
            return [], list(id_list)
        if len(id_list) == 1:
            LOGGER.error("Lookup failed for %s: %s", id_list[0], error)
            return [], []     # unknown or malformed id: treated as not found
        LOGGER.warning("Batch lookup failed (%s); retrying one by one", error)
        records, failed = [], []
        for single_id in id_list:
            single_records, single_failed = query_ids(client, [single_id])
            records += single_records
            failed += single_failed
        return records, failed


def discover_papers(client, base_ids):
    """Resolve assigned base ids into {base_id: paper_info}.

    Step 1: lookup on the base id returns the latest version (suffix = version count).
    Step 2: query every older versioned id to get per-version metadata and dates.
    """
    latest_records, failed_bases = {}, set()
    for chunk in chunk_list(base_ids, config.LOOKUP_BATCH_SIZE):
        records, failed = query_ids(client, chunk)
        failed_bases.update(split_versioned_id(item)[0] for item in failed)
        for record in records:
            latest_records[record["arxiv_id"]] = record
    LOGGER.info("Resolved %d of %d assigned ids", len(latest_records), len(base_ids))

    older_ids = []
    for base_id, record in latest_records.items():
        older_ids += [f"{base_id}v{number}" for number in range(1, record["version"])]
    older_records = {}
    for chunk in chunk_list(older_ids, config.LOOKUP_BATCH_SIZE):
        records, failed = query_ids(client, chunk)
        failed_bases.update(split_versioned_id(item)[0] for item in failed)
        for record in records:
            older_records[(record["arxiv_id"], record["version"])] = record

    papers = {}
    for base_id in base_ids:
        latest = latest_records.get(base_id)
        if latest is None:
            papers[base_id] = {"arxiv_id": base_id, "found": False, "versions": [],
                               "discovery_failed": base_id in failed_bases}
            continue
        versions = []
        for number in range(1, latest["version"] + 1):
            record = latest if number == latest["version"] else older_records.get((base_id, number))
            if record is None:  # version metadata missing: keep the slot so the download still runs
                record = {"arxiv_id": base_id, "version": number, "title": None, "authors": [],
                          "published": latest["published"], "updated": None, "journal_ref": None,
                          "doi": None, "primary_category": None, "categories": []}
            versions.append(record)
        papers[base_id] = {
            "arxiv_id": base_id,
            "found": True,
            "discovery_failed": base_id in failed_bases,   # True: some version metadata is missing, redo later
            "latest_version": latest["version"],
            "title": latest["title"],
            "authors": latest["authors"],
            "submission_date": latest["published"],
            "revised_dates": [v["updated"] for v in versions[1:] if v["updated"]],
            "venue": latest["journal_ref"],
            "versions": versions,
        }
    return papers

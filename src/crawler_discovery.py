"""Entry discovery: resolve assigned ids into paper metadata and version lists
with the arXiv API (arxiv.py 4.x, `client.results(search)` style)."""
import logging
import re

import arxiv

import crawler_config as config
from crawler_utils import chunk_list, split_versioned_id

LOGGER = logging.getLogger("crawler.discovery")
ENTRY_ID_PATTERN = re.compile(r"abs/(?P<versioned_id>.+)$")


def make_client():
    """arxiv.Client with the mandatory 3 s delay and a single connection."""
    return arxiv.Client(page_size=config.LOOKUP_BATCH_SIZE,
                        delay_seconds=config.ARXIV_DELAY_SECONDS,
                        num_retries=config.MAX_RETRIES)


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


def query_ids(client, id_list):
    """Query a batch of ids; on failure fall back to one id per call to isolate bad ids."""
    records = []
    try:
        search = arxiv.Search(id_list=id_list, max_results=len(id_list))
        records = [result_to_record(result) for result in client.results(search)]
    except Exception as error:  # noqa: BLE001 - logged, then isolated below
        LOGGER.warning("Batch lookup failed (%s); retrying one by one", error)
        for single_id in id_list:
            try:
                search = arxiv.Search(id_list=[single_id], max_results=1)
                records.extend(result_to_record(r) for r in client.results(search))
            except Exception as single_error:  # noqa: BLE001
                LOGGER.error("Lookup failed for %s: %s", single_id, single_error)
    return records


def discover_papers(client, base_ids):
    """Resolve assigned base ids into {base_id: paper_info}.

    Step 1: lookup on the base id returns the latest version (suffix = version count).
    Step 2: query every older versioned id to get per-version metadata and dates.
    """
    latest_records = {}
    for chunk in chunk_list(base_ids, config.LOOKUP_BATCH_SIZE):
        for record in query_ids(client, chunk):
            latest_records[record["arxiv_id"]] = record
    LOGGER.info("Resolved %d of %d assigned ids", len(latest_records), len(base_ids))

    older_ids = []
    for base_id, record in latest_records.items():
        older_ids += [f"{base_id}v{number}" for number in range(1, record["version"])]
    older_records = {}
    for chunk in chunk_list(older_ids, config.LOOKUP_BATCH_SIZE):
        for record in query_ids(client, chunk):
            older_records[(record["arxiv_id"], record["version"])] = record

    papers = {}
    for base_id in base_ids:
        latest = latest_records.get(base_id)
        if latest is None:
            papers[base_id] = {"arxiv_id": base_id, "found": False, "versions": []}
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
            "latest_version": latest["version"],
            "title": latest["title"],
            "authors": latest["authors"],
            "submission_date": latest["published"],
            "revised_dates": [v["updated"] for v in versions[1:] if v["updated"]],
            "venue": latest["journal_ref"],
            "versions": versions,
        }
    return papers

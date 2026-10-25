"""Reference metadata crawling through the Semantic Scholar Graph API."""
import logging
import time

import config
from utils import PermanentRequestError, RateLimiter, read_json, request_with_retries, write_json

LOGGER = logging.getLogger("crawler.references")


def normalize_reference(raw_reference):
    """Flatten one Semantic Scholar reference into the fields we keep locally."""
    external_ids = raw_reference.get("externalIds") or {}
    authors = [author.get("name") for author in (raw_reference.get("authors") or [])
               if author.get("name")]
    return {
        "s2_id": raw_reference.get("paperId"),
        "title": raw_reference.get("title"),
        "authors": authors,
        "year": raw_reference.get("year"),
        "publication_date": raw_reference.get("publicationDate"),
        "venue": raw_reference.get("venue") or None,
        "arxiv_id": external_ids.get("ArXiv"),   # keep old ids such as hep-th/9901001 as-is
        "doi": external_ids.get("DOI"),
        "corpus_id": external_ids.get("CorpusId"),
    }


def fetch_references(session, limiter, arxiv_id, api_key=None):
    """Fetch and normalize all references of one publication.

    Returns (status, references, error) with status in {"ok", "not_found", "failed"}.
    """
    url = config.S2_PAPER_URL.format(arxiv_id=arxiv_id)
    headers = {"x-api-key": api_key} if api_key else None
    try:
        response = request_with_retries(session, limiter, url,
                                        params={"fields": config.S2_FIELDS},
                                        extra_headers=headers)
        payload = response.json()
    except PermanentRequestError as error:
        return "not_found", [], str(error)
    except Exception as error:  # noqa: BLE001 - recorded in the log, crawl continues
        return "failed", [], str(error)
    references = [normalize_reference(raw) for raw in (payload.get("references") or [])]
    return "ok", references, None


def crawl_all_references(session, arxiv_ids, corpus_dir, folder_of, api_key=None):
    """Crawl references for every id, resuming from references.json files already on disk.

    Returns one stats row per id.
    """
    delay = config.S2_DELAY_WITH_KEY_SECONDS if api_key else config.S2_DELAY_NO_KEY_SECONDS
    limiter = RateLimiter(delay)
    rows = []
    for arxiv_id in arxiv_ids:
        target = corpus_dir / folder_of(arxiv_id) / "references.json"
        started = time.perf_counter()
        if target.exists():  # resume
            stored = read_json(target)
            rows.append({"arxiv_id": arxiv_id, "refs_status": stored["status"],
                         "n_references": len(stored["references"]),
                         "n_references_with_arxiv": sum(1 for r in stored["references"] if r["arxiv_id"]),
                         "refs_seconds": stored.get("seconds", 0.0), "refs_error": stored.get("error")})
            continue
        status, references, error = fetch_references(session, limiter, arxiv_id, api_key)
        seconds = time.perf_counter() - started
        if status != "failed":  # 'failed' is retried on the next run
            write_json(target, {"arxiv_id": arxiv_id, "status": status, "error": error,
                                "seconds": seconds, "references": references})
        if status != "ok":
            LOGGER.warning("References of %s: %s (%s)", arxiv_id, status, error)
        rows.append({"arxiv_id": arxiv_id, "refs_status": status, "n_references": len(references),
                     "n_references_with_arxiv": sum(1 for r in references if r["arxiv_id"]),
                     "refs_seconds": seconds, "refs_error": error})
    return rows

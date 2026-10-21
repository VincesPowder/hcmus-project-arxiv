"""Stage 3: crawl reference metadata from Semantic Scholar (one call per paper)."""
import logging
import os
import time
from datetime import datetime, timezone

import requests

from ..common.config import (FINAL_STATUSES, S2_DELAY_SECONDS_WITHOUT_KEY, S2_DELAY_SECONDS_WITH_KEY,
                             S2_FIELDS, S2_PAPER_URL, USER_AGENT)
from ..common.http_utils import get_with_retry
from ..common.paths import paper_dir, read_json, write_json
from ..common.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)


def parse_reference(raw_reference: dict) -> dict:
    external_ids = raw_reference.get("externalIds") or {}
    return {
        "s2_id": raw_reference.get("paperId"),
        "title": raw_reference.get("title"),
        "authors": [a.get("name") for a in (raw_reference.get("authors") or []) if a.get("name")],
        "year": raw_reference.get("year"),
        "publication_date": raw_reference.get("publicationDate"),
        "venue": raw_reference.get("venue"),
        "arxiv_id": external_ids.get("ArXiv"),  # keep old ids such as hep-th/9901001 as returned
        "external_ids": external_ids,
    }


def crawl_references_for_paper(session, limiter, data_dir, arxiv_id: str, api_key=None) -> dict:
    output_path = paper_dir(data_dir, arxiv_id) / "references.json"
    if output_path.exists() and read_json(output_path).get("status") in FINAL_STATUSES:
        return read_json(output_path)
    started = time.perf_counter()
    headers = {"x-api-key": api_key} if api_key else None
    response, error = get_with_retry(session, limiter, S2_PAPER_URL.format(arxiv_id=arxiv_id),
                                     params={"fields": S2_FIELDS}, headers=headers)
    result = {"arxiv_id": arxiv_id, "status": "failed", "reason": error, "references": []}
    if response is not None and response.status_code == 200:
        references = [parse_reference(r) for r in (response.json().get("references") or [])]
        result.update(status="ok", reason=None, references=references)
    elif response is not None:
        result["reason"] = f"http_{response.status_code}"
        if response.status_code == 404:
            result["status"] = "not_found"
    result["num_references"] = len(result["references"])
    result["num_with_arxiv_id"] = sum(1 for r in result["references"] if r["arxiv_id"])
    result["seconds"] = round(time.perf_counter() - started, 2)
    result["fetched_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    write_json(output_path, result)
    if result["status"] == "failed":
        logger.error("references failed %s: %s", arxiv_id, result["reason"])
    return result


def crawl_all_references(arxiv_ids, data_dir):
    api_key = os.environ.get("S2_API_KEY")
    if not api_key:
        logger.warning("S2_API_KEY not set: falling back to the slow unauthenticated pool")
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    limiter = RateLimiter(S2_DELAY_SECONDS_WITH_KEY if api_key else S2_DELAY_SECONDS_WITHOUT_KEY)
    for index, arxiv_id in enumerate(arxiv_ids, start=1):
        if (paper_dir(data_dir, arxiv_id) / "metadata.json").exists():
            crawl_references_for_paper(session, limiter, data_dir, arxiv_id, api_key)
        if index % 100 == 0:
            logger.info("references progress %d/%d", index, len(arxiv_ids))

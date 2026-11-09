"""Reference metadata crawling through the Semantic Scholar Graph API."""
import logging
import time

import crawler_config as config
from crawler_utils import (AuthError, PermanentRequestError, StopRequested, read_json,
                           request_with_retries, write_json)

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


class ReferenceFetcher:
    """Semantic Scholar client: batch POST by default, one GET per paper as the fallback.

    Result of every fetch is (status, references, error) with status in {"ok", "not_found", "failed"}.
    "failed" is never written to disk, so the item is retried in the next pass / run.
    """

    def __init__(self, session, limiter, api_key=None):
        self.session, self.limiter, self.api_key = session, limiter, api_key
        self.batch_enabled = config.S2_USE_BATCH

    def _request(self, url, **kwargs):
        """Request with the API key; if S2 rejects the key, continue without it instead of failing."""
        headers = {"x-api-key": self.api_key} if self.api_key else None
        try:
            return request_with_retries(self.session, self.limiter, url, extra_headers=headers, **kwargs)
        except AuthError:
            LOGGER.error("Semantic Scholar rejected the API key: continuing WITHOUT a key (slower). "
                         "Check S2_API_KEY.")
            self.api_key = None
            self.limiter.set_base_interval(config.S2_DELAY_NO_KEY_SECONDS)
            return request_with_retries(self.session, self.limiter, url, extra_headers=None, **kwargs)

    def fetch_single(self, arxiv_id):
        """GET /paper/arXiv:{id} with the references fields."""
        try:
            response = self._request(config.S2_PAPER_URL.format(arxiv_id=arxiv_id),
                                     params={"fields": config.S2_FIELDS})
            payload = response.json()
        except PermanentRequestError as error:
            return "not_found", [], str(error)
        except StopRequested:
            raise
        except Exception as error:  # noqa: BLE001 - recorded in the log, crawl continues
            return "failed", [], str(error)
        return "ok", [normalize_reference(raw) for raw in (payload.get("references") or []) if raw], None

    def fetch_batch(self, arxiv_ids):
        """POST /paper/batch for many papers at once. Returns {id: result}, or None if the endpoint
        does not deliver references (then the caller falls back to single requests for good)."""
        try:
            response = self._request(config.S2_BATCH_URL, method="POST",
                                     params={"fields": config.S2_FIELDS},
                                     json_body={"ids": [f"ARXIV:{arxiv_id}" for arxiv_id in arxiv_ids]})
            payload = response.json()
        except PermanentRequestError:   # HTTP 400: one malformed id spoils the chunk -> isolate it
            return {arxiv_id: self.fetch_single(arxiv_id) for arxiv_id in arxiv_ids}
        except StopRequested:
            raise
        except Exception as error:  # noqa: BLE001
            return {arxiv_id: ("failed", [], str(error)) for arxiv_id in arxiv_ids}
        if not isinstance(payload, list) or len(payload) != len(arxiv_ids):
            return {arxiv_id: ("failed", [], "unexpected batch response shape") for arxiv_id in arxiv_ids}
        found = [item for item in payload if item is not None]
        if found and all("references" not in item for item in found):
            LOGGER.warning("Batch endpoint returned no 'references' field: using one request per paper.")
            self.batch_enabled = False
            return None
        results = {}
        for arxiv_id, item in zip(arxiv_ids, payload):
            if item is None:    # S2 returns null for papers it does not index
                results[arxiv_id] = ("not_found", [], "not indexed by Semantic Scholar")
            else:
                results[arxiv_id] = ("ok", [normalize_reference(raw) for raw in (item.get("references") or [])
                                            if raw], None)
        return results

    def fetch_chunk(self, arxiv_ids):
        """Fetch a chunk of ids: one batch request when possible, single requests otherwise."""
        if self.batch_enabled:
            results = self.fetch_batch(arxiv_ids)
            if results is not None:
                return results
        return {arxiv_id: self.fetch_single(arxiv_id) for arxiv_id in arxiv_ids}


def reference_row(arxiv_id, status, references, seconds, error):
    """Stats row of one paper (input of papers_log.csv)."""
    return {"arxiv_id": arxiv_id, "refs_status": status, "n_references": len(references),
            "n_references_with_arxiv": sum(1 for r in references if r["arxiv_id"]),
            "refs_seconds": seconds, "refs_error": error}


def crawl_all_references(session, arxiv_ids, corpus_dir, folder_of, stop_event, own_stop_event,
                         limiter, api_key=None):
    """Crawl references for every id, resuming from references.json files already on disk.

    Ids go to Semantic Scholar in batches of S2_BATCH_SIZE (one request instead of fifty), and ids
    that fail are retried in later passes of the same run. Returns one stats row per id.
    """
    fetcher = ReferenceFetcher(session, limiter, api_key)
    rows, pending = {}, []
    for arxiv_id in arxiv_ids:
        target = corpus_dir / folder_of(arxiv_id) / "references.json"
        if target.exists():  # resume
            stored = read_json(target)
            rows[arxiv_id] = reference_row(arxiv_id, stored["status"], stored["references"],
                                           stored.get("seconds", 0.0), stored.get("error"))
        else:
            pending.append(arxiv_id)

    def should_stop():
        return stop_event.is_set() or own_stop_event.is_set()

    try:
        for pass_number in range(1, config.MAX_PASSES + 1):
            if not pending or should_stop():
                break
            if pass_number > 1:
                cooldown = config.PASS_COOLDOWN_SECONDS * (pass_number - 1)
                LOGGER.warning("References pass %d/%d: retrying %d ids after a %.0f s cooldown",
                               pass_number, config.MAX_PASSES, len(pending), cooldown)
                if stop_event.wait(cooldown):
                    break
            failed, position = [], 0
            while position < len(pending) and not should_stop():
                size = config.S2_BATCH_SIZE if fetcher.batch_enabled else 1
                chunk = pending[position:position + size]
                position += size
                started = time.perf_counter()
                results = fetcher.fetch_chunk(chunk)
                seconds_each = (time.perf_counter() - started) / len(chunk)
                for arxiv_id in chunk:
                    status, references, error = results[arxiv_id]
                    seconds = seconds_each + (rows[arxiv_id]["refs_seconds"] if arxiv_id in rows else 0.0)
                    if status != "failed":
                        write_json(corpus_dir / folder_of(arxiv_id) / "references.json",
                                   {"arxiv_id": arxiv_id, "status": status, "error": error,
                                    "seconds": seconds, "references": references})
                    else:
                        failed.append(arxiv_id)
                        LOGGER.warning("References of %s: %s (%s)", arxiv_id, status, error)
                    rows[arxiv_id] = reference_row(arxiv_id, status, references, seconds, error)
                if limiter.hard_blocked():
                    LOGGER.error("STOP references: %d throttles in a row from Semantic Scholar. Only the "
                                 "references crawl stops; arXiv downloads continue. Check S2_API_KEY / wait, "
                                 "then rerun the same command to fill the missing references.",
                                 limiter.consecutive_throttles)
                    own_stop_event.set()
            pending = failed + pending[position:]
    except StopRequested:
        LOGGER.warning("References crawl interrupted; progress is saved.")
    return list(rows.values())

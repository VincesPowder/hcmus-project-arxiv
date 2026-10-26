"""Download every version of every discovered paper into the corpus folder."""
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import crawler_config as config
from crawler_download import download_source, extract_source, write_files
from crawler_utils import (PermanentRequestError, StopRequested, make_session, read_json,
                           to_folder_name, write_json)

LOGGER = logging.getLogger("crawler.crawl")


def crawl_version(session, limiter, version_info, version_dir):
    """Download one version, keep .tex/.bib only, write metadata.json (the 'done' marker).

    Returns a stats row, or None when the run was stopped before the download finished.
    Status: ok (TeX kept), no_tex (empty record), failed (not marked done, retried in a later pass).
    """
    marker = version_dir / "metadata.json"
    if marker.exists():  # resume
        return read_json(marker)["stats"]

    versioned_id = f"{version_info['arxiv_id']}v{version_info['version']}"
    started = time.perf_counter()
    stats = {"arxiv_id": version_info["arxiv_id"], "version": version_info["version"],
             "status": "failed", "source_kind": None, "archive_bytes": 0,
             "uncompressed_bytes": 0, "kept_bytes": 0, "n_tex": 0, "n_bib": 0,
             "seconds": 0.0, "attempts": 1, "error": None}
    try:
        raw_bytes = download_source(session, limiter, versioned_id)
        files, uncompressed_bytes, kind = extract_source(raw_bytes)
        version_dir.mkdir(parents=True, exist_ok=True)
        write_files(version_dir, files)
        stats.update(status="ok" if files else "no_tex", source_kind=kind,
                     archive_bytes=len(raw_bytes), uncompressed_bytes=uncompressed_bytes,
                     kept_bytes=sum(len(content) for content in files.values()),
                     n_tex=sum(1 for name in files if name.lower().endswith(".tex")),
                     n_bib=sum(1 for name in files if name.lower().endswith(".bib")))
    except StopRequested:
        return None
    except PermanentRequestError as error:   # e.g. 404: no source for this version
        stats.update(status="no_tex", source_kind="unavailable", error=str(error))
        version_dir.mkdir(parents=True, exist_ok=True)
    except Exception as error:  # noqa: BLE001 - logged and retried in the next pass / run
        stats["error"] = str(error)
        LOGGER.error("Download failed for %s: %s", versioned_id, error)
    stats["seconds"] = time.perf_counter() - started
    if stats["status"] != "failed":
        write_json(marker, {"metadata": version_info, "stats": stats,
                            "files": sorted(p.relative_to(version_dir).as_posix()
                                            for p in version_dir.rglob("*") if p.is_file()
                                            and p.name != "metadata.json")})
    return stats


def crawl_all_sources(papers, corpus_dir, stop_event, limiter, workers=None, max_passes=None):
    """Crawl all versions of all found papers; returns one stats row per version.

    * One rate limiter (`limiter`) paces every worker, so `workers` > 1 only overlaps transfer time.
    * Failed versions are queued and retried in up to `max_passes` passes of the same run, each
      preceded by a longer cooldown, so one run normally ends with a complete corpus.
    * The run stops itself (progress is kept, rerun to resume) when the host keeps throttling
      (limiter.hard_blocked()) or MAX_CONSECUTIVE_FAILURES versions fail in a row.
    """
    workers = workers or config.DOWNLOAD_WORKERS
    max_passes = max_passes or config.MAX_PASSES
    tasks = []
    for paper in papers.values():
        if not paper["found"]:
            continue
        paper_dir = corpus_dir / to_folder_name(paper["arxiv_id"])
        write_json(paper_dir / "paper.json", {key: paper[key] for key in (
            "arxiv_id", "title", "authors", "submission_date", "revised_dates",
            "venue", "latest_version", "versions")})
        tasks += [(info, paper_dir / f"v{info['version']}") for info in paper["versions"]]

    final, lock, state = {}, threading.Lock(), {"consecutive_failures": 0}
    local = threading.local()

    def work(task):
        if stop_event.is_set():
            return
        version_info, version_dir = task
        if not hasattr(local, "session"):
            local.session = make_session()
        stats = crawl_version(local.session, limiter, version_info, version_dir)
        if stats is None:
            return
        key = (version_info["arxiv_id"], version_info["version"])
        with lock:
            earlier = final.get(key)
            if earlier:   # time of the failed earlier attempts belongs to this version too
                stats["seconds"] += earlier["seconds"]
                stats["attempts"] = earlier.get("attempts", 1) + 1
            final[key] = stats
            failed = stats["status"] == "failed"
            state["consecutive_failures"] = state["consecutive_failures"] + 1 if failed else 0
            if (limiter.hard_blocked() or state["consecutive_failures"] >= config.MAX_CONSECUTIVE_FAILURES) \
                    and not stop_event.is_set():
                LOGGER.error("STOP: %d consecutive failures, %d consecutive throttles on %s. Progress is "
                             "saved: wait 10-15 minutes, then rerun the SAME command to resume.",
                             state["consecutive_failures"], limiter.consecutive_throttles, limiter.name)
                stop_event.set()

    try:
        for pass_number in range(1, max_passes + 1):
            pending = [task for task in tasks
                       if pass_number == 1 or final.get(
                           (task[0]["arxiv_id"], task[0]["version"]), {}).get("status") == "failed"]
            if not pending or stop_event.is_set():
                break
            if pass_number > 1:
                cooldown = config.PASS_COOLDOWN_SECONDS * (pass_number - 1)
                LOGGER.warning("Pass %d/%d: retrying %d failed versions after a %.0f s cooldown",
                               pass_number, max_passes, len(pending), cooldown)
                if stop_event.wait(cooldown):
                    break
            state["consecutive_failures"] = 0
            if workers == 1:
                for task in pending:
                    work(task)
            else:
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    list(pool.map(work, pending))
    except KeyboardInterrupt:   # Ctrl+C: keep what is done, let the other worker stop cleanly
        LOGGER.warning("Interrupted by user; saving partial results.")
        stop_event.set()
    return list(final.values())

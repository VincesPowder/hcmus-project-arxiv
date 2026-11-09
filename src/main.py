"""End-to-end arXiv crawler for Milestone 1.

Examples:
    python src/main.py --ids-file group_ids.txt      # whole group in one run (ids and/or A..B ranges)
    python src/main.py --range 2310.00001 2310.00050
"""
import argparse
import logging
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

import crawler_config as config
from crawler_crawl import crawl_all_sources
from crawler_discovery import discover_papers, make_client
from crawler_monitor import ResourceMonitor
from crawler_references import crawl_all_references
from crawler_tables import build_tables
from crawler_utils import (AdaptiveRateLimiter, chunk_list, generate_id_range, make_session,
                           read_id_file, read_json, to_folder_name, write_json)


def parse_arguments():
    """Parse command-line options."""
    parser = argparse.ArgumentParser(description="arXiv crawler (Milestone 1)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--ids-file",
                       help="text file, one entry per line: an arXiv id or an inclusive range A..B "
                            "(merge every member's range here to crawl the whole group in one run)")
    group.add_argument("--range", nargs=2, metavar=("FIRST", "LAST"),
                       help="inclusive id range inside one month, e.g. 2310.00001 2310.00050")
    parser.add_argument("--corpus-dir", default="corpus", help="crawled .tex/.bib files go here")
    parser.add_argument("--work-dir", default="work",
                        help="logs, metrics and tables are written here")
    parser.add_argument("--skip-references", action="store_true",
                        help="do not call Semantic Scholar now (rerun without this flag later to fill references)")
    parser.add_argument("--limit", type=int, default=None, help="only crawl the first N ids (smoke test)")
    parser.add_argument("--workers", type=int, default=config.DOWNLOAD_WORKERS,
                        help="parallel arXiv download threads sharing ONE rate limiter (default 1; "
                             "benchmark before raising it)")
    parser.add_argument("--passes", type=int, default=config.MAX_PASSES,
                        help="how many times failed items are retried inside the same run")
    return parser.parse_args()


def get_s2_api_key():
    """Read the Semantic Scholar key from env var or Colab secrets (optional)."""
    key = os.environ.get("S2_API_KEY")
    if key:
        return key
    try:
        from google.colab import userdata  # only exists on Colab
        return userdata.get("S2_API_KEY")
    except Exception:  # noqa: BLE001 - no Colab or no secret set
        return None


def directory_size_bytes(path):
    """Total size of all files below path (like `du -sb`)."""
    return sum(file.stat().st_size for file in Path(path).rglob("*") if file.is_file())


def main():
    arguments = parse_arguments()
    corpus_dir, work_dir = Path(arguments.corpus_dir), Path(arguments.work_dir)
    corpus_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        handlers=[logging.FileHandler(work_dir / "crawl.log", encoding="utf-8"), logging.StreamHandler()])

    base_ids = (read_id_file(arguments.ids_file) if arguments.ids_file
                else generate_id_range(*arguments.range))
    if arguments.limit:
        base_ids = base_ids[:arguments.limit]
    api_key = get_s2_api_key()
    logging.info("Assigned papers: %d | Semantic Scholar key: %s", len(base_ids), bool(api_key))

    monitor = ResourceMonitor(corpus_dir)
    monitor.start()
    wall_started = time.perf_counter()

    # Phase 1: entry discovery (arXiv API, sequential, 3 s pacing inside arxiv.Client).
    # Saved block by block; a rerun only redoes ids that are missing or whose lookup failed.
    discovery_file, discovery_time_file = work_dir / "discovery.json", work_dir / "discovery_seconds.json"
    cached = read_json(discovery_file) if discovery_file.exists() else {}
    discovery_seconds = read_json(discovery_time_file)["seconds"] if discovery_time_file.exists() else 0.0
    client = None
    for pass_number in range(1, arguments.passes + 1):
        missing = [base_id for base_id in base_ids
                   if base_id not in cached or cached[base_id].get("discovery_failed")]
        if not missing:
            break
        cooldown = config.PASS_COOLDOWN_SECONDS * (pass_number - 1)
        if cooldown:
            logging.warning("Discovery pass %d/%d: %d ids to redo after a %.0f s cooldown",
                            pass_number, arguments.passes, len(missing), cooldown)
        block_started = time.perf_counter()
        try:
            time.sleep(cooldown)
            client = client or make_client()
            for block in chunk_list(missing, config.DISCOVERY_BLOCK):
                cached.update(discover_papers(client, block))
                discovery_seconds += time.perf_counter() - block_started
                block_started = time.perf_counter()
                write_json(discovery_file, cached)
                write_json(discovery_time_file, {"seconds": discovery_seconds})
                logging.info("Discovery: %d of %d ids resolved", sum(1 for i in base_ids if i in cached),
                             len(base_ids))
        except KeyboardInterrupt:
            logging.warning("Interrupted during discovery; progress is saved, rerun to resume.")
            sys.exit(1)
    papers = {base_id: cached[base_id] for base_id in base_ids if base_id in cached}
    unresolved = [base_id for base_id in base_ids if base_id not in papers or papers[base_id].get("discovery_failed")]
    if unresolved:
        logging.error("%d ids stay (partly) unresolved after %d passes, e.g. %s. Rerun later to fill them.",
                      len(unresolved), arguments.passes, unresolved[:3])
    papers = {base_id: papers.get(base_id, {"arxiv_id": base_id, "found": False, "versions": [],
                                            "discovery_failed": True}) for base_id in base_ids}

    # Phase 2: sources (export.arxiv.org) and references (Semantic Scholar) run in parallel,
    # because they hit different hosts with independent rate limits. Each host has ONE adaptive
    # limiter shared by all its threads: a 429 pauses that host for everybody and widens its gap.
    found_ids = [paper_id for paper_id, paper in papers.items() if paper["found"]]
    stop_event = threading.Event()              # arXiv blocked / Ctrl+C: stop everything
    references_stop_event = threading.Event()   # Semantic Scholar trouble: stop references only
    arxiv_limiter = AdaptiveRateLimiter("arxiv", config.ARXIV_DELAY_SECONDS,
                                        config.ARXIV_MAX_INTERVAL_SECONDS, stop_event)
    s2_limiter = AdaptiveRateLimiter("s2", config.S2_DELAY_WITH_KEY_SECONDS if api_key
                                     else config.S2_DELAY_NO_KEY_SECONDS,
                                     config.S2_MAX_INTERVAL_SECONDS, stop_event)
    with ThreadPoolExecutor(max_workers=1) as pool:
        reference_future = pool.submit(crawl_all_references, make_session(),
                                       [] if arguments.skip_references else found_ids,
                                       corpus_dir, to_folder_name, stop_event, references_stop_event,
                                       s2_limiter, api_key)
        try:
            version_rows = crawl_all_sources(papers, corpus_dir, stop_event, arxiv_limiter,
                                             arguments.workers, arguments.passes)
        except KeyboardInterrupt:   # Ctrl+C outside the crawl loop: stop both workers cleanly
            stop_event.set()
            version_rows = []
        try:
            reference_rows = reference_future.result()
        except KeyboardInterrupt:
            stop_event.set()
            reference_rows = reference_future.result()

    papers_table, references_table = build_tables(corpus_dir, work_dir / "tables")
    wall_seconds = time.perf_counter() - wall_started
    resources = monitor.stop()

    # Logs for the report
    versions_log = pd.DataFrame(version_rows)
    references_log = pd.DataFrame(reference_rows)
    paper_rows = []
    for paper_id, paper in papers.items():
        paper_versions = versions_log[versions_log["arxiv_id"] == paper_id] if len(versions_log) else versions_log
        reference_row = (references_log[references_log["arxiv_id"] == paper_id]
                         if len(references_log) else references_log)
        row = {"arxiv_id": paper_id, "found": paper["found"],
               "n_versions": len(paper["versions"]),
               "versions_with_tex": int((paper_versions["status"] == "ok").sum()) if len(paper_versions) else 0,
               "versions_empty": int((paper_versions["status"] == "no_tex").sum()) if len(paper_versions) else 0,
               "versions_failed": int((paper_versions["status"] == "failed").sum()) if len(paper_versions) else 0,
               "download_seconds": float(paper_versions["seconds"].sum()) if len(paper_versions) else 0.0}
        row.update(reference_row.iloc[0].drop("arxiv_id").to_dict() if len(reference_row) else
                   {"refs_status": "not_attempted", "n_references": 0, "n_references_with_arxiv": 0,
                    "refs_seconds": 0.0, "refs_error": None})
        paper_rows.append(row)
    papers_log = pd.DataFrame(paper_rows)
    papers_log["process_seconds"] = papers_log["download_seconds"] + papers_log["refs_seconds"]
    papers_log.to_csv(work_dir / "papers_log.csv", index=False)
    versions_log.to_csv(work_dir / "versions_log.csv", index=False)

    metrics = {"n_assigned": len(base_ids), "n_found": len(found_ids),
               "discovery_seconds": discovery_seconds, "wall_seconds": wall_seconds,
               "corpus_bytes": directory_size_bytes(corpus_dir),
               "n_papers_rows": len(papers_table), "n_reference_rows": len(references_table),
               "download_workers": arguments.workers,
               **arxiv_limiter.summary(), **s2_limiter.summary(), **resources}
    write_json(work_dir / "metrics.json", metrics)
    logging.info("Finished in %.1f s. Metrics: %s", wall_seconds, metrics)
    if stop_event.is_set():
        logging.error("Run stopped early (rate limit or Ctrl+C). Progress is saved: "
                      "wait 10-15 minutes and rerun the SAME command to resume.")
        sys.exit(1)
    if references_stop_event.is_set():
        logging.error("arXiv crawl finished, but the references crawl stopped early (Semantic Scholar 429). "
                      "Fix S2_API_KEY / wait, then rerun the SAME command to fill the missing references.")
        sys.exit(2)


if __name__ == "__main__":
    main()

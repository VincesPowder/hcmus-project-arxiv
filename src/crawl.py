"""Download every version of every discovered paper into the corpus folder."""
import logging
import time

import config
from download import download_source, extract_source, write_files
from utils import PermanentRequestError, RateLimiter, read_json, to_folder_name, write_json

LOGGER = logging.getLogger("crawler.crawl")


def crawl_version(session, limiter, version_info, version_dir):
    """Download one version, keep .tex/.bib only, write metadata.json (the 'done' marker).

    Returns a stats row. Status: ok (TeX kept), no_tex (empty record), failed (not marked done).
    """
    marker = version_dir / "metadata.json"
    if marker.exists():  # resume
        return read_json(marker)["stats"]

    versioned_id = f"{version_info['arxiv_id']}v{version_info['version']}"
    started = time.perf_counter()
    stats = {"arxiv_id": version_info["arxiv_id"], "version": version_info["version"],
             "status": "failed", "source_kind": None, "archive_bytes": 0,
             "uncompressed_bytes": 0, "kept_bytes": 0, "n_tex": 0, "n_bib": 0,
             "seconds": 0.0, "error": None}
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
    except PermanentRequestError as error:   # e.g. 404: no source for this version
        stats.update(status="no_tex", source_kind="unavailable", error=str(error))
        version_dir.mkdir(parents=True, exist_ok=True)
    except Exception as error:  # noqa: BLE001 - logged and retried on the next run
        stats["error"] = str(error)
        LOGGER.error("Download failed for %s: %s", versioned_id, error)
    stats["seconds"] = time.perf_counter() - started
    if stats["status"] != "failed":
        write_json(marker, {"metadata": version_info, "stats": stats,
                            "files": sorted(p.relative_to(version_dir).as_posix()
                                            for p in version_dir.rglob("*") if p.is_file()
                                            and p.name != "metadata.json")})
    return stats


def crawl_all_sources(session, papers, corpus_dir):
    """Crawl all versions of all found papers sequentially (one arXiv connection)."""
    limiter = RateLimiter(config.ARXIV_DELAY_SECONDS)
    version_rows = []
    for paper in papers.values():
        if not paper["found"]:
            continue
        paper_dir = corpus_dir / to_folder_name(paper["arxiv_id"])
        for version_info in paper["versions"]:
            version_dir = paper_dir / f"v{version_info['version']}"
            version_rows.append(crawl_version(session, limiter, version_info, version_dir))
        write_json(paper_dir / "paper.json", {key: paper[key] for key in (
            "arxiv_id", "title", "authors", "submission_date", "revised_dates",
            "venue", "latest_version", "versions")})
    return version_rows

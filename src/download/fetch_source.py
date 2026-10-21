"""Stage 2: download every version's source and keep only .tex and .bib files."""
import gzip
import io
import logging
import tarfile
import time
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

import requests

from ..common.config import ARXIV_DELAY_SECONDS, ARXIV_SOURCE_URL, FINAL_STATUSES, USER_AGENT
from ..common.http_utils import get_with_retry
from ..common.paths import paper_dir, read_json, version_dir, write_json
from ..common.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)
KEPT_SUFFIXES = (".tex", ".bib")


def classify_payload(payload: bytes) -> str:
    """Return tar, gzip_tex, plain_tex, pdf or other, based on content and not on file name."""
    if payload[:5] == b"%PDF-":
        return "pdf"
    if tarfile.is_tarfile(io.BytesIO(payload)):
        return "tar"
    if payload[:2] == b"\x1f\x8b":
        try:
            inner = gzip.decompress(payload)
        except (OSError, EOFError):
            return "other"
        return "gzip_tex" if b"\\" in inner[:200000] and b"\x00" not in inner[:2000] else "other"
    return "plain_tex" if b"\\documentclass" in payload[:200000] and b"\x00" not in payload[:2000] else "other"


def safe_relative_path(member_name: str):
    """Reject absolute paths and '..' to avoid writing outside the version folder."""
    path = PurePosixPath(member_name)
    if path.is_absolute() or ".." in path.parts or not path.name:
        return None
    return path


def extract_tex_and_bib(payload: bytes, kind: str, destination: Path):
    """Write only .tex/.bib files. Returns (kept_names, uncompressed_total_bytes, kept_bytes)."""
    kept_names, uncompressed_total, kept_bytes = [], 0, 0
    if kind == "tar":
        with tarfile.open(fileobj=io.BytesIO(payload)) as archive:
            for member in archive:
                if not member.isfile():
                    continue
                uncompressed_total += member.size
                relative = safe_relative_path(member.name)
                if relative is None or not relative.name.lower().endswith(KEPT_SUFFIXES):
                    continue
                data = archive.extractfile(member).read()
                target = destination / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                kept_names.append(str(relative))
                kept_bytes += len(data)
    elif kind in ("gzip_tex", "plain_tex"):
        data = gzip.decompress(payload) if kind == "gzip_tex" else payload
        uncompressed_total = len(data)
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "main.tex").write_bytes(data)
        kept_names, kept_bytes = ["main.tex"], len(data)
    return kept_names, uncompressed_total, kept_bytes


def download_version(session, limiter, data_dir, arxiv_id: str, version: int) -> dict:
    """Download one version. Writes status.json (also for empty records). Skips finished versions."""
    directory = version_dir(data_dir, arxiv_id, version)
    status_path = directory / "status.json"
    if status_path.exists() and read_json(status_path).get("status") in FINAL_STATUSES:
        return read_json(status_path)

    started = time.perf_counter()
    status = {"arxiv_id": arxiv_id, "version": version, "status": "failed", "reason": None,
              "http_status": None, "archive_bytes": 0, "uncompressed_bytes": 0, "kept_bytes": 0,
              "tex_files": [], "bib_files": []}
    response, error = get_with_retry(session, limiter, ARXIV_SOURCE_URL.format(arxiv_id=arxiv_id, version=version))
    if response is None:
        status["reason"] = error
    elif response.status_code != 200:
        status["http_status"] = response.status_code
        if response.status_code == 404:
            status["status"], status["reason"] = "empty", "http_404"
        else:
            status["reason"] = f"http_{response.status_code}"
    else:
        payload = response.content
        status["http_status"], status["archive_bytes"] = 200, len(payload)
        kind = classify_payload(payload)
        status["payload_kind"] = kind
        try:
            names, uncompressed_total, kept_bytes = extract_tex_and_bib(payload, kind, directory / "src")
        except (tarfile.TarError, OSError, EOFError) as extraction_error:
            names, uncompressed_total, kept_bytes = [], 0, 0
            status["reason"] = f"extract_error: {extraction_error}"
        status["uncompressed_bytes"], status["kept_bytes"] = uncompressed_total, kept_bytes
        status["tex_files"] = [n for n in names if n.lower().endswith(".tex")]
        status["bib_files"] = [n for n in names if n.lower().endswith(".bib")]
        if names:
            status["status"] = "ok"
        else:
            status["status"] = "failed" if status["reason"] else "empty"
            status["reason"] = status["reason"] or f"no_tex_or_bib ({kind})"
    status["seconds"] = round(time.perf_counter() - started, 2)
    status["finished_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    write_json(status_path, status)
    if status["status"] == "failed":
        logger.error("failed %sv%d: %s", arxiv_id, version, status["reason"])
    return status


def download_all(arxiv_ids, data_dir):
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    limiter = RateLimiter(ARXIV_DELAY_SECONDS)
    for index, arxiv_id in enumerate(arxiv_ids, start=1):
        metadata_path = paper_dir(data_dir, arxiv_id) / "metadata.json"
        if not metadata_path.exists():
            logger.warning("no metadata for %s, run discovery first", arxiv_id)
            continue
        for version in range(1, read_json(metadata_path)["num_versions"] + 1):
            download_version(session, limiter, data_dir, arxiv_id, version)
        if index % 50 == 0:
            logger.info("download progress %d/%d", index, len(arxiv_ids))

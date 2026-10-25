"""Full-text source download: keep only .tex and .bib, never the figures."""
import gzip
import io
import logging
import re
import time
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

import requests
import urllib3

import crawler_config as config
from crawler_utils import PermanentRequestError

TEX_HINT_PATTERN = re.compile(rb"\\(documentclass|documentstyle|begin|input|section|def|bye)")


def safe_relative_path(member_name):
    """Normalize an archive member name; return None if it could escape the folder."""
    path = PurePosixPath(member_name.replace("\\", "/"))
    parts = [part for part in path.parts if part not in ("", ".")]
    if not parts or path.is_absolute() or ".." in parts:
        return None
    return "/".join(parts)


def is_kept_file(relative_path):
    """True for .tex and .bib files (case-insensitive)."""
    return relative_path.lower().endswith(config.KEPT_EXTENSIONS)


def looks_like_tex(data):
    """Heuristic for a single-file submission: text without NUL bytes and with TeX commands."""
    return b"\x00" not in data[:4096] and TEX_HINT_PATTERN.search(data) is not None


def extract_source(raw_bytes):
    """Extract .tex/.bib from a downloaded source payload.

    Returns (files, uncompressed_bytes, kind) where files maps relative path -> bytes
    and kind is one of: tar, zip, single_tex, pdf, unknown.
    An empty `files` dict means the version has no TeX source (PDF-only, docx, ...).
    """
    if raw_bytes[:5] == b"%PDF-":
        return {}, len(raw_bytes), "pdf"

    try:  # gzipped tar and plain tar are both handled by mode "r:*"
        files, total_bytes = {}, 0
        with tarfile.open(fileobj=io.BytesIO(raw_bytes), mode="r:*") as archive:
            for member in archive:
                if not member.isfile():
                    continue
                total_bytes += member.size
                relative_path = safe_relative_path(member.name)
                if (relative_path and is_kept_file(relative_path)
                        and member.size <= config.MAX_MEMBER_BYTES):
                    files[relative_path] = archive.extractfile(member).read()
        return files, total_bytes, "tar"
    except tarfile.TarError:
        pass

    if raw_bytes[:2] == b"PK":
        try:
            files, total_bytes = {}, 0
            with zipfile.ZipFile(io.BytesIO(raw_bytes)) as archive:
                for info in archive.infolist():
                    total_bytes += info.file_size
                    relative_path = safe_relative_path(info.filename)
                    if (not info.is_dir() and relative_path and is_kept_file(relative_path)
                            and info.file_size <= config.MAX_MEMBER_BYTES):
                        files[relative_path] = archive.read(info)
            return files, total_bytes, "zip"
        except zipfile.BadZipFile:
            pass

    data = raw_bytes
    if raw_bytes[:2] == b"\x1f\x8b":  # a single gzipped file (not a tar)
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(raw_bytes)) as handle:
                data = handle.read(config.MAX_MEMBER_BYTES + 1)
        except (OSError, EOFError):
            return {}, len(raw_bytes), "unknown"
        if len(data) > config.MAX_MEMBER_BYTES:
            return {}, len(data), "unknown"
    if looks_like_tex(data):
        return {"main.tex": data}, len(data), "single_tex"
    return {}, len(data), "unknown"


def write_files(version_dir, files):
    """Write extracted files under version_dir, refusing any path outside it."""
    version_dir = Path(version_dir).resolve()
    for relative_path, content in files.items():
        destination = (version_dir / relative_path).resolve()
        if version_dir not in destination.parents:
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)


LOGGER = logging.getLogger("crawler.download")
CHUNK_BYTES = 1 << 20


def download_source(session, limiter, versioned_id):
    """Download the raw source of one version, resuming with HTTP Range after a broken connection.

    Raw (undecoded) bytes are kept so that byte offsets stay consistent between attempts.
    If the server ignores Range (status 200) the download restarts from scratch.
    """
    url = config.ARXIV_SRC_URL.format(versioned_id=versioned_id)
    buffer = bytearray()
    last_error = None
    for attempt in range(config.MAX_RETRIES + 1):
        limiter.wait()
        headers = {"Range": f"bytes={len(buffer)}-"} if buffer else {}
        try:
            with session.get(url, headers=headers, stream=True,
                             timeout=(30, config.REQUEST_TIMEOUT_SECONDS)) as response:
                status = response.status_code
                if status in (404, 400):
                    raise PermanentRequestError(f"HTTP {status}")
                if status == 200:
                    buffer = bytearray()          # full body (server ignored Range or first try)
                elif status != 206:
                    last_error = f"HTTP {status}"  # 403/429/5xx/416: wait and retry
                    buffer = bytearray() if status == 416 else buffer
                    raise requests.RequestException(last_error)
                start_length = len(buffer)
                for chunk in response.raw.stream(CHUNK_BYTES, decode_content=False):
                    buffer.extend(chunk)
                declared = response.headers.get("Content-Length")
                if declared and len(buffer) - start_length < int(declared):
                    raise requests.RequestException(
                        f"incomplete body: {len(buffer) - start_length} of {declared} bytes")
            if bytes(buffer[:15]).lstrip().lower().startswith((b"<html", b"<!doctype")):
                last_error, buffer = "HTML page instead of data", bytearray()
            else:
                return bytes(buffer)
        except PermanentRequestError:
            raise
        except (requests.RequestException, urllib3.exceptions.HTTPError, OSError) as error:
            last_error = f"network error: {error}"
        wait_seconds = config.BACKOFF_BASE_SECONDS * (2 ** attempt)
        LOGGER.warning("%s: %s (attempt %d/%d, %d bytes kept), waiting %.0f s", versioned_id,
                       last_error, attempt + 1, config.MAX_RETRIES + 1, len(buffer), wait_seconds)
        time.sleep(wait_seconds)
    raise RuntimeError(f"gave up after {config.MAX_RETRIES + 1} attempts: {last_error}")

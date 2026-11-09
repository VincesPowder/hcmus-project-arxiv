"""Small helpers: id handling, rate limiting, HTTP retries, JSON IO."""
import json
import re
import threading
import time
from pathlib import Path

import requests

import config

VERSIONED_ID_PATTERN = re.compile(r"^(?P<base>.+?)v(?P<version>\d+)$")


class PermanentRequestError(Exception):
    """Raised when retrying cannot help (for example HTTP 404)."""


class RateLimiter:
    """Thread-safe limiter that enforces a minimum gap between calls."""

    def __init__(self, min_interval_seconds):
        self.min_interval_seconds = min_interval_seconds
        self.last_call_time = 0.0
        self.lock = threading.Lock()

    def wait(self):
        """Block until the next call is allowed."""
        with self.lock:
            sleep_seconds = self.last_call_time + self.min_interval_seconds - time.monotonic()
            if sleep_seconds > 0:
                time.sleep(sleep_seconds)
            self.last_call_time = time.monotonic()


def split_versioned_id(versioned_id):
    """Split '2310.12345v4' into ('2310.12345', 4); no suffix gives version None."""
    match = VERSIONED_ID_PATTERN.match(versioned_id)
    if match is None:
        return versioned_id, None
    return match.group("base"), int(match.group("version"))


def to_folder_name(arxiv_id):
    """Folder name of a publication: '2310.12345' -> '2310-12345', 'hep-th/9901001' -> 'hep-th_9901001'."""
    return arxiv_id.replace(".", "-").replace("/", "_")


def generate_id_range(first_id, last_id):
    """Expand two new-style ids of the same month, e.g. 2310.00001 .. 2310.00050."""
    prefix_first, number_first = first_id.split(".")
    prefix_last, number_last = last_id.split(".")
    if prefix_first != prefix_last:
        raise ValueError("The range must stay inside one YYMM prefix.")
    width = len(number_first)
    return [f"{prefix_first}.{number:0{width}d}"
            for number in range(int(number_first), int(number_last) + 1)]


def read_id_file(path):
    """Read one arXiv id per line (blank lines and '#' comments ignored)."""
    ids = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.split("#")[0].strip()
        if line:
            ids.append(split_versioned_id(line)[0])
    return list(dict.fromkeys(ids))  # de-duplicate, keep order


def chunk_list(items, chunk_size):
    """Yield successive chunks of a list."""
    for start in range(0, len(items), chunk_size):
        yield items[start:start + chunk_size]


def request_with_retries(session, limiter, url, params=None, extra_headers=None):
    """GET with pacing, capped retries and exponential backoff.

    Retries on network errors, HTTP 429/5xx and HTML error pages.
    Raises PermanentRequestError for 404 and other non-retryable codes.
    """
    last_error = None
    for attempt in range(config.MAX_RETRIES + 1):
        limiter.wait()
        try:
            response = session.get(url, params=params, headers=extra_headers,
                                   timeout=config.REQUEST_TIMEOUT_SECONDS)
        except requests.RequestException as error:
            last_error = f"network error: {error}"
        else:
            if response.status_code == 200:
                if response.content[:15].lstrip().lower().startswith((b"<html", b"<!doctype")):
                    last_error = "HTML page instead of data"
                else:
                    return response
            elif response.status_code in (404, 400, 403):
                raise PermanentRequestError(f"HTTP {response.status_code}")
            else:
                last_error = f"HTTP {response.status_code}"
                retry_after = response.headers.get("Retry-After", "")
                if retry_after.isdigit():
                    time.sleep(min(int(retry_after), 120))
        time.sleep(config.BACKOFF_BASE_SECONDS * (2 ** attempt))
    raise RuntimeError(f"gave up after {config.MAX_RETRIES + 1} attempts: {last_error}")


def make_session():
    """Create a requests session carrying the descriptive User-Agent."""
    session = requests.Session()
    session.headers.update({"User-Agent": config.USER_AGENT})
    return session


def write_json(path, payload):
    """Write JSON atomically (temp file, then rename)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(path)


def read_json(path):
    """Read a JSON file."""
    return json.loads(Path(path).read_text(encoding="utf-8"))

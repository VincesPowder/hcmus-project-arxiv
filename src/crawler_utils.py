"""Small helpers: id handling, adaptive rate limiting, HTTP retries, JSON IO."""
import email.utils
import json
import logging
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

import crawler_config as config

LOGGER = logging.getLogger("crawler.http")
VERSIONED_ID_PATTERN = re.compile(r"^(?P<base>.+?)v(?P<version>\d+)$")
ID_RANGE_PATTERN = re.compile(r"^(?P<first>\d{4}\.\d{4,5})\s*(?:\.\.|-|:)\s*(?P<last>\d{4}\.\d{4,5})$")


class PermanentRequestError(Exception):
    """Raised when retrying cannot help (for example HTTP 404)."""


class AuthError(Exception):
    """The Semantic Scholar API key was rejected (HTTP 401/403 with a key)."""


class HardBlockError(RuntimeError):
    """The host kept throttling without a single success: stop and resume later."""


class StopRequested(Exception):
    """Raised inside a rate limiter wait when the run is being stopped (Ctrl+C, hard block)."""


class AdaptiveRateLimiter:
    """Thread-safe pacing for ONE host, shared by every thread that talks to it.

    * `wait()` hands out request slots at least `interval` seconds apart.
    * `on_throttle()` (HTTP 429/403) pauses the whole host for a cooldown and widens `interval`.
    * `on_success()` shrinks `interval` back towards the base gap.
    * Counters feed metrics.json, so the report can show how much time the throttling cost.
    """

    def __init__(self, name, base_interval, max_interval, stop_event=None):
        self.name = name
        self.base_interval = base_interval
        self.max_interval = max_interval
        self.interval = base_interval
        self.stop_event = stop_event or threading.Event()
        self.lock = threading.Lock()
        self.next_slot = 0.0
        self.blocked_until = 0.0
        self.consecutive_throttles = 0
        self.request_count = 0
        self.throttle_count = 0
        self.penalty_seconds = 0.0   # time the host was paused by cooldowns/backoffs

    def wait(self):
        """Block until the next request is allowed; raise StopRequested if the run is stopping."""
        if self.stop_event.is_set():
            raise StopRequested()
        with self.lock:
            now = time.monotonic()
            start = max(now, self.next_slot, self.blocked_until)
            self.next_slot = start + self.interval
            self.request_count += 1
        delay = start - now
        if delay > 0 and self.stop_event.wait(delay):
            raise StopRequested()

    def _block(self, seconds):
        """Pause the host for `seconds` (caller holds the lock); only the extension is counted."""
        now = time.monotonic()
        new_block = now + seconds
        if new_block > self.blocked_until:
            self.penalty_seconds += new_block - max(now, self.blocked_until)
            self.blocked_until = new_block
            self.next_slot = max(self.next_slot, new_block)

    def on_success(self):
        with self.lock:
            self.consecutive_throttles = 0
            self.interval = max(self.base_interval, self.interval * config.INTERVAL_DECAY)

    def on_throttle(self, retry_after=None):
        """Register HTTP 429/403: pause the host and slow down. Returns the cooldown in seconds."""
        with self.lock:
            self.consecutive_throttles += 1
            self.throttle_count += 1
            escalating = config.THROTTLE_COOLDOWN_BASE_SECONDS * 2 ** (self.consecutive_throttles - 1)
            cooldown = min(max(retry_after or 0.0, escalating), config.THROTTLE_COOLDOWN_MAX_SECONDS)
            self.interval = min(self.max_interval, self.interval * config.INTERVAL_GROWTH)
            self._block(cooldown)
            return cooldown

    def penalize(self, seconds):
        """Short pause after a transient error (network drop, HTTP 5xx)."""
        with self.lock:
            self._block(seconds)

    def set_base_interval(self, base_interval):
        """Change the base gap, e.g. after the API key was dropped."""
        with self.lock:
            self.base_interval = base_interval
            self.interval = max(self.interval, base_interval)

    def hard_blocked(self):
        return self.consecutive_throttles >= config.HARD_BLOCK_THROTTLES

    def summary(self):
        """Counters for metrics.json, prefixed with the host name."""
        with self.lock:
            return {f"{self.name}_requests": self.request_count,
                    f"{self.name}_throttled": self.throttle_count,
                    f"{self.name}_penalty_seconds": round(self.penalty_seconds, 1),
                    f"{self.name}_final_interval_seconds": round(self.interval, 2)}


def parse_retry_after(value):
    """Seconds from a Retry-After header (delta-seconds or HTTP date); None if absent/invalid."""
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        when = email.utils.parsedate_to_datetime(value)
        return max(0.0, (when - datetime.now(when.tzinfo or timezone.utc)).total_seconds())
    except (TypeError, ValueError):
        return None


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
    """Read arXiv ids from a text file, one entry per line.

    An entry is a single id ('2605.02338', 'hep-th/9901001', optional 'vN' suffix) or an inclusive
    range inside one month ('2605.02338..2605.07337'; '-' and ':' also work as separator).
    Blank lines and '#' comments are ignored, so a whole group can merge every member's range
    into one file and crawl it in a single run.
    """
    ids = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.split("#")[0].strip()
        if not line:
            continue
        range_match = ID_RANGE_PATTERN.match(line)
        if range_match:
            ids.extend(generate_id_range(range_match.group("first"), range_match.group("last")))
        else:
            ids.append(split_versioned_id(line)[0])
    return list(dict.fromkeys(ids))  # de-duplicate, keep order


def chunk_list(items, chunk_size):
    """Yield successive chunks of a list."""
    for start in range(0, len(items), chunk_size):
        yield items[start:start + chunk_size]


def request_with_retries(session, limiter, url, params=None, extra_headers=None,
                         method="GET", json_body=None):
    """HTTP request with shared pacing and two separate retry budgets.

    * Throttling (HTTP 429/403, HTML block page): the host is paused through the limiter
      (Retry-After or escalating cooldown), up to MAX_THROTTLE_RETRIES times.
    * Transient errors (network, HTTP 5xx): short exponential backoff, up to MAX_RETRIES times.
    Raises PermanentRequestError (404/400), AuthError (key rejected), HardBlockError (host keeps
    throttling) or RuntimeError (budget exhausted; the caller queues the item for a later pass).
    """
    transient_failures = throttle_failures = 0
    last_error = None
    while True:
        limiter.wait()
        retry_after, throttled = None, False
        try:
            response = session.request(method, url, params=params, headers=extra_headers,
                                       json=json_body, timeout=config.REQUEST_TIMEOUT_SECONDS)
        except requests.RequestException as error:
            last_error = f"network error: {error}"
        else:
            status = response.status_code
            if status == 200:
                if response.content[:15].lstrip().lower().startswith((b"<html", b"<!doctype")):
                    last_error, throttled = "HTML page instead of data", True
                else:
                    limiter.on_success()
                    return response
            elif status in (404, 400):
                raise PermanentRequestError(f"HTTP {status}")
            elif status in (401, 403) and extra_headers and "x-api-key" in extra_headers:
                raise AuthError(f"HTTP {status}")
            elif status in (429, 403):
                last_error, throttled = f"HTTP {status}", True
                retry_after = parse_retry_after(response.headers.get("Retry-After"))
            else:
                last_error = f"HTTP {status} {response.text[:80]!r}"

        if throttled:
            throttle_failures += 1
            cooldown = limiter.on_throttle(retry_after)
            if limiter.hard_blocked():
                raise HardBlockError(f"{limiter.name}: {limiter.consecutive_throttles} throttles in a row")
            if throttle_failures > config.MAX_THROTTLE_RETRIES:
                raise RuntimeError(f"gave up after {throttle_failures} throttled attempts: {last_error}")
        else:
            transient_failures += 1
            if transient_failures > config.MAX_RETRIES:
                raise RuntimeError(f"gave up after {transient_failures} attempts: {last_error}")
            cooldown = config.BACKOFF_BASE_SECONDS * 2 ** (transient_failures - 1)
            limiter.penalize(cooldown)
        LOGGER.warning("%s -> %s (throttled %d, transient %d), host paused %.0f s",
                       url.split("?")[0], last_error, throttle_failures, transient_failures, cooldown)


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

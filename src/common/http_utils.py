"""HTTP GET with retries, backoff and rate limiting."""
import logging
import time

import requests

from .config import MAX_RETRIES, REQUEST_TIMEOUT_SECONDS, RETRY_BACKOFF_BASE_SECONDS

RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
logger = logging.getLogger(__name__)


def get_with_retry(session, limiter, url, params=None, headers=None):
    """Return (response, None) on success or (None, error_message) after all retries.

    A non-retryable status such as 404 is returned as a normal response.
    """
    last_error = "unknown"
    for attempt in range(1, MAX_RETRIES + 1):
        retry_after_seconds = 0.0
        try:
            with limiter:
                response = session.get(
                    url, params=params, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS
                )
        except requests.RequestException as error:
            last_error = f"{type(error).__name__}: {error}"
        else:
            if response.status_code not in RETRYABLE_STATUS_CODES:
                return response, None
            last_error = f"http_{response.status_code}"
            header_value = response.headers.get("Retry-After", "")
            if header_value.isdigit():
                retry_after_seconds = float(header_value)
        if attempt < MAX_RETRIES:
            sleep_seconds = max(retry_after_seconds, RETRY_BACKOFF_BASE_SECONDS * 2 ** (attempt - 1))
            logger.warning("%s (attempt %d/%d) for %s, sleeping %.0fs", last_error, attempt, MAX_RETRIES, url, sleep_seconds)
            time.sleep(sleep_seconds)
    return None, last_error

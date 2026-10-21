"""One-connection-at-a-time rate limiter."""
import threading
import time


class RateLimiter:
    """Allow one request at a time, with a minimum pause after the previous one ended.

    Use as a context manager around the whole request, including reading the body:
        with limiter:
            response = session.get(url)
    """

    def __init__(self, min_interval_seconds: float):
        self.min_interval_seconds = min_interval_seconds
        self._last_finished = 0.0
        self._lock = threading.Lock()

    def __enter__(self):
        self._lock.acquire()
        wait_seconds = self._last_finished + self.min_interval_seconds - time.monotonic()
        if wait_seconds > 0:
            time.sleep(wait_seconds)
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self._last_finished = time.monotonic()
        self._lock.release()

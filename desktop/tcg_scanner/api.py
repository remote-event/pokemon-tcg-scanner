"""Bounded GET requests with shared throttling and retry handling."""
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import http.client
import json
import logging
import math
import random
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

log = logging.getLogger(__name__)


class FetchError(RuntimeError):
    pass


def retry_after(value):
    if not value:
        return 0.0
    try:
        seconds = float(value)
        return max(0.0, seconds) if math.isfinite(seconds) else 0.0
    except ValueError:
        try:
            return max(0.0, (parsedate_to_datetime(value) -
                            datetime.now(timezone.utc)).total_seconds())
        except (ValueError, TypeError, OverflowError):
            return 0.0


class Client:
    def __init__(self, config):
        self.config = config
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.next_request = 0.0

    def url(self, endpoint):
        return f"{self.config.api_base.rstrip('/')}/{self.config.language}/{endpoint}"

    def card_url(self, card_id):
        return self.url("cards/" + quote(card_id, safe=""))

    def throttle(self):
        while True:
            if self.stop.is_set():
                raise FetchError("Interrupted")
            with self.lock:
                delay = self.next_request - time.monotonic()
                if delay <= 0:
                    self.next_request = time.monotonic() + 1 / self.config.requests_per_second
                    return
            self.stop.wait(delay)

    def pause(self, seconds):
        with self.lock:
            self.next_request = max(self.next_request, time.monotonic() + seconds)

    def get(self, url, *, headers=None, decode=None):
        """Return (decoded body, response headers, status); validate inside retries."""
        for attempt in range(self.config.retries + 1):
            self.throttle()
            retry_delay = 0.0
            try:
                request = Request(url, headers={"User-Agent": "tcg-scanner/0.1",
                                                **(headers or {})})
                with urlopen(request, timeout=self.config.timeout) as response:
                    body = response.read(32 * 1024 * 1024 + 1)
                    if len(body) > 32 * 1024 * 1024:
                        raise ValueError("Response exceeds 32 MiB limit")
                    length = response.headers.get("Content-Length")
                    if length and len(body) != int(length):
                        raise ValueError("Truncated response")
                    result = decode(body) if decode else body
                    return result, dict(response.headers.items()), response.status
            except HTTPError as exc:
                status = exc.code
                response_headers = dict(exc.headers.items())
                retry_delay = retry_after(exc.headers.get("Retry-After"))
                exc.close()
                if status == 304:
                    return None, response_headers, 304
                if status not in {408, 429} and not 500 <= status < 600:
                    raise FetchError(f"HTTP {status}: {url}") from exc
                error = f"HTTP {status}"
            except (URLError, OSError, http.client.HTTPException, ValueError) as exc:
                error = str(exc)
            if attempt == self.config.retries:
                raise FetchError(f"{url}: {error} (after {attempt + 1} attempts)")
            delay = max(retry_delay, min(60, self.config.backoff * 2**attempt)
                        + random.uniform(0, self.config.backoff))
            log.warning("Retry %d/%d in %.1fs: %s: %s", attempt + 1,
                        self.config.retries, delay, url, error)
            self.pause(delay)
        raise AssertionError("unreachable")

    def json(self, url, headers=None):
        return self.get(url, headers=headers, decode=json.loads)

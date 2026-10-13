"""Shared HTTP session with retries, exponential backoff and a small response cache."""

from __future__ import annotations

import logging
import time
from urllib.parse import urlparse

import requests

log = logging.getLogger(__name__)

DEFAULT_UA = "Mozilla/5.0 (compatible; StockTrackerBot/1.0)"
_session: requests.Session | None = None
_cache: dict[str, tuple[float, bytes]] = {}
# Circuit breaker: after 3 straight failures a host is skipped for 10 minutes.
_failures: dict[str, int] = {}
_open_until: dict[str, float] = {}
TRIP_AFTER, COOL_OFF = 3, 600


def session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers["User-Agent"] = DEFAULT_UA
    return _session


def get(url: str, *, params: dict | None = None, headers: dict | None = None,
        timeout: float = 15, retries: int = 3, cache_seconds: float = 0) -> bytes | None:
    """GET with backoff (1s, 2s, 4s). Returns body bytes, or None after giving up."""
    host = urlparse(url).netloc
    if _open_until.get(host, 0) > time.time():
        return None
    key = url + repr(sorted((params or {}).items()))
    if cache_seconds and key in _cache and time.time() - _cache[key][0] < cache_seconds:
        return _cache[key][1]
    delay = 1.0
    for attempt in range(retries):
        try:
            resp = session().get(url, params=params, headers=headers, timeout=timeout)
            if resp.status_code == 429 or resp.status_code >= 500:
                raise requests.HTTPError(f"HTTP {resp.status_code}")
            if resp.status_code >= 400:
                log.debug("GET %s -> HTTP %s", url, resp.status_code)
                return None
            if cache_seconds:
                _cache[key] = (time.time(), resp.content)
            _failures[host] = 0
            return resp.content
        except requests.RequestException as exc:
            if attempt == retries - 1:
                _failures[host] = _failures.get(host, 0) + 1
                if _failures[host] >= TRIP_AFTER:
                    _open_until[host] = time.time() + COOL_OFF
                    log.warning("%s unreachable (%s); pausing it for %d min",
                                host, type(exc).__name__, COOL_OFF // 60)
                else:
                    log.info("GET %s failed: %s", host, type(exc).__name__)
                return None
            time.sleep(delay)
            delay *= 2
    return None

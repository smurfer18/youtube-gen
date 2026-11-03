"""HTTP helpers: one session, a descriptive User-Agent (required by Wikimedia),
retries, the system CA bundle (SSL_CERT_FILE / REQUESTS_CA_BUNDLE) and atomic
downloads."""

from __future__ import annotations

import hashlib
import os
import ssl
import threading
from pathlib import Path
from typing import Any, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

USER_AGENT = "ytgen/0.1 (+https://github.com/smurfer18/youtube-gen; faceless video generator)"

_local = threading.local()


def session() -> requests.Session:
    s = getattr(_local, "s", None)
    if s is None:
        s = requests.Session()
        s.headers["User-Agent"] = USER_AGENT
        # 429s are not retried here (and Retry-After is not slept on): callers back off per
        # provider instead, so one throttled host never stalls the whole pipeline.
        retry = Retry(total=2, connect=2, read=1, backoff_factor=0.6, status_forcelist=(500, 502, 503, 504),
                      allowed_methods=("GET", "HEAD", "POST"), respect_retry_after_header=False)
        s.mount("https://", HTTPAdapter(max_retries=retry, pool_maxsize=16))
        s.mount("http://", HTTPAdapter(max_retries=retry, pool_maxsize=16))
        _local.s = s
    return s


def _check(r: requests.Response) -> None:
    if r.status_code == 429:
        from urllib.parse import urlparse

        ra = r.headers.get("Retry-After", "60")
        raise Throttled(urlparse(r.url).hostname or "?", float(ra) if ra.isdigit() else 60.0)
    r.raise_for_status()


def get_json(url: str, params: Optional[dict] = None, timeout: float = 20, headers: Optional[dict] = None) -> Any:
    r = session().get(url, params=params, timeout=timeout, headers=headers)
    _check(r)
    return r.json()


def download(url: str, dest: Path, timeout: float = 60, headers: Optional[dict] = None) -> Path:
    dest = Path(dest)
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with session().get(url, stream=True, timeout=timeout, headers=headers) as r:
        _check(r)
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(1 << 16):
                f.write(chunk)
    os.replace(tmp, dest)
    return dest


def url_key(url: str) -> str:
    return hashlib.sha1(url.encode()).hexdigest()[:16]


def ssl_context() -> ssl.SSLContext:
    """An SSL context that honours SSL_CERT_FILE / REQUESTS_CA_BUNDLE (corporate
    proxies, custom CAs); falls back to the platform default store."""
    cafile = os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE")
    if cafile and Path(cafile).is_file():
        return ssl.create_default_context(cafile=cafile)
    try:
        import certifi  # type: ignore

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


class Throttled(Exception):
    """Raised when a host answers 429; carries the host for circuit-breaking."""

    def __init__(self, host: str, retry_after: float):
        super().__init__(f"{host} rate-limited us (retry after {retry_after:.0f}s)")
        self.host, self.retry_after = host, retry_after


_pace_lock = threading.Lock()
_last_hit: dict[str, float] = {}


def pace(host: str, min_interval: float) -> None:
    """Keep at least `min_interval` seconds between requests to `host` (process-wide)."""
    import time

    with _pace_lock:
        now = time.time()
        wait = _last_hit.get(host, 0) + min_interval - now
        _last_hit[host] = max(now, _last_hit.get(host, 0) + min_interval)
    if wait > 0:
        time.sleep(wait)

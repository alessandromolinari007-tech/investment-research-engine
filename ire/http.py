"""HTTP layer: polite rate limiting, retries, on-disk cache with TTL + conditional GET.

Every downloaded payload is cached on disk (data/cache/...) so that
(1) a run can be reproduced/inspected later and (2) we don't hammer free sources.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

from .config import load_config


class SourceUnavailable(RuntimeError):
    """Raised when a source cannot be reached / returns an unusable answer."""


class RateLimiter:
    def __init__(self, per_second: float):
        self.min_interval = 1.0 / max(per_second, 0.1)
        self._lock = threading.Lock()
        self._last = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            delay = self._last + self.min_interval - now
            if delay > 0:
                time.sleep(delay)
            self._last = time.monotonic()


@dataclass
class CachedResponse:
    content: bytes
    fetched_at: float
    from_cache: bool
    url: str

    def json(self) -> Any:
        return json.loads(self.content.decode("utf-8"))

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")


class HttpClient:
    DEFAULT_RATES = {
        "data.sec.gov": 8.0,
        "www.sec.gov": 8.0,
        "efts.sec.gov": 5.0,
        "fred.stlouisfed.org": 2.0,
        "www.ecb.europa.eu": 2.0,
        "en.wikipedia.org": 2.0,
    }

    def __init__(self, user_agent: str | None = None, cache_dir: Path | None = None):
        cfg = load_config()
        self.cache_dir = cache_dir or (cfg.cache_dir / "http")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.session = requests.Session()
        # Generic UA for every host; the SEC UA (with the user's contact) is sent ONLY to sec.gov.
        self.generic_ua = "InvestmentResearchEngine/1.0 (personal, non-commercial research)"
        self.sec_user_agent = user_agent or ""
        self.session.headers.update({"User-Agent": self.generic_ua, "Accept-Encoding": "gzip, deflate"})
        sec_rate = float(cfg.get("sec.max_requests_per_second", 8))
        self.rates = dict(self.DEFAULT_RATES)
        self.rates["data.sec.gov"] = min(sec_rate, 9.0)
        self.rates["www.sec.gov"] = min(sec_rate, 9.0)
        self._limiters: dict[str, RateLimiter] = {}
        self.stats = {"requests": 0, "cache_hits": 0, "not_modified": 0, "errors": 0}

    # ------------------------------------------------------------------
    _lock = threading.Lock()

    def _limiter(self, host: str) -> RateLimiter:
        # all sec.gov hosts share ONE limiter: the SEC fair-access limit is per user, not per hostname
        key = "sec.gov" if host.endswith("sec.gov") else host
        with self._lock:
            if key not in self._limiters:
                rate = min(self.rates.get("data.sec.gov", 8.0), 9.0) if key == "sec.gov" else self.rates.get(host, 4.0)
                self._limiters[key] = RateLimiter(rate)
            return self._limiters[key]

    def _count(self, key: str) -> None:
        with self._lock:
            self.stats[key] += 1

    @staticmethod
    def _atomic_write(path: Path, data: bytes) -> None:
        tmp = path.with_name(path.name + f".{threading.get_ident()}.tmp")
        tmp.write_bytes(data)
        for i in range(5):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:      # Windows: file briefly locked by antivirus/indexer
                time.sleep(0.2 * (i + 1))
        os.replace(tmp, path)

    def _read_cached(self, body_p: Path, meta_p: Path) -> bytes | None:
        """Cached body, or None (and the corrupt entry is deleted) if it cannot be decompressed."""
        try:
            return gzip.decompress(body_p.read_bytes())
        except (OSError, EOFError, ValueError):
            for p in (body_p, meta_p):
                try:
                    p.unlink()
                except OSError:
                    pass
            return None

    def _paths(self, url: str) -> tuple[Path, Path]:
        parsed = urlparse(url)
        h = hashlib.sha1(url.encode()).hexdigest()[:16]
        tail = Path(parsed.path).name or "index"
        tail = "".join(c if c.isalnum() or c in "._-" else "_" for c in tail)[:80]
        d = self.cache_dir / parsed.netloc
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{tail}.{h}.gz", d / f"{tail}.{h}.meta.json"

    def cache_age_seconds(self, url: str) -> float | None:
        body, meta = self._paths(url)
        if not body.exists() or not meta.exists():
            return None
        try:
            m = json.loads(meta.read_text())
            return time.time() - float(m["fetched_at"])
        except Exception:
            return None

    def get(
        self,
        url: str,
        ttl_seconds: float = 86400,
        headers: dict[str, str] | None = None,
        max_retries: int = 4,
        timeout: float = 60,
        allow_stale_on_error: bool = True,
    ) -> CachedResponse:
        body_p, meta_p = self._paths(url)
        meta: dict[str, Any] = {}
        cached: bytes | None = None
        if body_p.exists() and meta_p.exists():
            try:
                meta = json.loads(meta_p.read_text())
            except Exception:
                meta = {}
            cached = self._read_cached(body_p, meta_p)
            if cached is None:
                meta = {}          # corrupt entry removed → full download without conditional headers
            elif time.time() - float(meta.get("fetched_at", 0)) < ttl_seconds:
                self._count("cache_hits")
                return CachedResponse(cached, float(meta["fetched_at"]), True, url)
        expect_json = urlparse(url).path.endswith(".json")

        host = urlparse(url).netloc
        req_headers = dict(headers or {})
        if host.endswith("sec.gov"):
            if not self.sec_user_agent or "@" not in self.sec_user_agent:
                raise SourceUnavailable(
                    "Manca il SEC User-Agent: apri config.toml e in [sec] imposta user_agent = "
                    "\"Nome Cognome tua@email\" (la SEC lo richiede per l'accesso gratuito)."
                )
            req_headers["User-Agent"] = self.sec_user_agent
        if cached is not None and meta.get("etag"):
            req_headers["If-None-Match"] = meta["etag"]
        if cached is not None and meta.get("last_modified"):
            req_headers["If-Modified-Since"] = meta["last_modified"]

        last_err: Exception | None = None
        for attempt in range(max_retries):
            self._limiter(host).wait()
            try:
                self._count("requests")
                r = self.session.get(url, headers=req_headers, timeout=timeout)
            except requests.RequestException as e:
                last_err = e
                time.sleep(min(2 ** attempt, 20))
                continue
            if r.status_code == 304 and cached is not None:
                self._count("not_modified")
                meta["fetched_at"] = time.time()
                self._atomic_write(meta_p, json.dumps(meta).encode())
                return CachedResponse(cached, meta["fetched_at"], True, url)
            if r.status_code == 200:
                content = r.content
                if expect_json:
                    try:
                        json.loads(content.decode("utf-8"))
                    except (ValueError, UnicodeDecodeError):
                        # maintenance page / truncated answer: never cache it as if it were data
                        last_err = SourceUnavailable(f"risposta non valida (non JSON) da {url}")
                        time.sleep(min(2 ** (attempt + 1), 30))
                        continue
                self._atomic_write(body_p, gzip.compress(content))
                meta = {
                    "url": url,
                    "fetched_at": time.time(),
                    "etag": r.headers.get("ETag"),
                    "last_modified": r.headers.get("Last-Modified"),
                }
                self._atomic_write(meta_p, json.dumps(meta).encode())
                return CachedResponse(content, meta["fetched_at"], False, url)
            if r.status_code == 404:
                last_err = SourceUnavailable(f"404 Not Found: {url}")
                break
            if r.status_code == 403 and "sec.gov" in host:
                body = (r.text or "")[:2000].lower()
                if "rate" in body or "threshold" in body or "undeclared" not in body and attempt < max_retries - 1:
                    # SEC signals throttling with 403 "Request Rate Threshold Exceeded": back off and retry
                    last_err = SourceUnavailable("SEC: limite di richieste superato (403), attesa e nuovo tentativo")
                    time.sleep(60 * (attempt + 1))
                    continue
                last_err = SourceUnavailable(
                    "SEC ha risposto 403: controlla che in config.toml [sec] user_agent contenga "
                    "nome ed email reali, e di non superare 10 richieste/secondo.")
                break
            if r.status_code in (429, 500, 502, 503, 504):
                last_err = SourceUnavailable(f"HTTP {r.status_code} for {url}")
                retry_after = r.headers.get("Retry-After")
                wait = float(retry_after) if retry_after and retry_after.isdigit() else min(2 ** (attempt + 1), 60)
                time.sleep(wait)
                continue
            last_err = SourceUnavailable(f"HTTP {r.status_code} for {url}")
            break

        self._count("errors")
        if allow_stale_on_error and cached is not None:
            # Serve stale cache; the caller can see it through from_cache + fetched_at
            return CachedResponse(cached, float(meta.get("fetched_at", 0)), True, url)
        raise SourceUnavailable(str(last_err) if last_err else f"Failed: {url}")


_CLIENT: HttpClient | None = None


def get_client() -> HttpClient:
    global _CLIENT
    if _CLIENT is None:
        cfg = load_config()
        ua = cfg.sec_user_agent or None
        _CLIENT = HttpClient(user_agent=ua)
    return _CLIENT

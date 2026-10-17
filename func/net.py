"""HTTP layer: pooled sessions, sane retries, UA/proxy rotation and Tor support.

One :class:`requests.Session` is kept per worker thread so TCP/TLS connections
are reused (keep-alive) instead of being rebuilt for every request, which is
where most of the previous version's slowness came from.
"""

from __future__ import annotations

import threading
import time
from urllib.parse import urlsplit

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from utils.ansi import c
from utils.user_agents import AgentPool

__all__ = ["HttpPool", "DEFAULT_HEADERS"]

DEFAULT_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,pt-BR;q=0.8,pt;q=0.7",
    "Upgrade-Insecure-Requests": "1",
    "Connection": "keep-alive",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}


class HttpPool:
    """Thread aware requests session factory with retry/proxy/UA handling."""

    def __init__(
        self,
        timeout: float = 15.0,
        connect_timeout: float | None = None,
        retries: int = 2,
        verify: bool = True,
        proxies: dict[str, str] | None = None,
        proxy_pool=None,
        agents: AgentPool | None = None,
        delay: float = 0.0,
        verbose: bool = False,
        log=None,
        user_headers: dict[str, str] | None = None,
        redirects: bool = True,
    ) -> None:
        self.timeout = float(timeout)
        self.connect_timeout = float(connect_timeout if connect_timeout else min(6.0, self.timeout))
        self.retries = max(0, int(retries))
        self.verify = bool(verify)
        self.static_proxies = proxies or None
        self.proxy_pool = proxy_pool
        self.agents = agents or AgentPool()
        self.delay = max(0.0, float(delay))
        self.verbose = verbose
        self.log = log
        self.redirects = redirects
        self.headers = dict(DEFAULT_HEADERS)
        if user_headers:
            self.headers.update(user_headers)

        self._local = threading.local()
        self._counter_lock = threading.Lock()
        self._requests_made = 0
        self._errors = 0
        self._last_error = threading.local()
        self._scheme_cache: dict[str, str] = {}

        if not self.verify:
            try:
                requests.packages.urllib3.disable_warnings()  # type: ignore[attr-defined]
            except Exception:
                pass

    # ------------------------------------------------------------------ #
    # sessions
    # ------------------------------------------------------------------ #
    def _build_session(self, proxy: dict[str, str] | None) -> requests.Session:
        session = requests.Session()
        retry = Retry(
            total=self.retries,
            connect=self.retries,
            read=self.retries,
            status=0,  # status codes are handled by the scanner logic, not urllib3
            backoff_factor=0.4,
            allowed_methods=frozenset(["GET", "POST", "HEAD", "OPTIONS"]),
            raise_on_status=False,
            respect_retry_after_header=False,
        )
        adapter = HTTPAdapter(
            max_retries=retry,
            pool_connections=4,
            pool_maxsize=16,
            pool_block=False,
        )
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        session.headers.update(self.headers)
        session.headers["User-Agent"] = self.agents.get()
        session.verify = self.verify
        if proxy:
            session.proxies.update(proxy)
        return session

    def session(self) -> requests.Session:
        session = getattr(self._local, "session", None)
        if session is None:
            session = self._build_session(self._current_proxy())
            self._local.session = session
        return session

    def close(self) -> None:
        session = getattr(self._local, "session", None)
        if session is not None:
            try:
                session.close()
            except Exception:
                pass
            self._local.session = None

    # ------------------------------------------------------------------ #
    # proxies / user agents
    # ------------------------------------------------------------------ #
    def _current_proxy(self) -> dict[str, str] | None:
        if self.proxy_pool is not None and self.proxy_pool.enabled:
            return self.proxy_pool.next()
        return self.static_proxies

    def randomize_agent(self) -> str:
        """Give the current thread a fresh user agent (called per target)."""
        session = self.session()
        agent = self.agents.get()
        session.headers["User-Agent"] = agent
        return agent

    def set_agent(self, agent: str) -> None:
        self.session().headers["User-Agent"] = agent

    def current_agent(self) -> str:
        return self.session().headers.get("User-Agent", "")

    def rotate_proxy(self) -> dict[str, str] | None:
        if self.proxy_pool is None or not self.proxy_pool.enabled:
            return None
        proxy = self.proxy_pool.next()
        session = self.session()
        session.proxies.clear()
        if proxy:
            session.proxies.update(proxy)
        return proxy

    # ------------------------------------------------------------------ #
    # requests
    # ------------------------------------------------------------------ #
    @property
    def stats(self) -> dict[str, int]:
        with self._counter_lock:
            return {"requests": self._requests_made, "errors": self._errors}

    def last_error(self) -> str:
        """Human readable description of the last network failure in this thread."""
        return getattr(self._last_error, "message", "")

    def _scheme_candidates(self, url: str) -> list[str]:
        """URLs to try for a request: cached scheme first, then the alternate.

        Leak-list targets rarely state a scheme (or state the wrong one), so a
        failed TLS/connect attempt is retried once over the other scheme and
        the winner is remembered per host for the rest of the run.
        """
        try:
            parts = urlsplit(url)
        except ValueError:
            return [url]
        if parts.scheme not in ("http", "https") or not parts.netloc:
            return [url]
        scheme = parts.scheme
        other = "http" if scheme == "https" else "https"
        cached = self._scheme_cache.get(parts.netloc.lower())
        ordered = [cached, scheme, other] if cached else [scheme, other]
        prefix_len = len(scheme) + 3
        urls: list[str] = []
        seen: set[str] = set()
        for candidate in ordered:
            if candidate in seen:
                continue
            seen.add(candidate)
            urls.append(url if candidate == scheme else f"{candidate}://{url[prefix_len:]}")
        return urls

    def request(
        self,
        method: str,
        url: str,
        *,
        session: requests.Session | None = None,
        timeout: float | None = None,
        allow_redirects: bool | None = None,
        tolerate_errors: bool = True,
        **kwargs,
    ) -> requests.Response | None:
        """Perform a request returning ``None`` when the network fails."""
        session = session or self.session()
        if self.delay:
            time.sleep(self.delay)
        if self.proxy_pool is not None and self.proxy_pool.count > 1:
            proxy = self.proxy_pool.next()
            if proxy:
                session.proxies = proxy

        read_timeout = float(timeout if timeout is not None else self.timeout)
        if kwargs.pop("_raw_timeout", False):
            timeouts: float | tuple[float, float] = read_timeout
        else:
            timeouts = (min(self.connect_timeout, read_timeout), read_timeout)

        if allow_redirects is None:
            allow_redirects = self.redirects

        candidates = self._scheme_candidates(url)
        for index, candidate in enumerate(candidates):
            with self._counter_lock:
                self._requests_made += 1
            try:
                response = session.request(
                    method.upper(),
                    candidate,
                    timeout=timeouts,
                    allow_redirects=allow_redirects,
                    **kwargs,
                )
            except requests.exceptions.RequestException as exc:
                with self._counter_lock:
                    self._errors += 1
                swappable = isinstance(
                    exc, (requests.exceptions.SSLError, requests.exceptions.ConnectionError)
                )
                if swappable and index + 1 < len(candidates):
                    if self.verbose and self.log:
                        self.log(
                            c(
                                f"  {candidate} failed ({exc.__class__.__name__}), retrying {candidates[index + 1]}",
                                "yellow",
                            )
                        )
                    continue
                message = f"{exc.__class__.__name__}: {str(exc).strip() or 'request failed'}"
                self._last_error.message = message[:240]
                if self.verbose and self.log:
                    self.log(c(f"  network error {method.upper()} {candidate}: {message[:120]}", "yellow"))
                if tolerate_errors:
                    return None
                raise
            parts = urlsplit(candidate)
            self._scheme_cache[parts.netloc.lower()] = parts.scheme
            return response

    def get(self, url: str, **kwargs) -> requests.Response | None:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs) -> requests.Response | None:
        return self.request("POST", url, **kwargs)

    # ------------------------------------------------------------------ #
    def describe(self) -> str:
        bits = [f"timeout {self.timeout:g}s", f"retries {self.retries}"]
        if self.verify is False:
            bits.append("TLS verify off")
        if self.proxy_pool is not None and self.proxy_pool.enabled:
            bits.append(f"{self.proxy_pool.count} proxy(ies)")
        elif self.static_proxies:
            bits.append("proxy on")
        bits.append(f"UA {self.agents.describe()}")
        return ", ".join(bits)

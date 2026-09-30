"""Shared HTTP plumbing for every external API we talk to.

Every integration subclasses `BaseAPIClient` and gets, for free:

* a connection-pooled `requests.Session` (one per client instance)
* a hard timeout on every call — an external API that hangs must never hold a
  Django worker open indefinitely, which is the fastest way to take down a
  30-50 user app
* bounded retries with exponential backoff, on idempotent methods only
* one exception type (`UpstreamServiceError` -> HTTP 502) instead of leaking
  `requests` exceptions up into the view layer
* optional response caching
* logging that never prints the API key

Adding a second integration means writing a subclass and a config block in
settings.INTEGRATIONS — no copy-pasted retry/timeout logic.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Mapping
from urllib.parse import urljoin

import requests
from django.conf import settings
from django.core.cache import cache
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from core.exceptions import UpstreamServiceError

logger = logging.getLogger(__name__)

# Methods that are safe to replay after a failure. POST is deliberately absent:
# retrying a non-idempotent write can double-charge, double-send or
# double-create. A subclass that knows its POST is idempotent (e.g. it sends an
# idempotency key) can override `retry_methods`.
IDEMPOTENT_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "PUT", "DELETE"})


class IntegrationConfigError(RuntimeError):
    """Raised at construction when required configuration is missing.

    Failing loudly at startup/first use beats discovering an empty API key as a
    401 from the vendor in production.
    """


class BaseAPIClient:
    #: Key into settings.INTEGRATIONS.
    config_key: str = ""
    #: Set True on subclasses whose vendor genuinely requires a key.
    requires_api_key: bool = False
    #: Default seconds to cache successful GETs (0 disables).
    cache_ttl: int = 0
    retry_methods: frozenset[str] = IDEMPOTENT_METHODS

    def __init__(self, base_url: str | None = None, api_key: str | None = None,
                 timeout: int | None = None):
        conf = settings.INTEGRATIONS.get(self.config_key, {}) if self.config_key else {}
        self.base_url = (base_url or conf.get("BASE_URL") or "").rstrip("/") + "/"
        self.api_key = api_key if api_key is not None else conf.get("API_KEY", "")
        self.timeout = timeout or conf.get("TIMEOUT", 10)

        if self.base_url == "/":
            raise IntegrationConfigError(
                f"{type(self).__name__}: no BASE_URL configured "
                f"(settings.INTEGRATIONS['{self.config_key}']['BASE_URL'])."
            )
        if self.requires_api_key and not self.api_key:
            raise IntegrationConfigError(
                f"{type(self).__name__}: API key is required but not configured."
            )

        self.session = self._build_session()

    # -- session -----------------------------------------------------------

    def _build_session(self) -> requests.Session:
        session = requests.Session()
        retry = Retry(
            total=getattr(settings, "HTTP_CLIENT_MAX_RETRIES", 2),
            backoff_factor=getattr(settings, "HTTP_CLIENT_BACKOFF", 0.5),
            # 429 and 5xx are transient; 4xx client errors are not and must not
            # be retried.
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=self.retry_methods,
            raise_on_status=False,
            respect_retry_after_header=True,
        )
        adapter = HTTPAdapter(max_retries=retry, pool_connections=10, pool_maxsize=20)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        session.headers.update(self.default_headers())
        return session

    def default_headers(self) -> dict[str, str]:
        return {"Accept": "application/json", "User-Agent": "backend-integration/1.0"}

    def auth_headers(self) -> dict[str, str]:
        """Override per vendor (Bearer, X-Api-Key, Basic, ...)."""
        return {}

    def close(self) -> None:
        self.session.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()

    # -- request -----------------------------------------------------------

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json_body: Any = None,
        headers: Mapping[str, str] | None = None,
        use_cache: bool | None = None,
    ) -> Any:
        method = method.upper()
        url = urljoin(self.base_url, path.lstrip("/"))

        should_cache = (
            self.cache_ttl > 0
            and method == "GET"
            and (use_cache if use_cache is not None else True)
        )
        cache_key = self._cache_key(url, params) if should_cache else None
        if cache_key:
            hit = cache.get(cache_key)
            if hit is not None:
                logger.debug("%s cache hit %s", type(self).__name__, url)
                return hit

        merged = {**self.auth_headers(), **(headers or {})}
        try:
            response = self.session.request(
                method,
                url,
                params=params,
                json=json_body,
                headers=merged,
                timeout=self.timeout,
            )
        except requests.Timeout as exc:
            logger.warning("%s timeout calling %s", type(self).__name__, url)
            raise UpstreamServiceError(
                f"{self.service_name} did not respond in time."
            ) from exc
        except requests.RequestException as exc:
            logger.warning("%s transport error calling %s: %s", type(self).__name__, url, exc)
            raise UpstreamServiceError(
                f"{self.service_name} could not be reached."
            ) from exc

        data = self._handle_response(response, url)

        if cache_key:
            cache.set(cache_key, data, self.cache_ttl)
        return data

    def get(self, path: str, **kwargs) -> Any:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs) -> Any:
        return self.request("POST", path, **kwargs)

    # -- response ----------------------------------------------------------

    def _handle_response(self, response: requests.Response, url: str) -> Any:
        if response.status_code >= 400:
            # Truncate: a vendor error page can be a megabyte of HTML and we do
            # not want that in the log or the exception message.
            body = (response.text or "")[:500]
            logger.warning(
                "%s HTTP %s from %s: %s",
                type(self).__name__,
                response.status_code,
                url,
                body,
            )
            raise UpstreamServiceError(
                f"{self.service_name} returned HTTP {response.status_code}."
            )

        if not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            # A 200 carrying HTML (captive portal, vendor maintenance page) is a
            # failure, not an empty result. Callers must not see None here.
            logger.warning(
                "%s returned non-JSON body from %s (content-type=%s)",
                type(self).__name__,
                url,
                response.headers.get("Content-Type"),
            )
            raise UpstreamServiceError(
                f"{self.service_name} returned an unreadable response."
            ) from exc

    # -- helpers -----------------------------------------------------------

    @property
    def service_name(self) -> str:
        return (self.config_key or type(self).__name__).replace("_", " ").title()

    def _cache_key(self, url: str, params: Mapping[str, Any] | None) -> str:
        raw = json.dumps(
            {"url": url, "params": dict(sorted((params or {}).items()))},
            sort_keys=True,
            default=str,
        )
        digest = hashlib.sha256(raw.encode()).hexdigest()
        return f"integration:{type(self).__name__}:{digest}"

    def health_check(self) -> dict[str, Any]:
        """Cheap probe used by the integrations status endpoint.

        Subclasses override with the vendor's lightest endpoint.
        """
        return {"service": self.service_name, "configured": bool(self.base_url)}

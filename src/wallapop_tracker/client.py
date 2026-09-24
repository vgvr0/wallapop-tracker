"""Centralized asynchronous HTTP client for authorized public data access."""

import asyncio
import hashlib
import json
import logging
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from .domain.metadata import AvailableFilter, Brand, Category, ProductModel
from .exceptions import (
    WallapopHTTPError,
    WallapopNotFoundError,
    WallapopPaginationError,
    WallapopParseError,
    WallapopRateLimitError,
)
from .models import ItemsPage, Listing, Profile, ProfileStats, ReviewSummary
from .observability import get_metrics, log_event, operation_for_url, status_class
from .parsers.brands import parse_brands
from .parsers.categories import parse_categories
from .parsers.common import parse_condition
from .parsers.filters import parse_available_filters
from .parsers.items import parse_item, parse_items_page
from .parsers.models import parse_models
from .parsers.profile import parse_profile
from .parsers.reviews import parse_review_summary
from .parsers.stats import parse_profile_stats

logger = logging.getLogger(__name__)


def _listing_coordinate(raw: Mapping[str, Any], name: str) -> float | None:
    value = raw.get(name)
    location = raw.get("location")
    if value is None and isinstance(location, Mapping):
        value = location.get(name)
    return float(value) if isinstance(value, (int, float)) else None


class _SharedRateLimiter:
    """Process-local limiter shared by clients targeting one Wallapop host."""

    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.last_request_at = 0.0
        self.blocked_until = 0.0

    async def acquire(self, min_interval: float) -> None:
        async with self.lock:
            now = asyncio.get_running_loop().time()
            wait = max(
                0.0,
                min_interval - (now - self.last_request_at),
                self.blocked_until - now,
            )
            if wait:
                await asyncio.sleep(wait)
            self.last_request_at = asyncio.get_running_loop().time()

    async def block(self, delay: float) -> float:
        async with self.lock:
            deadline = asyncio.get_running_loop().time() + delay
            self.blocked_until = max(
                self.blocked_until,
                deadline,
            )
            return deadline

    async def clear_block(self, deadline: float) -> None:
        async with self.lock:
            if self.blocked_until <= deadline:
                self.blocked_until = min(self.blocked_until, asyncio.get_running_loop().time())


_RATE_LIMITERS: dict[str, _SharedRateLimiter] = {}


def _rate_limiter_for(base_url: str) -> _SharedRateLimiter:
    return _RATE_LIMITERS.setdefault(base_url, _SharedRateLimiter())


def _metadata_search_params(
    query: str | None, category_id: str | None, order_by: str
) -> dict[str, str]:
    params = {"order_by": order_by, "source": "search_box"}
    if query is not None:
        params["keywords"] = query
    if category_id is not None:
        params["category_id"] = category_id
    return params


class WallapopClient:
    """Read-only client with conservative retries and optional raw capture."""

    def __init__(
        self,
        *,
        base_url: str = "https://api.wallapop.com",
        web_base_url: str = "https://es.wallapop.com",
        timeout: float = 20.0,
        max_retries: int = 3,
        backoff_factor: float = 0.5,
        max_retry_after: float = 60.0,
        min_interval: float = 0.25,
        user_agent: str = "wallapop-profile-tracker/1.0 (authorized-read-only)",
        raw_data_dir: Path | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.web_base_url = web_base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor
        self.max_retry_after = max_retry_after
        self.min_interval = min_interval
        self.user_agent = user_agent
        self.raw_data_dir = raw_data_dir
        self._http: httpx.AsyncClient | None = None
        self._rate_limiter = _rate_limiter_for(self.base_url)
        self.health_counters = {
            "http_requests": 0,
            "http_errors": 0,
            "http_403": 0,
            "http_429": 0,
            "http_5xx": 0,
            "parse_errors": 0,
        }
        self.schema_observations: list[tuple[str, Any]] = []

    async def __aenter__(self) -> "WallapopClient":
        self._http = httpx.AsyncClient(timeout=self.timeout)
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    async def _request(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, str] | None = None,
        raw_kind: str | None = None,
        raw_key: str = "response",
        expect_json: bool = True,
    ) -> Any:
        if self._http is None:
            raise WallapopHTTPError("WallapopClient must be used as an async context manager")
        headers = {
            "Accept": "application/json" if expect_json else "text/html",
            "User-Agent": self.user_agent,
        }
        operation = operation_for_url(url)
        self._observability_operation = operation
        metrics = get_metrics()
        for attempt in range(self.max_retries + 1):
            await self._wait_for_rate_limit()
            try:
                response = await self._http.request(method, url, params=params, headers=headers)
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                self.health_counters["http_requests"] += 1
                self.health_counters["http_errors"] += 1
                metrics.wallapop_http_requests_total.labels(operation, method, "error").inc()
                if attempt >= self.max_retries:
                    raise WallapopHTTPError(f"Transient request failed: {url}") from exc
                await self._backoff(attempt, None)
                continue
            metrics.wallapop_http_requests_total.labels(
                operation, method, status_class(response.status_code)
            ).inc()
            self.health_counters["http_requests"] += 1
            if response.status_code == 403:
                self.health_counters["http_403"] += 1
            if response.status_code == 429:
                self.health_counters["http_429"] += 1
            if 500 <= response.status_code <= 599:
                self.health_counters["http_5xx"] += 1
            if response.is_error:
                self.health_counters["http_errors"] += 1
            if response.status_code == 404:
                raise WallapopNotFoundError(f"Resource not found: {url}")
            if response.status_code == 429:
                metrics.wallapop_http_429_total.labels(operation).inc()
                if attempt >= self.max_retries:
                    raise WallapopRateLimitError(f"Rate limited: {url}")
                await self._backoff(attempt, response.headers.get("Retry-After"))
                continue
            if response.status_code in {500, 502, 503, 504}:
                if attempt >= self.max_retries:
                    raise WallapopHTTPError(f"HTTP {response.status_code}: {url}")
                await self._backoff(attempt, response.headers.get("Retry-After"))
                continue
            if response.is_error:
                raise WallapopHTTPError(f"HTTP {response.status_code}: {url}")
            if expect_json:
                try:
                    raw_value = json.loads(response.text)
                    value = json.loads(response.text, parse_float=Decimal)
                except json.JSONDecodeError as exc:
                    self.health_counters["parse_errors"] += 1
                    metrics.wallapop_parse_errors_total.labels(operation).inc()
                    raise WallapopParseError(f"Invalid JSON response: {url}") from exc
            else:
                raw_value = response.text
                value = response.text
            if raw_kind is not None:
                self._save_raw(raw_kind, raw_key, raw_value)
                source = {
                    "search": "search",
                    "profile": "profile",
                    "items": "profile_items" if "/users/" in url else "listing_detail",
                }.get(raw_kind)
                if source is not None and isinstance(raw_value, (dict, list)):
                    self.schema_observations.append((source, raw_value))
            return value
        raise WallapopHTTPError(f"Request failed: {url}")

    async def _wait_for_rate_limit(self) -> None:
        await self._rate_limiter.acquire(self.min_interval)

    async def _backoff(self, attempt: int, retry_after: str | None) -> None:
        delay = self.backoff_factor * (2**attempt)
        if retry_after:
            try:
                delay = max(delay, min(float(retry_after), self.max_retry_after))
            except ValueError:
                try:
                    retry_at = parsedate_to_datetime(retry_after)
                    retry_delay = (retry_at - datetime.now(UTC)).total_seconds()
                    delay = max(delay, min(retry_delay, self.max_retry_after))
                except (TypeError, ValueError, OverflowError):
                    log_event(
                        logger,
                        logging.WARNING,
                        "invalid_retry_after",
                        operation=getattr(self, "_observability_operation", "request"),
                    )
        delay = max(0.0, delay)
        get_metrics().wallapop_http_retries_total.labels(
            getattr(self, "_observability_operation", "request")
        ).inc()
        log_event(
            logger,
            logging.WARNING,
            "retrying_wallapop_request",
            operation=getattr(self, "_observability_operation", "request"),
            attempt=attempt + 1,
        )
        deadline = await self._rate_limiter.block(delay)
        try:
            await asyncio.sleep(delay)
        finally:
            await self._rate_limiter.clear_block(deadline)

    def _save_raw(self, kind: str, key: str, value: Any) -> None:
        if self.raw_data_dir is None:
            return
        try:
            target_dir = self.raw_data_dir / kind
            target_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
            safe_key = re.sub(r"[^A-Za-z0-9_.-]", "_", key)[:80]
            path = target_dir / f"{timestamp}_{safe_key}.json"
            path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        except (OSError, TypeError, ValueError) as exc:
            logger.warning("raw_write_failed kind=%s key=%s error=%s", kind, key, exc)

    async def get_profile(self, user_id: str) -> Profile:
        data = await self._request(
            "GET",
            f"{self.base_url}/api/v3/users/{user_id}",
            raw_kind="profile",
            raw_key=user_id,
        )
        if not isinstance(data, Mapping):
            raise WallapopParseError("Profile endpoint did not return an object")
        return parse_profile(data)

    async def get_profile_stats(self, user_id: str) -> ProfileStats:
        data = await self._request(
            "GET",
            f"{self.base_url}/api/v3/users/{user_id}/stats",
            raw_kind="stats",
            raw_key=user_id,
        )
        if not isinstance(data, Mapping):
            raise WallapopParseError("Stats endpoint did not return an object")
        return parse_profile_stats(data)

    async def get_review_summary(self, user_id: str) -> ReviewSummary:
        data = await self._request(
            "GET",
            f"{self.base_url}/api/v3/users/{user_id}/reviews/summary",
            raw_kind="reviews",
            raw_key=user_id,
        )
        if not isinstance(data, Mapping):
            raise WallapopParseError("Review summary endpoint did not return an object")
        return parse_review_summary(data)

    async def get_items_page(self, user_id: str, *, since: str | None = None) -> ItemsPage:
        params = {"since": since} if since is not None else None
        cursor_value = since or "first"
        cursor_label = re.sub(r"[^A-Za-z0-9_.-]", "_", cursor_value)[:24]
        cursor_hash = hashlib.sha256(cursor_value.encode()).hexdigest()[:12]
        raw_key = f"{user_id}_cursor-{cursor_label}-{cursor_hash}"
        data = await self._request(
            "GET",
            f"{self.base_url}/api/v3/users/{user_id}/items",
            params=params,
            raw_kind="items",
            raw_key=raw_key,
        )
        if not isinstance(data, Mapping):
            raise WallapopParseError("Items endpoint did not return an object")
        return parse_items_page(data, user_id=user_id)

    async def get_all_items(self, user_id: str) -> list[Listing]:
        items: list[Listing] = []
        seen_ids: set[str] = set()
        seen_cursors: set[str] = set()
        since: str | None = None
        pages = 0
        while True:
            page = await self.get_items_page(user_id, since=since)
            pages += 1
            for item in page.items:
                if item.item_id in seen_ids:
                    logger.warning("duplicate_item item_id=%s", item.item_id)
                    continue
                seen_ids.add(item.item_id)
                items.append(item)
            logger.info(
                "fetched_items_page items=%s has_next=%s",
                len(page.items),
                bool(page.next_since),
            )
            if page.next_since is None:
                break
            if page.next_since in seen_cursors:
                raise WallapopPaginationError(f"Repeated pagination cursor: {page.next_since}")
            seen_cursors.add(page.next_since)
            since = page.next_since
        logger.info("fetched_all_items user_id=%s total=%s pages=%s", user_id, len(items), pages)
        return items

    async def get_item(self, item_id: str) -> Listing:
        """Fetch one public listing detail from the observed v3 item endpoint."""
        item_id = item_id.strip()
        if not item_id:
            raise ValueError("item_id is required")
        data = await self._request(
            "GET",
            f"{self.base_url}/api/v3/items/{item_id}",
            raw_kind="items",
            raw_key=f"detail-{item_id}",
        )
        return parse_item(data, item_id=item_id)

    async def categories(self, *, context: str | None = None) -> list[Category]:
        """Fetch the observed public category catalog."""
        params = {"context": context} if context is not None else None
        data = await self._request(
            "GET",
            f"{self.base_url}/api/v3/categories",
            params=params,
            raw_kind="metadata",
            raw_key="categories",
        )
        return parse_categories(data)

    async def available_filters(
        self,
        *,
        query: str | None = None,
        category_id: str | None = None,
        order_by: str = "most_relevance",
    ) -> list[AvailableFilter]:
        """Fetch filters exposed for one observed search context."""
        params = _metadata_search_params(query, category_id, order_by)
        data = await self._request(
            "GET",
            f"{self.base_url}/api/v3/search/filters/regular-filters",
            params=params,
            raw_kind="metadata",
            raw_key="filters",
        )
        return parse_available_filters(data)

    async def brands(
        self,
        *,
        query: str | None = None,
        category_id: str | None = None,
        order_by: str = "most_relevance",
    ) -> list[Brand]:
        """Fetch the observed brand options for one search context."""
        data = await self._request(
            "GET",
            f"{self.base_url}/api/v3/search/filters/brand",
            params=_metadata_search_params(query, category_id, order_by),
            raw_kind="metadata",
            raw_key="brands",
        )
        return parse_brands(data)

    async def models(
        self,
        *,
        query: str | None = None,
        category_id: str | None = None,
        order_by: str = "most_relevance",
    ) -> list[ProductModel]:
        """Fetch the observed model options for one search context."""
        data = await self._request(
            "GET",
            f"{self.base_url}/api/v3/search/filters/model",
            params=_metadata_search_params(query, category_id, order_by),
            raw_kind="metadata",
            raw_key="models",
        )
        return parse_models(data)

    async def search_items(
        self,
        *,
        query: str | None = None,
        keywords: str | None = None,
        category_id: str | None = None,
        min_price: Decimal | None = None,
        max_price: Decimal | None = None,
        condition: str | None = None,
        brand: str | None = None,
        shipping_required: bool | None = None,
        latitude: float | None = None,
        longitude: float | None = None,
        distance: float | None = None,
        max_pages: int = 5,
    ) -> list[Listing]:
        """Search public listings through Wallapop's v3 search endpoint.

        Results are limited to the first ``max_pages`` newest pages. This is
        intentionally a recent-items alert window, not an exhaustive search.
        Filters not consistently supported server-side are applied locally so
        the saved-search semantics stay deterministic.
        """
        if query is not None and keywords is not None and query != keywords:
            raise ValueError("query and keywords must match when both are supplied")
        search_query = (query if query is not None else keywords or "").strip()
        if not search_query:
            raise ValueError("query is required")
        params: dict[str, str] = {
            "keywords": search_query,
            "source": "search_box",
            "order_by": "newest",
        }
        if category_id is not None:
            params["category_id"] = category_id
        if min_price is not None:
            params["min_price"] = str(min_price)
        if max_price is not None:
            params["max_price"] = str(max_price)
        if latitude is not None:
            params["latitude"] = str(latitude)
        if longitude is not None:
            params["longitude"] = str(longitude)
        if distance is not None:
            params["distance"] = str(distance)
        if max_pages < 1:
            raise ValueError("max_pages must be positive")
        result: list[Listing] = []
        seen_ids: set[str] = set()
        seen_cursors: set[str] = set()
        next_page: str | None = None
        for _ in range(max_pages):
            request_params = dict(params)
            if next_page is not None:
                request_params["next_page"] = next_page
            data = await self._request(
                "GET",
                f"{self.base_url}/api/v3/search",
                params=request_params,
                raw_kind="search",
                raw_key=f"{search_query}_page-{len(result)}",
            )
            payload = data.get("data") if isinstance(data, Mapping) else None
            if isinstance(payload, Mapping):
                raw_items = payload.get("items", [])
            elif isinstance(payload, list):
                raw_items = payload
            else:
                raw_items = []
            if not isinstance(raw_items, list):
                raw_items = []
            for raw in raw_items:
                if not isinstance(raw, Mapping) or not raw.get("id"):
                    continue
                item_id = str(raw["id"])
                if item_id in seen_ids:
                    continue
                seen_ids.add(item_id)
                price = raw.get("price")
                currency = None
                if isinstance(price, Mapping):
                    currency = price.get("currency")
                    price = price.get("amount")
                seller = raw.get("user_id")
                if isinstance(seller, Mapping):
                    seller = seller.get("id")
                shipping = raw.get("shipping")
                condition_code, condition_label = parse_condition(
                    raw.get("condition"), raw.get("type_attributes") or raw.get("attributes")
                )
                item = Listing(
                    item_id=item_id,
                    user_id=str(seller or ""),
                    title=raw.get("title"),
                    description=raw.get("description"),
                    price=price,
                    currency=currency,
                    category_id=str(raw.get("category_id"))
                    if raw.get("category_id") is not None
                    else None,
                    category_name=raw.get("category_name"),
                    brand=raw.get("brand"),
                    model=(raw.get("model") or (raw.get("attributes") or {}).get("model"))
                    if isinstance(raw.get("attributes") or {}, Mapping)
                    else None,
                    latitude=_listing_coordinate(raw, "latitude"),
                    longitude=_listing_coordinate(raw, "longitude"),
                    condition=condition_code or condition_label,
                    condition_code=condition_code,
                    condition_label=condition_label,
                    status=raw.get("status"),
                    reserved=raw.get("reserved"),
                    shipping_available=(shipping or {}).get("item_is_shippable")
                    if isinstance(shipping, Mapping)
                    else None,
                    url=f"{self.web_base_url}/item/{raw.get('web_slug')}"
                    if raw.get("web_slug")
                    else None,
                    image_url=raw.get("main_image_url") or raw.get("image_url"),
                    images_json=raw.get("images") if isinstance(raw.get("images"), list) else None,
                    attributes_json=(
                        raw.get("attributes")
                        if isinstance(raw.get("attributes"), Mapping)
                        else None
                    ),
                    created_at=raw.get("created_at"),
                    modified_at=raw.get("modified_at"),
                )
                if min_price is not None and (item.price is None or item.price < min_price):
                    continue
                if max_price is not None and (item.price is None or item.price > max_price):
                    continue
                if condition is not None and item.condition != condition:
                    continue
                if brand is not None and item.brand != brand:
                    continue
                if shipping_required and item.shipping_available is not True:
                    continue
                result.append(item)
            meta = data.get("meta", {}) if isinstance(data, Mapping) else {}
            candidate = meta.get("next_page") if isinstance(meta, Mapping) else None
            if not isinstance(candidate, str) or not candidate:
                break
            if candidate in seen_cursors:
                break
            seen_cursors.add(candidate)
            next_page = candidate
        return result

    async def resolve_user_id(self, profile_url: str) -> str:
        """Resolve canonical API IDs locally when the URL carries a 12-char ID.

        The example URL ends in a legacy numeric public identifier, which is
        not the canonical API ID observed in the current SSR payload; it uses
        the SSR fallback. A URL ending in ``-<12 lowercase alphanumeric chars>``
        is treated as locally resolvable.
        """
        parsed = urlparse(profile_url)
        if parsed.scheme not in {"http", "https"} or parsed.hostname not in {
            "es.wallapop.com",
            "www.wallapop.com",
        }:
            raise WallapopParseError("Unsupported Wallapop profile URL")
        match = re.fullmatch(r"/user/([A-Za-z0-9][A-Za-z0-9_-]*)(?:/)?", parsed.path)
        if match is None:
            raise WallapopParseError("URL is not a public Wallapop profile URL")
        path_value = match.group(1)
        local_match = re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*-([a-z0-9]{12})", path_value)
        if local_match is not None:
            logger.debug("resolved_user_id_from_url user_id=%s", local_match.group(1))
            return local_match.group(1)
        html = await self._request("GET", profile_url, expect_json=False)
        if not isinstance(html, str):
            raise WallapopParseError("Profile page did not return HTML")
        script = re.search(
            r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
            html,
            re.DOTALL,
        )
        if script is None:
            raise WallapopParseError("Profile HTML has no __NEXT_DATA__")
        try:
            next_data = json.loads(script.group(1))
            user_id = next_data["props"]["pageProps"]["user"]["id"]
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise WallapopParseError("Invalid __NEXT_DATA__ profile payload") from exc
        if not isinstance(user_id, str) or not user_id:
            raise WallapopParseError("__NEXT_DATA__ has no valid user id")
        return user_id

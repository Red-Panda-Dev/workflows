"""Cloudflare Browser Rendering API client for CentralDepo workflow."""

import asyncio
import logging
import random
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import aiohttp

from .config import (
    CIRCUIT_BREAKER_MAX_FAILURES,
    CIRCUIT_BREAKER_RESET_TIMEOUT,
    CONNECTION_TIMEOUT,
    INITIAL_DELAY,
    MAX_CONCURRENT_SCRAPES,
    MAX_DELAY,
    MAX_RETRIES,
    MIN_DELAY,
    RETRY_BACKOFF_BASE,
    RETRY_BACKOFF_MAX,
    SCRAPE_API,
    SELECTOR,
)
from .models import ScrapeResult
from .parser import parse_items

logger = logging.getLogger(__name__)

# Common user agents to rotate through (avoids fake_useragent sandbox issue)
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
]


def get_random_user_agent() -> str:
    """Return a random user agent string."""
    return random.choice(USER_AGENTS)


class CircuitBreaker:
    """Circuit breaker to prevent cascading failures when Cloudflare API is unavailable.

    Opens after consecutive failures, preventing further requests until reset timeout elapses.
    This prevents overwhelming a failing API and allows it time to recover.
    """

    def __init__(
        self,
        max_failures: int = CIRCUIT_BREAKER_MAX_FAILURES,
        reset_timeout: int = CIRCUIT_BREAKER_RESET_TIMEOUT,
    ):
        """Initialize circuit breaker.

        Args:
            max_failures: Consecutive failures before circuit opens
            reset_timeout: Seconds to wait before attempting to close circuit
        """
        self.max_failures = max_failures
        self.reset_timeout = reset_timeout
        self.failures: int = 0
        self.opened: bool = False
        self.last_failure_time: float | None = None

    def record_failure(self) -> None:
        """Record a failure. Opens circuit if threshold exceeded."""
        self.failures += 1
        self.last_failure_time = time.monotonic()
        if self.failures >= self.max_failures:
            self.opened = True
            logger.warning(
                "Circuit breaker OPENED after %d failures. Will retry in %ds.",
                self.failures,
                self.reset_timeout,
            )

    def record_success(self) -> None:
        """Record a success. Resets failure count and closes circuit."""
        self.failures = 0
        self.opened = False

    def can_request(self) -> bool:
        """Check if requests are allowed through the circuit.

        Returns:
            True if circuit is closed or reset timeout has elapsed
        """
        if not self.opened:
            return True

        elapsed = time.monotonic() - (self.last_failure_time or 0)
        if elapsed >= self.reset_timeout:
            self.opened = False
            self.failures = 0
            logger.info("Circuit breaker CLOSED after timeout")
            return True

        return False


class RateLimiter:
    """Adaptive rate limiter based on Cloudflare API responses.

    Dynamically adjusts delay between requests based on:
    - Explicit Retry-After headers from 429 responses
    - Failure patterns (exponential backoff on failures)
    - Success patterns (gradual delay reduction)
    """

    def __init__(
        self,
        initial_delay: float = INITIAL_DELAY,
        min_delay: float = MIN_DELAY,
        max_delay: float = MAX_DELAY,
    ):
        """Initialize rate limiter.

        Args:
            initial_delay: Starting delay between requests
            min_delay: Minimum allowed delay
            max_delay: Maximum allowed delay
        """
        self.delay = initial_delay
        self.min_delay = min_delay
        self.max_delay = max_delay
        self._lock = asyncio.Lock()

    async def adjust_on_failure(self, retry_after: int | None = None) -> None:
        """Increase delay after a failure.

        Args:
            retry_after: Optional Retry-After header value from 429 response
        """
        async with self._lock:
            if retry_after is not None:
                # Respect explicit Retry-After header from Cloudflare
                self.delay = max(self.min_delay, min(self.max_delay, float(retry_after)))
                logger.info("Rate limiter: adjusted delay to %s (Retry-After: %s)", self.delay, retry_after)
            else:
                # Exponential backoff
                self.delay = min(self.max_delay, self.delay * 2)

    async def adjust_on_success(self) -> None:
        """Gradually reduce delay after successful requests."""
        async with self._lock:
            self.delay = max(self.min_delay, self.delay * 0.8)

    async def get_delay(self) -> float:
        """Get current delay value."""
        async with self._lock:
            return self.delay


class CloudflareSessionManager:
    """Manages aiohttp ClientSession for Cloudflare API calls with connection pooling.

    Provides:
    - Connection pooling and reuse (reduces TCP/SSL handshake overhead)
    - Global rate limiting via semaphore
    - Circuit breaker for API health
    - Adaptive rate limiting

    Usage:
        async with CloudflareSessionManager(account_id, api_token) as mgr:
            client = CloudflareClient(account_id, api_token, session_manager=mgr)
            result = await client.scrape_page(1, url, timeout)
    """

    def __init__(
        self,
        account_id: str,
        api_token: str,
        max_concurrent: int = MAX_CONCURRENT_SCRAPES,
    ):
        """Initialize session manager.

        Args:
            account_id: Cloudflare account ID
            api_token: Cloudflare API token with Browser Rendering permissions
            max_concurrent: Maximum concurrent requests (connection pool size)
        """
        self.account_id = account_id
        self.api_token = api_token
        self.max_concurrent = max_concurrent
        self.api_url = SCRAPE_API.format(account_id=account_id)
        self.headers = {
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json",
        }
        self._session: aiohttp.ClientSession | None = None
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._circuit_breaker = CircuitBreaker()
        self._rate_limiter = RateLimiter()

    async def __aenter__(self) -> CloudflareSessionManager:
        """Enter async context manager - creates session."""
        connector = aiohttp.TCPConnector(
            limit=self.max_concurrent,
            force_close=True,
        )
        timeout = aiohttp.ClientTimeout(total=CONNECTION_TIMEOUT, connect=10)
        self._session = aiohttp.ClientSession(
            connector=connector,
            timeout=timeout,
            headers=self.headers,
        )
        logger.debug("Cloudflare session created with %d max connections", self.max_concurrent)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Exit async context manager - closes session."""
        if self._session:
            await self._session.close()
            logger.debug("Cloudflare session closed")
            self._session = None

    @property
    def session(self) -> aiohttp.ClientSession:
        """Get the aiohttp session. Raises if not initialized."""
        if not self._session:
            raise RuntimeError("Session not initialized. Use async context manager.")
        return self._session

    @property
    def rate_limiter(self) -> RateLimiter:
        """Get the rate limiter instance."""
        return self._rate_limiter

    @property
    def circuit_breaker(self) -> CircuitBreaker:
        """Get the circuit breaker instance."""
        return self._circuit_breaker

    async def acquire_semaphore(self) -> AsyncIterator[None]:
        """Async context manager for semaphore acquisition."""
        await self._semaphore.acquire()
        try:
            yield
        finally:
            self._semaphore.release()

    @asynccontextmanager
    async def request_slot(self):
        """Acquire semaphore and check circuit breaker for a request slot."""
        async with self.acquire_semaphore():
            if not self._circuit_breaker.can_request():
                # Circuit is open, wait for timeout or skip
                wait_time = self._circuit_breaker.reset_timeout - (
                    time.monotonic() - (self._circuit_breaker.last_failure_time or 0)
                )
                if wait_time > 0:
                    logger.warning("Circuit open, waiting %s seconds", wait_time)
                    await asyncio.sleep(wait_time)
                    # Re-check after waiting
                    if not self._circuit_breaker.can_request():
                        raise RuntimeError("Circuit breaker still open after timeout")
            yield


class CloudflareClient:
    """Client for Cloudflare Browser Rendering API.

    Handles authentication, request sending, retry logic, and rate limiting.
    Supports both individual page scraping and batch scraping for better performance.

    Args:
        account_id: Cloudflare account ID
        api_token: Cloudflare API token with Browser Rendering permissions
        session_manager: Optional shared session manager for connection pooling.
                         If not provided, creates its own session per request (legacy mode).
    """

    def __init__(
        self,
        account_id: str,
        api_token: str,
        session_manager: CloudflareSessionManager | None = None,
    ):
        """Initialize client with Cloudflare credentials.

        Args:
            account_id: Cloudflare account ID
            api_token: Cloudflare API token with Browser Rendering permissions
            session_manager: Optional shared session manager for connection pooling
        """
        self.account_id = account_id
        self.api_token = api_token
        self.session_manager = session_manager
        self.api_url = SCRAPE_API.format(account_id=account_id)
        self.headers = {
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json",
        }

    def _get_session(self) -> aiohttp.ClientSession:
        """Get session - either from manager or create new one."""
        if self.session_manager:
            return self.session_manager.session
        # Legacy mode: create new session (will be closed after request)
        # This is less efficient but maintains backward compatibility
        return aiohttp.ClientSession()

    def _get_api_url(self) -> str:
        """Get API URL - either from client or manager."""
        if self.session_manager:
            return self.session_manager.api_url
        return self.api_url

    async def _make_request(
        self,
        page: int,
        url: str,
        payload: dict,
        timeout: int,
        attempt: int,
    ) -> tuple[ScrapeResult, bool]:
        """Make a single API request with retry logic. Returns (result, should_retry).

        Internal method used by both scrape_page and batch scraping.
        """
        use_manager = self.session_manager is not None

        # Use session from manager if available
        if use_manager:
            session = self.session_manager.session
            api_url = self.session_manager.api_url
        else:
            session = aiohttp.ClientSession()
            api_url = self.api_url

        try:
            async with session.post(
                api_url,
                json=payload,
                headers=self.headers,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                # Handle rate limiting
                if resp.status == 429:
                    retry_after = resp.headers.get("Retry-After")
                    retry_after_val = int(retry_after) if retry_after else 5

                    logger.warning(
                        "Page %d attempt %d/%d: Rate limited (429), waiting %ds",
                        page,
                        attempt,
                        MAX_RETRIES,
                        retry_after_val,
                    )

                    # Record rate limit hit
                    if use_manager:
                        await self.session_manager.rate_limiter.adjust_on_failure(retry_after_val)

                    # Use Retry-After header value
                    await asyncio.sleep(retry_after_val)
                    return (
                        ScrapeResult(
                            page=page,
                            items=[],
                            success=False,
                            error=f"Rate limited, Retry-After: {retry_after_val}",
                        ),
                        True,
                    )

                # Handle server errors
                if resp.status >= 500:
                    text = await resp.text()
                    error_msg = f"HTTP {resp.status}: {text[:200]}"
                    logger.warning("Page %d attempt %d/%d: %s", page, attempt, MAX_RETRIES, error_msg)

                    # Server errors (5xx) are also retryable per-page
                    # Calculate backoff: exponential but capped
                    backoff = min(RETRY_BACKOFF_MAX, RETRY_BACKOFF_BASE**attempt)
                    if attempt < MAX_RETRIES:
                        await asyncio.sleep(backoff)
                    return (
                        ScrapeResult(page=page, items=[], success=False, error=error_msg),
                        attempt < MAX_RETRIES,
                    )

                # Handle client errors (4xx) - these are page-specific issues, not API failures
                # Do NOT trigger circuit breaker - allow retries
                if resp.status != 200:
                    text = await resp.text()
                    error_msg = f"HTTP {resp.status}: {text[:200]}"
                    logger.warning("Page %d attempt %d/%d: %s", page, attempt, MAX_RETRIES, error_msg)

                    # Client errors (4xx) like 422 (navigation timeout) are retryable
                    # Only 5xx errors trigger circuit breaker
                    return (
                        ScrapeResult(page=page, items=[], success=False, error=error_msg),
                        attempt < MAX_RETRIES,
                    )

                # Success - parse response
                data = await resp.json()

                if not data.get("success"):
                    error_msg = data.get("error", "Unknown error")
                    if isinstance(error_msg, dict):
                        error_msg = str(error_msg)
                    logger.error("Page %d: Cloudflare API returned success=false: %s", page, error_msg)

                    # Cloudflare API returned success=false - this is a page-level issue
                    # (e.g., navigation timeout, element not found). Retry the page.
                    return (
                        ScrapeResult(
                            page=page,
                            items=[],
                            success=False,
                            error=f"CF API error: {error_msg}",
                        ),
                        attempt < MAX_RETRIES,
                    )

                result_list = data.get("result") or []
                if not result_list:
                    logger.info("Page %d: Empty result list", page)

                    if use_manager:
                        await self.session_manager.rate_limiter.adjust_on_success()

                    return (ScrapeResult(page=page, items=[], success=True, error=None), False)

                # Find the selector block
                selector_block = None
                for block in result_list:
                    if block.get("selector") == SELECTOR:
                        selector_block = block
                        break

                if selector_block is None:
                    logger.warning("Page %d: No %s selector block in response", page, SELECTOR)

                    if use_manager:
                        await self.session_manager.rate_limiter.adjust_on_success()

                    return (ScrapeResult(page=page, items=[], success=True, error=None), False)

                items = selector_block.get("results") or []
                records = parse_items(items, page)

                logger.info("Page %d: Successfully scraped %d items", page, len(records))

                if use_manager:
                    await self.session_manager.rate_limiter.adjust_on_success()

                return (ScrapeResult(page=page, items=records, success=True, error=None), False)

        except (TimeoutError, aiohttp.ClientError) as e:
            error = f"{type(e).__name__}: {e}"
            logger.warning("Page %d attempt %d/%d: Network error: %s", page, attempt, MAX_RETRIES, error)

            # Network errors are retryable per-page, don't trigger circuit breaker
            # Only after all retries fail will this page be marked as failed
            if attempt < MAX_RETRIES:
                backoff = min(RETRY_BACKOFF_MAX, RETRY_BACKOFF_BASE**attempt)
                await asyncio.sleep(backoff)
            return (ScrapeResult(page=page, items=[], success=False, error=error), attempt < MAX_RETRIES)

        finally:
            # Close session only if we created it (not from manager)
            if not use_manager and session and not session.closed:
                await session.close()

    async def scrape_page(self, page: int, url: str, timeout: int) -> ScrapeResult:
        """Scrape a single page using Cloudflare Browser Rendering.

        Implements:
        - Retry logic with exponential backoff (MAX_RETRIES attempts)
        - Rate limiting respect (Retry-After header)
        - Error handling for various HTTP status codes
        - Parsing of response into DividendRecord objects
        - Connection pooling when using session_manager

        Args:
            page: 1-based page number (for logging)
            url: URL to scrape
            timeout: Request timeout in seconds

        Returns:
            ScrapeResult with items (List[DividendRecord]) or error info
        """
        payload = {
            "url": url,
            "userAgent": get_random_user_agent(),
            "waitForSelector": {"selector": SELECTOR, "timeout": 60000},
            "elements": [{"selector": SELECTOR}],
        }

        for attempt in range(1, MAX_RETRIES + 1):
            result, should_retry = await self._make_request(page, url, payload, timeout, attempt)

            if not should_retry:
                return result

        # All attempts failed
        logger.error("Page %d: All %d attempts failed", page, MAX_RETRIES)
        return ScrapeResult(
            page=page,
            items=[],
            success=False,
            error=f"All {MAX_RETRIES} attempts failed",
        )

    async def scrape_pages_batch(
        self,
        page_urls: list[tuple[int, str]],
        timeout: int,
    ) -> list[ScrapeResult]:
        """Scrape multiple pages in parallel using batch processing.

        This is the primary performance improvement over sequential scraping.
        All pages share the same session (connection pool) and execute concurrently
        within the semaphore limits.

        Args:
            page_urls: List of (page_number, url) tuples to scrape
            timeout: Request timeout in seconds for each page

        Returns:
            List of ScrapeResult objects in same order as input page_urls
        """
        if not page_urls:
            return []

        # Single pass with gather - no retry logic here as _make_request handles retries
        tasks = []
        for page, url in page_urls:
            payload = {
                "url": url,
                "userAgent": get_random_user_agent(),
                "waitForSelector": {"selector": SELECTOR, "timeout": 60000},
                "elements": [{"selector": SELECTOR}],
            }
            # For batch, we use attempt=1 and let _make_request handle retries internally
            task = asyncio.create_task(self._scrape_page_single(page, url, payload, timeout))
            tasks.append(task)

        results = await asyncio.gather(*tasks)
        return results

    async def _scrape_page_single(
        self,
        page: int,
        url: str,
        payload: dict,
        timeout: int,
    ) -> ScrapeResult:
        """Internal method for batch scraping a single page."""
        for attempt in range(1, MAX_RETRIES + 1):
            result, should_retry = await self._make_request(page, url, payload, timeout, attempt)
            if not should_retry:
                return result

        logger.error("Batch page %d: All %d attempts failed", page, MAX_RETRIES)
        return ScrapeResult(
            page=page,
            items=[],
            success=False,
            error=f"All {MAX_RETRIES} attempts failed",
        )

"""HTTP scraping client for CentralDepo workflow.

Uses direct aiohttp-based HTTP scraping with BeautifulSoup parsing.
"""

import asyncio
import logging
import random
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from urllib.parse import urljoin

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
    SELECTOR,
)
from .models import DividendRecord, ScrapeResult

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
    """Circuit breaker to prevent cascading failures when site is unavailable.

    Opens after consecutive failures, preventing further requests until reset timeout elapses.
    This prevents overwhelming a failing site and allows it time to recover.
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
    """Adaptive rate limiter based on API responses.

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
                # Respect explicit Retry-After header
                self.delay = max(
                    self.min_delay, min(self.max_delay, float(retry_after))
                )
                logger.info(
                    "Rate limiter: adjusted delay to %s (Retry-After: %s)",
                    self.delay,
                    retry_after,
                )
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


class AiohttpSessionManager:
    """Manages aiohttp ClientSession for direct HTTP scraping.

    Provides:
    - Connection pooling and reuse (reduces TCP/SSL handshake overhead)
    - Global rate limiting via semaphore
    - Circuit breaker for site health
    - Adaptive rate limiting

    Usage:
        async with AiohttpSessionManager() as mgr:
            client = AiohttpClient(session_manager=mgr)
            result = await client.scrape_page(1, url, timeout)
    """

    def __init__(
        self,
        max_concurrent: int = MAX_CONCURRENT_SCRAPES,
        timeout: int = CONNECTION_TIMEOUT,
    ):
        """Initialize session manager.

        Args:
            max_concurrent: Maximum concurrent requests (connection pool size)
            timeout: Connection timeout in seconds
        """
        self.max_concurrent = max_concurrent
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self._session: aiohttp.ClientSession | None = None
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._circuit_breaker = CircuitBreaker()
        self._rate_limiter = RateLimiter()

    async def __aenter__(self) -> AiohttpSessionManager:
        """Enter async context manager - creates session."""
        connector = aiohttp.TCPConnector(
            limit=self.max_concurrent,
            force_close=True,
        )
        self._session = aiohttp.ClientSession(
            connector=connector,
            timeout=self.timeout,
        )
        logger.debug(
            "Aiohttp session created with %d max connections", self.max_concurrent
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Exit async context manager - closes session."""
        if self._session:
            await self._session.close()
            logger.debug("Aiohttp session closed")
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


class AiohttpClient:
    """Client for direct HTTP scraping with aiohttp and BeautifulSoup.

    Handles:
    - HTTP GET requests with retries
    - HTML parsing with BeautifulSoup
    - Element extraction by CSS selector (.news-item)
    - Same retry/circuit breaker logic for robustness

    This is the primary scraping client for the workflow.

    Args:
        session_manager: Optional shared session manager for connection pooling.
                         If not provided, creates its own session per request.
    """

    def __init__(
        self,
        session_manager: AiohttpSessionManager | None = None,
    ):
        """Initialize client.

        Args:
            session_manager: Optional shared session manager for connection pooling
        """
        self.session_manager = session_manager

    def _get_session(self) -> aiohttp.ClientSession:
        """Get session - either from manager or create new one."""
        if self.session_manager:
            return self.session_manager.session
        # Legacy mode: create new session (will be closed after request)
        return aiohttp.ClientSession()

    async def scrape_page(
        self, page: int, url: str, timeout: int = 180
    ) -> ScrapeResult:
        """Scrape a single page using direct HTTP.

        Implements:
        - Retry logic with exponential backoff (MAX_RETRIES attempts)
        - Rate limiting respect (Retry-After header)
        - Error handling for various HTTP status codes
        - Parsing of HTML into DividendRecord objects
        - Connection pooling when using session_manager

        Args:
            page: 1-based page number (for logging)
            url: URL to scrape
            timeout: Request timeout in seconds

        Returns:
            ScrapeResult with items (List[DividendRecord]) or error info
        """
        for attempt in range(1, MAX_RETRIES + 1):
            result, should_retry = await self._fetch_and_parse(
                page, url, timeout, attempt
            )
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

    async def _fetch_and_parse(
        self, page: int, url: str, timeout: int, attempt: int
    ) -> tuple[ScrapeResult, bool]:
        """Fetch HTML and parse for .news-item elements."""
        use_manager = self.session_manager is not None
        session = (
            self.session_manager.session if use_manager else aiohttp.ClientSession()
        )

        try:
            headers = {"User-Agent": get_random_user_agent()}

            async with session.get(
                url,
                headers=headers,
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
                        await self.session_manager.rate_limiter.adjust_on_failure(
                            retry_after_val
                        )

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
                    logger.warning(
                        "Page %d attempt %d/%d: %s",
                        page,
                        attempt,
                        MAX_RETRIES,
                        error_msg,
                    )

                    if attempt < MAX_RETRIES:
                        backoff = min(RETRY_BACKOFF_MAX, RETRY_BACKOFF_BASE**attempt)
                        await asyncio.sleep(backoff)
                    return (
                        ScrapeResult(
                            page=page, items=[], success=False, error=error_msg
                        ),
                        attempt < MAX_RETRIES,
                    )

                # Handle client errors (4xx)
                if resp.status != 200:
                    text = await resp.text()
                    error_msg = f"HTTP {resp.status}: {text[:200]}"
                    logger.warning(
                        "Page %d attempt %d/%d: %s",
                        page,
                        attempt,
                        MAX_RETRIES,
                        error_msg,
                    )
                    return (
                        ScrapeResult(
                            page=page, items=[], success=False, error=error_msg
                        ),
                        attempt < MAX_RETRIES,
                    )

                # Success - parse HTML
                html = await resp.text()
                items = self._parse_html(html, page, url)

                logger.info("Page %d: Successfully scraped %d items", page, len(items))

                if use_manager:
                    await self.session_manager.rate_limiter.adjust_on_success()

                return (
                    ScrapeResult(page=page, items=items, success=True, error=None),
                    False,
                )

        except (TimeoutError, aiohttp.ClientError) as e:
            error = f"{type(e).__name__}: {e}"
            logger.warning(
                "Page %d attempt %d/%d: Network error: %s",
                page,
                attempt,
                MAX_RETRIES,
                error,
            )
            if attempt < MAX_RETRIES:
                backoff = min(RETRY_BACKOFF_MAX, RETRY_BACKOFF_BASE**attempt)
                await asyncio.sleep(backoff)
            return (
                ScrapeResult(page=page, items=[], success=False, error=error),
                attempt < MAX_RETRIES,
            )

        finally:
            # Close session only if we created it (not from manager)
            if not use_manager and session and not session.closed:
                await session.close()

    def _parse_html(self, html: str, page: int, base_url: str) -> list[DividendRecord]:
        """Parse HTML to extract DividendRecord objects.

        Uses BeautifulSoup to find .news-item elements and extract
        company names and archive URLs.

        Args:
            html: Raw HTML content
            page: Current page number (for logging)
            base_url: Base URL for resolving relative links

        Returns:
            List of DividendRecord objects
        """
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "lxml")
        news_items = soup.select(SELECTOR)  # .news-item

        records: list[DividendRecord] = []
        for idx, item in enumerate(news_items, 1):
            # Extract text (company name)
            text = item.get_text(strip=True)

            # Extract href from links within the item
            link = item.find("a", href=True)
            if not link:
                logger.warning("Page %d item %d: no link found, skipping", page, idx)
                continue

            href = link["href"]
            archive_url = urljoin(base_url, href)

            if not text:
                logger.warning(
                    "Page %d item %d: empty company name, skipping", page, idx
                )
                continue

            records.append(DividendRecord(company_name=text, archive_url=archive_url))

        return records

    async def scrape_pages_batch(
        self,
        page_urls: list[tuple[int, str]],
        timeout: int = 180,
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

        # Single pass with gather - no retry logic here as _fetch_and_parse handles retries
        tasks = []
        for page, url in page_urls:
            task = asyncio.create_task(self._scrape_page_single(page, url, timeout))
            tasks.append(task)

        results = await asyncio.gather(*tasks)
        return results

    async def _scrape_page_single(
        self,
        page: int,
        url: str,
        timeout: int,
    ) -> ScrapeResult:
        """Internal method for batch scraping a single page."""
        for attempt in range(1, MAX_RETRIES + 1):
            result, should_retry = await self._fetch_and_parse(
                page, url, timeout, attempt
            )
            if not should_retry:
                return result

        logger.error("Batch page %d: All %d attempts failed", page, MAX_RETRIES)
        return ScrapeResult(
            page=page,
            items=[],
            success=False,
            error=f"All {MAX_RETRIES} attempts failed",
        )

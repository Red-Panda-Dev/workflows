"""Cloudflare Browser Rendering API client for CentralDepo workflow."""

import asyncio
import logging
import random
from typing import Optional

import aiohttp

from .config import MAX_RETRIES, RETRY_BACKOFF_BASE, SCRAPE_API, SELECTOR
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


class CloudflareClient:
    """Client for Cloudflare Browser Rendering API.

    Handles authentication, request sending, retry logic, and rate limiting.
    """

    def __init__(self, account_id: str, api_token: str):
        """Initialize client with Cloudflare credentials.

        Args:
            account_id: Cloudflare account ID
            api_token: Cloudflare API token with Browser Rendering permissions
        """
        self.account_id = account_id
        self.api_token = api_token
        self.api_url = SCRAPE_API.format(account_id=account_id)
        self.headers = {
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json",
        }

    async def scrape_page(self, page: int, url: str, timeout: int) -> ScrapeResult:
        """Scrape a single page using Cloudflare Browser Rendering.

        Implements:
        - Retry logic with exponential backoff (MAX_RETRIES attempts)
        - Rate limiting respect (Retry-After header)
        - Error handling for various HTTP status codes
        - Parsing of response into DividendRecord objects

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

        last_error: Optional[str] = None

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        self.api_url,
                        json=payload,
                        headers=self.headers,
                        timeout=aiohttp.ClientTimeout(total=timeout),
                    ) as resp:
                        # Handle rate limiting
                        if resp.status == 429:
                            retry_after = int(resp.headers.get("Retry-After", "5"))
                            logger.warning(
                                "Page %d attempt %d/%d: Rate limited, waiting %ds",
                                page,
                                attempt,
                                MAX_RETRIES,
                                retry_after,
                            )
                            await asyncio.sleep(retry_after)
                            last_error = f"Rate limited, retried after {retry_after}s"
                            continue

                        # Handle server errors
                        if resp.status >= 500:
                            text = await resp.text()
                            error_msg = f"HTTP {resp.status}: {text[:200]}"
                            logger.warning("Page %d attempt %d/%d: %s", page, attempt, MAX_RETRIES, error_msg)
                            last_error = error_msg
                            if attempt < MAX_RETRIES:
                                await asyncio.sleep(RETRY_BACKOFF_BASE**attempt)
                            continue

                        # Handle client errors
                        if resp.status != 200:
                            text = await resp.text()
                            error_msg = f"HTTP {resp.status}: {text[:200]}"
                            logger.warning("Page %d attempt %d/%d: %s", page, attempt, MAX_RETRIES, error_msg)
                            last_error = error_msg
                            if attempt < MAX_RETRIES:
                                await asyncio.sleep(RETRY_BACKOFF_BASE**attempt)
                            continue

                        # Success - parse response
                        data = await resp.json()

                        if not data.get("success"):
                            error_msg = data.get("error", "Unknown error")
                            if isinstance(error_msg, dict):
                                error_msg = str(error_msg)
                            logger.error("Page %d: Cloudflare API returned success=false: %s", page, error_msg)
                            return ScrapeResult(page=page, items=[], success=False, error=f"CF API error: {error_msg}")

                        result_list = data.get("result") or []
                        if not result_list:
                            logger.info("Page %d: Empty result list", page)
                            return ScrapeResult(page=page, items=[], success=True, error=None)

                        # Find the selector block
                        selector_block = None
                        for block in result_list:
                            if block.get("selector") == SELECTOR:
                                selector_block = block
                                break

                        if selector_block is None:
                            logger.warning("Page %d: No .news-item selector block in response", page)
                            return ScrapeResult(page=page, items=[], success=True, error=None)

                        items = selector_block.get("results") or []
                        records = parse_items(items, page)

                        logger.info("Page %d: Successfully scraped %d items", page, len(records))
                        return ScrapeResult(page=page, items=records, success=True, error=None)

            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                last_error = f"{type(e).__name__}: {e}"
                logger.warning("Page %d attempt %d/%d: Network error: %s", page, attempt, MAX_RETRIES, last_error)
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(RETRY_BACKOFF_BASE**attempt)

        # All attempts failed
        logger.error("Page %d: All %d attempts failed. Last error: %s", page, MAX_RETRIES, last_error)
        return ScrapeResult(page=page, items=[], success=False, error=last_error or "Unknown error")

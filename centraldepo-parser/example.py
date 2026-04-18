"""
Fetch all dividend disclosure records from centraldepo.by via Cloudflare Browser Rendering.

Scrapes paginated dividend registry, extracts company name and archive URL
from each .news-item element, and writes deduplicated results to a single JSON file.

Usage:
    python wiki/utils/fetch_centraldepo_dividends.py
    python wiki/utils/fetch_centraldepo_dividends.py --output dividends.json
    python wiki/utils/fetch_centraldepo_dividends.py --max-pages 50 --delay 2

Environment:
    CF_ACCOUNT_ID  - Cloudflare account ID
    CF_API_TOKEN   - API token with Browser Rendering - Edit permission
"""

import argparse
import asyncio
import json
import logging
import os
import re
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import aiohttp
from fake_useragent import UserAgent as FakeUserAgent

logger = logging.getLogger(__name__)

BASE_URL = "https://www.centraldepo.by/uslugi/raskrytie-informatsii/reestr/dividends/"
SCRAPE_API = "https://api.cloudflare.com/client/v4/accounts/{account_id}/browser-rendering/scrape"
SELECTOR = ".news-item"
HREF_RE = re.compile(r'href="([^"]+)"')
_ua = FakeUserAgent()

SCRIPT_DIR = Path(__file__).parent
DEFAULT_OUTPUT = SCRIPT_DIR / "centraldepo_dividends.json"


@dataclass
class DividendRecord:
    """Single dividend disclosure entry.

    Attributes:
        company_name: Company name extracted from .news-item text.
        archive_url: Absolute URL to the dividend archive file.
    """

    company_name: str
    archive_url: str


def build_page_url(page: int) -> str:
    """Build paginated URL for centraldepo dividends registry.

    Page 1 uses the base URL without query params. Page 2+ appends
    ?PAGEN_1=<n>.

    Args:
        page: 1-based page number.

    Returns:
        Full URL string for the requested page.
    """
    if page == 1:
        return BASE_URL
    return f"{BASE_URL}?PAGEN_1={page}"


def normalize_archive_url(href: str) -> str:
    """Resolve a possibly-relative href to an absolute URL.

    Args:
        href: Raw href value from HTML, may be relative or absolute.

    Returns:
        Absolute URL string.
    """
    return urljoin(BASE_URL, href)


def parse_items(results: list[dict[str, Any]], page: int) -> list[DividendRecord]:
    """Extract clean DividendRecords from a page of scraped .news-item elements.

    Args:
        results: List of element dicts from CF scrape response, each with
                 "text" and "html" keys.
        page: Current page number, used for logging skipped items.

    Returns:
        List of valid DividendRecord instances.
    """
    records: list[DividendRecord] = []
    for idx, item in enumerate(results, 1):
        text = (item.get("text") or "").strip()
        html = item.get("html") or ""

        href_match = HREF_RE.search(html)
        if not href_match:
            logger.warning("Page %d item %d: no href found, skipping", page, idx)
            continue

        company_name = text
        archive_url = normalize_archive_url(href_match.group(1))

        if not company_name:
            logger.warning("Page %d item %d: empty company name, skipping", page, idx)
            continue

        records.append(DividendRecord(company_name=company_name, archive_url=archive_url))

    return records


async def scrape_page(
    page: int,
    *,
    session: aiohttp.ClientSession,
    account_id: str,
    api_token: str,
    timeout: int,
) -> list[DividendRecord] | None:
    """Fetch and parse a single paginated page from centraldepo dividends registry.

    Retries up to 3 times on transient errors. Returns None if all retries fail,
    signaling the caller to skip this page. Returns an empty list if the page
    loaded but had no matching elements (signals end of pagination).

    Args:
        page: 1-based page number.
        session: Shared aiohttp session.
        account_id: Cloudflare account ID.
        api_token: Cloudflare API token.
        timeout: Request timeout in seconds.

    Returns:
        List of DividendRecord, empty list if no results, or None on failure.
    """
    url = build_page_url(page)
    api_url = SCRAPE_API.format(account_id=account_id)
    headers = {
        "Authorization": f"Bearer {api_token}",
        "Content-Type": "application/json",
    }
    payload: dict[str, Any] = {
        "url": url,
        "userAgent": _ua.random,
        "waitForSelector": {"selector": SELECTOR, "timeout": 60000},
        "elements": [{"selector": SELECTOR}],
    }

    last_error: str = ""
    for attempt_num in range(1, 4):
        try:
            async with session.post(
                api_url,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                if resp.status == 429:
                    retry_after = int(resp.headers.get("Retry-After", "5"))
                    logger.warning("Page %d rate limited, waiting %ds (attempt %d/3)", page, retry_after, attempt_num)
                    await asyncio.sleep(retry_after)
                    last_error = "rate limited"
                    continue

                if resp.status >= 500 or resp.status == 422:
                    text = await resp.text()
                    last_error = f"HTTP {resp.status}: {text[:200]}"
                    logger.warning("Page %d attempt %d/3 failed: %s", page, attempt_num, last_error)
                    await asyncio.sleep(2**attempt_num)
                    continue

                if resp.status != 200:
                    text = await resp.text()
                    last_error = f"HTTP {resp.status}: {text[:200]}"
                    logger.warning("Page %d attempt %d/3 failed: %s", page, attempt_num, last_error)
                    continue

                data = await resp.json()

                if not data.get("success"):
                    logger.error("Page %d: CF returned success=false", page)
                    return []

                result_list = data.get("result") or []
                if not result_list:
                    return []

                selector_block = None
                for block in result_list:
                    if block.get("selector") == SELECTOR:
                        selector_block = block
                        break

                if selector_block is None:
                    logger.warning("Page %d: no .news-item selector block in response", page)
                    return []

                items = selector_block.get("results") or []
                return parse_items(items, page)

        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            last_error = f"{type(e).__name__}: {e}"
            logger.warning("Page %d attempt %d/3 error: %s", page, attempt_num, last_error)
            if attempt_num < 3:
                await asyncio.sleep(2**attempt_num)

    logger.error("Page %d skipped after 3 failed attempts: %s", page, last_error)
    return None


async def fetch_all_pages(
    *,
    account_id: str,
    api_token: str,
    max_pages: int,
    delay: float,
    timeout: int,
) -> list[DividendRecord]:
    """Iterate through all dividend registry pages until exhausted.

    Args:
        account_id: Cloudflare account ID.
        api_token: Cloudflare API token.
        max_pages: Safety cap on number of pages to fetch.
        delay: Seconds to sleep between page requests.
        timeout: Per-request timeout in seconds.

    Returns:
        Aggregated list of all DividendRecords across pages.

    Raises:
        RuntimeError: If max_pages is reached with no sign of natural end.
    """
    all_records: list[DividendRecord] = []
    skipped_pages: list[int] = []
    consecutive_empty = 0

    async with aiohttp.ClientSession() as session:
        for page in range(1, max_pages + 1):
            start = time.monotonic()
            result = await scrape_page(
                page,
                session=session,
                account_id=account_id,
                api_token=api_token,
                timeout=timeout,
            )
            elapsed = time.monotonic() - start

            if result is None:
                skipped_pages.append(page)
                consecutive_empty += 1
                logger.info(
                    "Page %d: skipped (failed). Total: %d records, %.1fs",
                    page,
                    len(all_records),
                    elapsed,
                )
                if consecutive_empty >= 10:
                    logger.info("3 consecutive failures, stopping pagination")
                    break
                if page < max_pages:
                    await asyncio.sleep(delay)
                continue

            if not result:
                logger.info(
                    "Page %d: empty (end of pagination). Total: %d records, %.1fs",
                    page,
                    len(all_records),
                    elapsed,
                )
                break

            consecutive_empty = 0
            all_records.extend(result)
            logger.info(
                "Page %d: %d items, total %d, %.1fs",
                page,
                len(result),
                len(all_records),
                elapsed,
            )

            if page < max_pages:
                await asyncio.sleep(delay)
        else:
            logger.warning(
                "Reached max-pages limit (%d) with no empty page. Increase --max-pages if needed.",
                max_pages,
            )

    if skipped_pages:
        logger.warning("Skipped %d failed pages: %s", len(skipped_pages), skipped_pages)

    return all_records


def deduplicate(records: list[DividendRecord]) -> list[DividendRecord]:
    """Remove duplicates preserving first occurrence, then sort.

    Args:
        records: Raw records possibly containing duplicates.

    Returns:
        Sorted unique records by (company_name, archive_url).
    """
    seen: set[tuple[str, str]] = set()
    unique: list[DividendRecord] = []
    for r in records:
        key = (r.company_name, r.archive_url)
        if key not in seen:
            seen.add(key)
            unique.append(r)

    duplicates = len(records) - len(unique)
    if duplicates:
        logger.info("Removed %d duplicate(s)", duplicates)

    unique.sort(key=lambda r: (r.company_name, r.archive_url))
    return unique


def write_json(records: list[DividendRecord], path: Path) -> None:
    """Write records to JSON file atomically.

    Writes to a temp file first, then renames to target path.

    Args:
        records: Final deduplicated records.
        path: Target JSON file path.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    data = [asdict(r) for r in records]
    content = json.dumps(data, ensure_ascii=False, indent=2)

    fd, tmp_path = tempfile.mkstemp(
        dir=path.parent,
        prefix=".centraldepo_",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    logger.info("Wrote %d records to %s", len(records), path)


async def main() -> None:
    """CLI entrypoint for centraldepo dividend registry scraper."""
    parser = argparse.ArgumentParser(
        description="Fetch dividend records from centraldepo.by via Cloudflare Browser Rendering.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python wiki/utils/fetch_centraldepo_dividends.py\n"
            "  python wiki/utils/fetch_centraldepo_dividends.py --output dividends.json\n"
            "  python wiki/utils/fetch_centraldepo_dividends.py --max-pages 50 --delay 2\n"
        ),
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Output JSON file path")
    parser.add_argument("--max-pages", type=int, default=500, help="Safety cap on pages to fetch (default: 500)")
    parser.add_argument(
        "--delay", type=float, default=1.0, help="Delay between page requests in seconds (default: 1.0)"
    )
    parser.add_argument("--timeout", type=int, default=180, help="Per-request timeout in seconds (default: 180)")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    account_id = os.getenv("CF_ACCOUNT_ID") or ""
    api_token = os.getenv("CF_API_TOKEN") or ""
    if not account_id or not api_token:
        print("Error: Set CF_ACCOUNT_ID and CF_API_TOKEN env vars.", file=sys.stderr)
        sys.exit(1)

    logger.info("Starting scrape: base=%s, max_pages=%d, delay=%.1fs", BASE_URL, args.max_pages, args.delay)

    records = await fetch_all_pages(
        account_id=account_id,
        api_token=api_token,
        max_pages=args.max_pages,
        delay=args.delay,
        timeout=args.timeout,
    )

    if not records:
        logger.error("Zero records collected. Check site availability or selector.")
        sys.exit(1)

    records = deduplicate(records)
    write_json(records, args.output)


if __name__ == "__main__":
    asyncio.run(main())

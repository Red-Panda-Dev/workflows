"""File download logic for CentralDepo workflow."""

import asyncio
import hashlib
import logging
import os
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import aiohttp

logger = logging.getLogger(__name__)

# Download constants
MAX_CONCURRENT_DOWNLOADS = 10
DOWNLOAD_TIMEOUT = 300  # seconds
DOWNLOAD_RETRIES = 3
CHUNK_SIZE = 8192  # bytes


def get_company_folder_name(company_name: str) -> str:
    """Generate MD5 hash of lowercase company name for folder naming.

    Args:
        company_name: Original company name (may be mixed case)

    Returns:
        32-character MD5 hex string
    """
    return hashlib.md5(company_name.lower().encode("utf-8")).hexdigest()


def get_filename_from_url(url: str) -> str:
    """Extract filename from URL path and convert to lowercase.

    Args:
        url: Full URL to archive file

    Returns:
        Filename string (last non-empty path component, lowercased)

    Raises:
        ValueError: If URL has no path or empty filename
    """
    parsed = urlparse(url)
    filename = Path(parsed.path).name

    if not filename:
        raise ValueError(f"Cannot extract filename from URL: {url}")

    return filename.lower()


async def download_file(
    url: str,
    output_path: Path,
    session: aiohttp.ClientSession,
    semaphore: asyncio.Semaphore,
) -> tuple[str, bool, str | None]:
    """Download a single file with retry logic and atomic write.

    Args:
        url: URL to download
        output_path: Target file path (will be overwritten if exists)
        session: Shared aiohttp session
        semaphore: Concurrency limiter

    Returns:
        Tuple of (url, success, error_message)

    Note:
        Uses streaming to handle large files efficiently.
        Parent directories are created automatically.
        Writes to temp file first, then atomically renames.
    """
    async with semaphore:
        for attempt in range(1, DOWNLOAD_RETRIES + 1):
            try:
                output_path.parent.mkdir(parents=True, exist_ok=True)

                # Use temp file for atomic write
                fd, tmp_path = tempfile.mkstemp(
                    dir=str(output_path.parent),
                    prefix=".download_",
                    suffix=".tmp",
                )

                try:
                    async with session.get(
                        url,
                        timeout=aiohttp.ClientTimeout(total=DOWNLOAD_TIMEOUT),
                    ) as resp:
                        if resp.status != 200:
                            text = await resp.text()
                            error = f"HTTP {resp.status}: {text[:200]}"
                            logger.warning(
                                "Download attempt %d/%d failed for %s: %s",
                                attempt,
                                DOWNLOAD_RETRIES,
                                url,
                                error,
                            )
                            os.close(fd)
                            if os.path.exists(tmp_path):
                                os.unlink(tmp_path)
                            if attempt < DOWNLOAD_RETRIES:
                                await asyncio.sleep(2**attempt)
                            continue

                        # Stream download to avoid loading entire file in memory
                        total_bytes = 0
                        async for chunk in resp.content.iter_chunked(CHUNK_SIZE):
                            total_bytes += len(chunk)
                            os.write(fd, chunk)

                        # Atomic rename
                        os.close(fd)
                        os.replace(tmp_path, str(output_path))

                        logger.info(
                            "Downloaded %s (%d bytes) to %s",
                            url,
                            total_bytes,
                            output_path,
                        )
                        return (url, True, None)

                except (TimeoutError, aiohttp.ClientError) as e:
                    os.close(fd)
                    if os.path.exists(tmp_path):
                        os.unlink(tmp_path)

                    error = f"{type(e).__name__}: {e}"
                    logger.warning(
                        "Download attempt %d/%d failed for %s: %s",
                        attempt,
                        DOWNLOAD_RETRIES,
                        url,
                        error,
                    )
                    if attempt < DOWNLOAD_RETRIES:
                        await asyncio.sleep(2**attempt)
                    continue

            except Exception as e:
                error = f"{type(e).__name__}: {e}"
                logger.error(
                    "Unexpected error downloading %s (attempt %d/%d): %s",
                    url,
                    attempt,
                    DOWNLOAD_RETRIES,
                    error,
                )
                if attempt < DOWNLOAD_RETRIES:
                    await asyncio.sleep(2**attempt)
                continue

        # All attempts failed
        return (url, False, f"Failed after {DOWNLOAD_RETRIES} attempts")


async def download_company_files(
    company_name: str,
    urls: list[str],
    output_root: Path,
    semaphore: asyncio.Semaphore,
    session: aiohttp.ClientSession,
) -> tuple[str, int, int, list[str]]:
    """Download all files for a single company.

    Args:
        company_name: Company name (original case for logging)
        urls: List of URLs to download
        output_root: Root output directory (e.g., Path("output"))
        semaphore: Global concurrency limiter
        session: Shared aiohttp session

    Returns:
        Tuple of (company_name, success_count, failure_count, failed_urls)
    """
    folder_name = get_company_folder_name(company_name)
    company_folder = output_root / folder_name

    tasks = []
    for url in urls:
        try:
            filename = get_filename_from_url(url)
            output_path = company_folder / filename

            # Always download and overwrite (user decision)
            task = asyncio.create_task(
                download_file(url, output_path, session, semaphore)
            )
            tasks.append(task)
        except ValueError as e:
            logger.error("Skipping URL %s: %s", url, e)

    results = await asyncio.gather(*tasks)

    success = sum(1 for _, suc, _ in results if suc)
    failure = sum(1 for _, suc, _ in results if not suc)
    failed_urls = [url for url, suc, _ in results if not suc]

    return (company_name, success, failure, failed_urls)


async def download_all_files(
    results: list[tuple[str, list[str]]],
    output_root: Path,
) -> dict:
    """Download files for all companies in parallel.

    Args:
        results: List of (company_name, urls) tuples
        output_root: Root output directory

    Returns:
        Dictionary with download statistics including:
        - total_companies
        - total_files (attempted)
        - successful
        - failed
        - failed_urls (list of all URLs that failed)
        - by_company (per-company breakdown)
    """
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)

    async with aiohttp.ClientSession() as session:
        tasks = []
        for company_name, urls in results:
            task = asyncio.create_task(
                download_company_files(
                    company_name,
                    urls,
                    output_root,
                    semaphore,
                    session,
                )
            )
            tasks.append(task)

        company_results = await asyncio.gather(*tasks)

    # Aggregate statistics
    stats = {
        "total_companies": len(company_results),
        "total_files": 0,
        "successful": 0,
        "failed": 0,
        "failed_urls": [],
        "by_company": {},
    }

    for company, success, failure, failed_urls in company_results:
        stats["total_files"] += success + failure
        stats["successful"] += success
        stats["failed"] += failure
        stats["failed_urls"].extend(failed_urls)
        stats["by_company"][company] = {
            "success": success,
            "failed": failure,
            "failed_urls": failed_urls,
        }

    return stats

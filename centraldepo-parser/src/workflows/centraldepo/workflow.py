"""Main workflow for CentralDepo Dividend Parser.

This workflow scrapes dividend disclosures from centraldepo.by using
Cloudflare Browser Rendering, with pagination support.
"""

import asyncio
import logging
from pathlib import Path
from typing import List, Optional

import mistralai.workflows as workflows

from .client import CloudflareClient
from .config import BASE_URL
from .models import CompanyResult, DividendRecord, ScrapeResult, WorkflowInput, WorkflowOutput
from .parser import transform_to_output

logger = logging.getLogger(__name__)


@workflows.activity()
async def scrape_single_page(
    page: int,
    url: str,
    account_id: str,
    api_token: str,
    timeout: int,
) -> Optional[List[DividendRecord]]:
    """Activity to scrape a single page and return dividend records.

    Args:
        page: 1-based page number
        url: URL to scrape
        account_id: Cloudflare account ID
        api_token: Cloudflare API token
        timeout: Request timeout in seconds

    Returns:
        List of DividendRecord objects. None if page failed, empty list if no results.
    """
    client = CloudflareClient(account_id, api_token)
    result: ScrapeResult = await client.scrape_page(page, url, timeout)

    if not result.success:
        logger.warning("Page %d failed: %s", page, result.error)
        return None

    return result.items


@workflows.activity()
async def get_credentials() -> tuple[str, str]:
    """Activity to read Cloudflare credentials from environment.

    This is separated into an activity because environment variable access
    is restricted in the Temporal workflow sandbox.

    Returns:
        Tuple of (account_id, api_token)

    Raises:
        ValueError: If credentials are not set
    """
    import os

    account_id = os.environ.get("CF_ACCOUNT_ID", "")
    api_token = os.environ.get("CF_API_TOKEN", "")

    if not account_id or not api_token:
        raise ValueError("CF_ACCOUNT_ID and CF_API_TOKEN environment variables required. Set them in your .env file.")

    return account_id, api_token


@workflows.activity()
async def save_results(output: WorkflowOutput, output_path: str) -> str:
    """Activity to save workflow output to JSON file atomically.

    Args:
        output: WorkflowOutput containing results and stats
        output_path: Path where to save the JSON file

    Returns:
        The absolute path where the file was saved

    Raises:
        RuntimeError: If file write fails
    """
    import json
    import os
    import tempfile

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    # Convert to dict for JSON serialization
    data = {
        "results": [{"company_name": r.company_name, "urls": r.urls} for r in output.results],
        "stats": output.stats,
    }
    if output.download_stats:
        data["download_stats"] = output.download_stats
    if output.extraction_stats:
        data["extraction_stats"] = output.extraction_stats

    # Atomic write using temp file
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), prefix=".centraldepo_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, str(path))
        logger.info("Saved %d companies to %s", len(output.results), path)
        return str(path.resolve())
    except Exception as e:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise RuntimeError(f"Failed to save results: {e}") from e


@workflows.activity()
async def download_all_results_files(
    results: List["CompanyResult"],
    output_path: str,
) -> dict:
    """Activity to download all files for all companies.

    Args:
        results: List of CompanyResult with company_name and urls
        output_path: Path to the JSON output file (used to find output_root)

    Returns:
        Dictionary with download statistics
    """
    from pathlib import Path

    from .downloader import download_all_files

    output_root = Path(output_path).parent

    # Prepare list of (company_name, urls) tuples
    results_list = [(r.company_name, r.urls) for r in results]

    stats = await download_all_files(results_list, output_root)

    logger.info(
        "Download complete: %d companies, %d files (%d successful, %d failed)",
        stats["total_companies"],
        stats["total_files"],
        stats["successful"],
        stats["failed"],
    )

    if stats["failed_urls"]:
        logger.warning(
            "Failed to download %d files: %s",
            len(stats["failed_urls"]),
            stats["failed_urls"][:5],
        )

    return stats


@workflows.activity()
async def extract_all_downloaded_archives(
    results: List["CompanyResult"],
    output_path: str,
) -> dict:
    """Activity to extract all downloaded archives for all companies.

    Args:
        results: List of CompanyResult with company_name and urls
        output_path: Path to the JSON output file (used to find output_root)

    Returns:
        Dictionary with extraction statistics
    """
    from pathlib import Path

    from .extractor import extract_all_archives

    output_root = Path(output_path).parent

    # Prepare list of (company_name, urls) tuples
    results_list = [(r.company_name, r.urls) for r in results]

    stats = await extract_all_archives(results_list, output_root)

    logger.info(
        "Extraction complete: %d companies, %d archives (%d successful, %d failed, %d files extracted)",
        stats["total_companies"],
        stats["total_archives"],
        stats["successful"],
        stats["failed"],
        stats.get("files_extracted", 0),
    )

    if stats.get("failed_archives"):
        logger.warning(
            "Failed to extract %d archives: %s",
            len(stats["failed_archives"]),
            stats["failed_archives"][:5],
        )

    return stats


@workflows.workflow.define(
    name="centraldepo-parser",
    workflow_display_name="CentralDepo Dividend Parser",
    workflow_description="Scrapes dividend disclosures from centraldepo.by, downloads files, and extracts archives.",
)
class CentralDepoWorkflow:
    """Workflow that scrapes dividend registry from centraldepo.by.

    This workflow:
    1. Validates Cloudflare credentials
    2. Iterates through paginated pages (up to max_pages)
    3. Uses Cloudflare Browser Rendering to scrape each page
    4. Extracts company names and archive URLs
    5. Groups URLs by company (normalized to lowercase)
    6. Saves results to JSON file
    7. Downloads all files to company folders (MD5-named)
    8. Extracts archive files (zip, tar, gz, tar.gz, tgz) into company folders
    9. Returns aggregated output with download and extraction statistics
    """

    @workflows.workflow.entrypoint
    async def run(self, input: WorkflowInput) -> WorkflowOutput:
        """Main workflow entry point.

        Scrapes first N pages from centraldepo.by dividend registry,
        groups URLs by company, saves to JSON file, downloads all files,
        and returns results with download statistics.

        Args:
            input: WorkflowInput with max_pages, delay, timeout, and output_path

        Returns:
            WorkflowOutput with results grouped by company, stats, and download_stats

        Raises:
            ValueError: If Cloudflare credentials are missing
        """
        # Get Cloudflare credentials via activity (sandbox restricts os.environ access)
        account_id, api_token = await get_credentials()

        all_records: List[DividendRecord] = []
        consecutive_failures = 0

        logger.info(
            "Starting scrape: base=%s, max_pages=%d, delay=%.1fs, timeout=%ds",
            BASE_URL,
            input.max_pages,
            input.delay,
            input.timeout,
        )

        # Build URLs and scrape pages
        for page in range(1, input.max_pages + 1):
            url = self._build_page_url(page)
            logger.info("Scraping page %d: %s", page, url)

            records = await scrape_single_page(
                page=page,
                url=url,
                account_id=account_id,
                api_token=api_token,
                timeout=input.timeout,
            )

            if records is None:
                # Page failed after all retries - this shouldn't happen
                # as scrape_single_page returns [] on failure, not None
                logger.error("Page %d returned None, treating as failure", page)
                consecutive_failures += 1
                if consecutive_failures >= 3:
                    logger.info("3 consecutive failures, stopping pagination")
                    break
                if page < input.max_pages:
                    await asyncio.sleep(input.delay)
                continue

            if not records:
                # Empty page means end of pagination
                logger.info("Page %d: empty (end of results), stopping", page)
                break

            consecutive_failures = 0
            all_records.extend(records)
            logger.info("Page %d: %d items, total: %d", page, len(records), len(all_records))

            if page < input.max_pages:
                # Delay between pages to avoid rate limiting
                await asyncio.sleep(input.delay)
        else:
            if consecutive_failures > 0:
                logger.warning(
                    "Reached max_pages (%d) with %d consecutive failures", input.max_pages, consecutive_failures
                )

        # Transform to output format (group URLs by company)
        results = transform_to_output(all_records)

        # Create output with stats
        output = WorkflowOutput(
            results=results,
            stats={
                "total_pages_scraped": min(input.max_pages, page),
                "total_records": len(all_records),
                "companies_found": len(results),
                "duplicate_urls_removed": len(all_records) - sum(len(r.urls) for r in results),
            },
            download_stats=None,
            extraction_stats=None,
        )

        # Save to file
        saved_path = await save_results(output, input.output_path)

        # Download all files
        download_stats = await download_all_results_files(results, saved_path)
        output.download_stats = download_stats

        # Extract all archives
        extraction_stats = await extract_all_downloaded_archives(results, saved_path)
        output.extraction_stats = extraction_stats

        logger.info(
            "Workflow complete: %d companies, %d total records, %d files downloaded, "
            "%d archives extracted, saved to %s",
            len(results),
            len(all_records),
            download_stats.get("successful", 0),
            extraction_stats.get("successful", 0),
            input.output_path,
        )

        return output

    def _build_page_url(self, page: int) -> str:
        """Build paginated URL for centraldepo dividends registry.

        Page 1 uses the base URL without query params. Page 2+ appends ?PAGEN_1=<n>.

        Args:
            page: 1-based page number

        Returns:
            Full URL string for the requested page
        """
        if page == 1:
            return BASE_URL
        return f"{BASE_URL}?PAGEN_1={page}"

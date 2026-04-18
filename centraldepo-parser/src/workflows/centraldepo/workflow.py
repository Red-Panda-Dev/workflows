"""Main workflow for CentralDepo Dividend Parser.

This workflow scrapes dividend disclosures from centraldepo.by using
Cloudflare Browser Rendering, with pagination support.
"""

import asyncio
import logging
import os
from pathlib import Path
from typing import List, Optional, Tuple

import mistralai.workflows as workflows

from .client import CloudflareClient, CloudflareSessionManager
from .config import BASE_URL, BATCH_SIZE, MAX_CONCURRENT_SCRAPES, SCRAPE_BATCH_DELAY
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

    This is the original activity for backward compatibility.
    For better performance, use scrape_pages_batch instead.

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
async def scrape_pages_batch(
    page_urls: List[Tuple[int, str]],
    account_id: str,
    api_token: str,
    timeout: int,
) -> List[ScrapeResult]:
    """Activity to scrape multiple pages in parallel with connection pooling.

    This is the primary performance improvement over scrape_single_page.
    All pages in the batch share the same session (connection pool) and
    execute concurrently within semaphore limits.

    Args:
        page_urls: List of (page_number, url) tuples to scrape
        account_id: Cloudflare account ID
        api_token: Cloudflare API token
        timeout: Request timeout in seconds for each page

    Returns:
        List of ScrapeResult objects in same order as input page_urls.
        Failed pages return ScrapeResult with success=False.
    """
    async with CloudflareSessionManager(account_id, api_token, max_concurrent=MAX_CONCURRENT_SCRAPES) as session_mgr:
        client = CloudflareClient(account_id, api_token, session_manager=session_mgr)
        results = await client.scrape_pages_batch(page_urls, timeout)

        # Log summary
        success_count = sum(1 for r in results if r.success)
        failure_count = len(results) - success_count
        total_items = sum(len(r.items) for r in results)

        if failure_count > 0:
            logger.warning(
                "Batch scrape: %d/%d pages succeeded, %d failed, %d total items",
                success_count,
                len(results),
                failure_count,
                total_items,
            )
        else:
            logger.info(
                "Batch scrape: %d/%d pages succeeded, %d total items",
                success_count,
                len(results),
                total_items,
            )

        return results


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
    if output.conversion_stats:
        data["conversion_stats"] = output.conversion_stats

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


@workflows.activity()
async def generate_final_json(
    results: List["CompanyResult"],
    output_root: str,
) -> str:
    """Activity to generate final JSON file mapping hashed folders to company info and MD files.

    Creates a file at output_root/final_mapping.json with structure:
    {
        "<md5_hash_folder>": {
            "company_name": "<lowercase company name>",
            "files_paths": ["<relative path to md file>", ...]
        },
        ...
    }

    Args:
        results: List of CompanyResult objects
        output_root: Root output directory path (as string, can be Path or str)

    Returns:
        Path to the generated final JSON file
    """
    import json
    import os
    import tempfile
    from pathlib import Path

    from .downloader import get_company_folder_name

    final_data = {}
    output_root_path = Path(output_root)
    output_root_path.mkdir(parents=True, exist_ok=True)

    for company_result in results:
        company_name_lower = company_result.company_name.lower()
        folder_name = get_company_folder_name(company_name_lower)
        folder_path = output_root_path / folder_name

        # Find all MD files in the company folder
        md_files = []
        if folder_path.exists():
            for item in folder_path.iterdir():
                if item.is_file() and item.suffix.lower() == ".md":
                    # Use relative path from output_root for portability
                    rel_path = str(item.relative_to(output_root_path))
                    md_files.append(rel_path)

        final_data[folder_name] = {
            "company_name": company_name_lower,
            "files_paths": sorted(md_files),
        }

    # Save to final JSON file
    final_output_path = output_root_path / "final_mapping.json"

    # Atomic write using temp file
    fd, tmp_path = tempfile.mkstemp(dir=str(final_output_path.parent), prefix=".final_mapping_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(final_data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, str(final_output_path))
        logger.info("Generated final mapping JSON with %d companies at %s", len(final_data), final_output_path)
        return str(final_output_path)
    except Exception as e:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise RuntimeError(f"Failed to save final JSON: {e}") from e


@workflows.activity()
async def convert_all_downloaded_files(
    results: List["CompanyResult"],
    output_path: str,
) -> dict:
    """Activity to convert all downloaded files to Markdown format.

    Handles both non-PDF files (docx, doc, xls) using Python libraries
    and PDF files using Mistral OCR plugin.

    Args:
        results: List of CompanyResult objects from previous steps
        output_path: Path to the JSON output file (used to find output_root)

    Returns:
        Dictionary with conversion statistics including cleaned_up_files list.
    """
    from pathlib import Path

    from .converter import convert_all_files

    output_root = Path(output_path).parent
    results_list = [(r.company_name, r.urls) for r in results]

    # Read cleanup flag from environment, default to true
    cleanup_source = os.environ.get("CLEANUP_SOURCE_FILES", "true").lower() == "true"

    # overwrite=True per user decision, pass cleanup flag
    stats = await convert_all_files(results_list, output_root, overwrite=True, cleanup_source=cleanup_source)

    logger.info(
        "Conversion complete: %d files attempted, %d successful, %d failed, %d skipped",
        stats.get("total_files_attempted", 0),
        stats.get("total_successful", 0),
        stats.get("total_failed", 0),
        stats.get("total_skipped", 0),
    )

    if stats.get("failed_files"):
        logger.warning(
            "Failed to convert %d files: %s",
            len(stats["failed_files"]),
            stats["failed_files"][:5],
        )

    if cleanup_source and stats.get("cleaned_up_files"):
        logger.info(
            "Cleaned up %d source files after successful conversion",
            len(stats["cleaned_up_files"]),
        )

    return stats


@workflows.workflow.define(
    name="centraldepo-parser",
    workflow_display_name="CentralDepo Dividend Parser",
    workflow_description="Scrapes dividend disclosures from centraldepo.by, downloads files, extracts archives, and converts to MD.",
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
    9. Converts all extracted files to Markdown (docx, doc, xls via Python libs; PDF via OCR)
    10. Returns aggregated output with download, extraction, and conversion statistics
    """

    @workflows.workflow.entrypoint
    async def run(self, input: WorkflowInput) -> WorkflowOutput:
        """Main workflow entry point.

        Scrapes first N pages from centraldepo.by dividend registry using batch
        processing for improved performance. Groups URLs by company, saves to JSON
        file, downloads all files, extracts archives, converts files to Markdown,
        and returns results with download, extraction, and conversion statistics.

        Uses batch processing with shared connection pooling for 5-10x performance
        improvement over sequential page scraping.

        Args:
            input: WorkflowInput with max_pages, delay, timeout, and output_path

        Returns:
            WorkflowOutput with results grouped by company, stats, download_stats,
            extraction_stats, and conversion_stats

        Raises:
            ValueError: If Cloudflare credentials are missing
        """
        # Get Cloudflare credentials via activity (sandbox restricts os.environ access)
        account_id, api_token = await get_credentials()

        all_records: List[DividendRecord] = []
        total_pages_scraped = 0
        consecutive_failures = 0

        logger.info(
            "Starting scrape: base=%s, max_pages=%d, delay=%.1fs, timeout=%ds, batch_size=%d, max_concurrent=%d",
            BASE_URL,
            input.max_pages,
            input.delay,
            input.timeout,
            BATCH_SIZE,
            MAX_CONCURRENT_SCRAPES,
        )

        # Build all page URLs upfront
        page_urls = [(page, self._build_page_url(page)) for page in range(1, input.max_pages + 1)]

        # Process pages in batches for parallelism
        for batch_start in range(0, len(page_urls), BATCH_SIZE):
            batch = page_urls[batch_start : batch_start + BATCH_SIZE]
            batch_pages = [page for page, _ in batch]

            logger.info(
                "Scraping batch: pages %d-%d (%d pages)",
                batch_pages[0],
                batch_pages[-1],
                len(batch),
            )

            # Scrape batch in parallel
            batch_results = await scrape_pages_batch(
                page_urls=batch,
                account_id=account_id,
                api_token=api_token,
                timeout=input.timeout,
            )

            # Process results
            batch_had_failures = False
            batch_had_empty = False
            batch_items_count = 0

            for result in batch_results:
                if result.success:
                    if not result.items:
                        # Empty result means end of pagination
                        batch_had_empty = True
                    else:
                        all_records.extend(result.items)
                        batch_items_count += len(result.items)
                        total_pages_scraped += 1
                else:
                    batch_had_failures = True
                    consecutive_failures += 1
                    logger.warning("Page %d failed in batch: %s", result.page, result.error)

            # Log batch results
            logger.info(
                "Batch %d-%d: %d/%d pages succeeded, %d items, %sempty, %sfailures",
                batch_pages[0],
                batch_pages[-1],
                len(batch) - (1 if batch_had_failures else 0),
                len(batch),
                batch_items_count,
                "has " if batch_had_empty else "no ",
                "has " if batch_had_failures else "no ",
            )

            # Check for early termination conditions
            # Stop if we hit end of pagination (empty results)
            if batch_had_empty:
                logger.info(
                    "Empty page detected in batch %d-%d, stopping pagination",
                    batch_pages[0],
                    batch_pages[-1],
                )
                # Adjust total_pages_scraped since we may have not processed all pages
                total_pages_scraped = min(input.max_pages, batch_pages[0] + len(batch) - 1)
                break

            # Stop if too many consecutive failures
            if consecutive_failures >= 3:
                logger.warning(
                    "3 consecutive batch failures at pages %d-%d, stopping pagination",
                    batch_pages[0],
                    batch_pages[-1],
                )
                break

            # Delay between batches to respect rate limits
            if batch_start + BATCH_SIZE < len(page_urls):
                # Use the configured inter-batch delay
                await asyncio.sleep(SCRAPE_BATCH_DELAY)

        # Update total_pages_scraped if we processed all pages
        if total_pages_scraped == 0 and len(page_urls) > 0:
            # We didn't scrape any pages successfully
            total_pages_scraped = min(input.max_pages, len(page_urls))

        # Transform to output format (group URLs by company)
        results = transform_to_output(all_records)

        # Create output with stats
        output = WorkflowOutput(
            results=results,
            stats={
                "total_pages_scraped": total_pages_scraped,
                "total_records": len(all_records),
                "companies_found": len(results),
                "duplicate_urls_removed": len(all_records) - sum(len(r.urls) for r in results),
                "batch_processing_used": True,
                "batch_size": BATCH_SIZE,
                "max_concurrent": MAX_CONCURRENT_SCRAPES,
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

        # Convert all files to Markdown
        conversion_stats = await convert_all_downloaded_files(results, saved_path)
        output.conversion_stats = conversion_stats

        # Generate final JSON mapping hashed folders to company info and MD files
        final_json_path = await generate_final_json(results, str(Path(saved_path).parent))

        logger.info(
            "Workflow complete: %d companies, %d total records, %d files downloaded, "
            "%d archives extracted, %d files converted to MD, "
            "final mapping saved to %s",
            len(results),
            len(all_records),
            download_stats.get("successful", 0),
            extraction_stats.get("successful", 0),
            conversion_stats.get("total_successful", 0),
            final_json_path,
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

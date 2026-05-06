"""Workflow activities for CentralDepo workflows."""

import logging
import os
from datetime import datetime
from pathlib import Path

import mistralai.workflows as workflows

from .ai_distiller import run_ai_distillation
from .client import AiohttpClient, AiohttpSessionManager
from .common import atomic_write_json, company_results_to_tuples, get_output_root, workflow_output_to_json
from .config import MAX_CONCURRENT_SCRAPES
from .models import CompanyResult, DividendRecord, ScrapeResult, WorkflowOutput

logger = logging.getLogger(__name__)


@workflows.activity()
async def scrape_single_page(
    page: int,
    url: str,
    timeout: int = 180,
) -> list[DividendRecord] | None:
    """Activity to scrape a single page and return dividend records."""
    async with AiohttpSessionManager() as session_mgr:
        client = AiohttpClient(session_manager=session_mgr)
        result: ScrapeResult = await client.scrape_page(page, url, timeout)

    if not result.success:
        logger.warning("Page %d failed: %s", page, result.error)
        return None

    return result.items


@workflows.activity()
async def scrape_pages_batch(
    page_urls: list[tuple[int, str]],
    timeout: int = 180,
) -> list[ScrapeResult]:
    """Activity to scrape multiple pages in parallel with connection pooling."""
    async with AiohttpSessionManager(max_concurrent=MAX_CONCURRENT_SCRAPES) as session_mgr:
        client = AiohttpClient(session_manager=session_mgr)
        results = await client.scrape_pages_batch(page_urls, timeout)

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
async def save_results(output: WorkflowOutput, output_path: str) -> str:
    """Activity to save workflow output to JSON file atomically."""
    path = Path(output_path)
    saved = atomic_write_json(workflow_output_to_json(output, output_root=path.parent), path, ".centraldepo_")
    logger.info("Saved %d companies to %s", len(output.results), path)
    return saved


@workflows.activity()
async def download_all_results_files(
    results: list[CompanyResult],
    output_path: str,
) -> dict:
    """Activity to download all files for all companies."""
    from .downloader import download_all_files

    output_root = get_output_root(output_path)
    stats = await download_all_files(company_results_to_tuples(results), output_root)

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
    results: list[CompanyResult],
    output_path: str,
) -> dict:
    """Activity to extract all downloaded archives for all companies."""
    from .extractor import extract_all_archives

    output_root = get_output_root(output_path)
    stats = await extract_all_archives(company_results_to_tuples(results), output_root)

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
async def convert_all_downloaded_files(
    results: list[CompanyResult],
    output_path: str,
) -> dict:
    """Activity to convert all downloaded files to Markdown format."""
    from .converter import convert_all_files

    output_root = get_output_root(output_path)
    cleanup_source = os.environ.get("CLEANUP_SOURCE_FILES", "true").lower() == "true"

    stats = await convert_all_files(
        company_results_to_tuples(results),
        output_root,
        overwrite=True,
        cleanup_source=cleanup_source,
    )

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


@workflows.activity()
async def run_ai_data_distillation(
    results: list[CompanyResult],
    output_path: str,
    reference_date: str | None = None,
) -> tuple[dict, dict]:
    """Activity to run AI distillation on all MD files."""
    output_root = get_output_root(output_path)

    if reference_date is None:
        reference_date = datetime.now().strftime("%Y-%m-%d")

    return await run_ai_distillation(company_results_to_tuples(results), output_root, reference_date)


@workflows.activity()
async def save_distillation_results(
    distillation_data: dict,
    output_root: str,
) -> str:
    """Activity to save AI distillation results to JSON file atomically."""
    final_output_path = Path(output_root) / "ai_distilled.json"
    saved = atomic_write_json(distillation_data, final_output_path, ".ai_distilled_")
    logger.info(
        "Saved AI distillation results for %d companies to %s",
        len(distillation_data),
        final_output_path,
    )
    return saved


@workflows.activity()
async def generate_final_json(
    results: list[CompanyResult],
    output_root: str,
) -> str:
    """Activity to generate final JSON mapping hashed folders to MD files."""
    final_data = {}
    output_root_path = Path(output_root)
    output_root_path.mkdir(parents=True, exist_ok=True)

    for company_result in results:
        company_name_lower = company_result.company_name.lower()
        folder_name = company_result.company_hash
        folder_path = output_root_path / folder_name

        md_files = []
        if folder_path.exists():
            for item in folder_path.iterdir():
                if item.is_file() and item.suffix.lower() == ".md":
                    md_files.append(str(item.relative_to(output_root_path)))

        final_data[folder_name] = {
            "company_name": company_name_lower,
            "files_paths": sorted(md_files),
        }

    final_output_path = output_root_path / "final_mapping.json"
    saved = atomic_write_json(final_data, final_output_path, ".final_mapping_")
    logger.info(
        "Generated final mapping JSON with %d companies at %s",
        len(final_data),
        final_output_path,
    )
    return saved

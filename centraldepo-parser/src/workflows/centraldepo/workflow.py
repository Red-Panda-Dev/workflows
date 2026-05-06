"""Workflow definitions for split CentralDepo pipeline."""

import asyncio
import logging
from pathlib import Path

import mistralai.workflows as workflows

from .activities import (
    convert_all_downloaded_files,
    download_all_results_files,
    extract_all_downloaded_archives,
    generate_final_json,
    run_ai_data_distillation,
    save_distillation_results,
    save_results,
    scrape_pages_batch,
)
from .common import build_page_url, load_company_results
from .config import BASE_URL, BATCH_SIZE, MAX_CONCURRENT_SCRAPES, SCRAPE_BATCH_DELAY
from .models import CollectAssetsInput, DistillDividendsInput, DividendRecord, WorkflowOutput
from .parser import transform_to_output

logger = logging.getLogger(__name__)


@workflows.workflow.define(
    name="centraldepo-collect-assets",
    workflow_display_name="CentralDepo Collect Assets",
    workflow_description="Scrapes dividend disclosures, downloads files, and extracts archives.",
)
class CentralDepoCollectAssetsWorkflow:
    """Workflow that parses pages, downloads files, and extracts archives."""

    @workflows.workflow.entrypoint
    async def run(self, input: CollectAssetsInput) -> WorkflowOutput:
        """Run collection workflow."""
        all_records: list[DividendRecord] = []
        total_pages_scraped = 0
        consecutive_failures = 0

        logger.info(
            "Starting collect-assets: base=%s, max_pages=%d, delay=%.1fs, timeout=%ds, batch_size=%d, max_concurrent=%d",
            BASE_URL,
            input.max_pages,
            input.delay,
            input.timeout,
            BATCH_SIZE,
            MAX_CONCURRENT_SCRAPES,
        )

        page_urls = [(page, build_page_url(page)) for page in range(1, input.max_pages + 1)]

        for batch_start in range(0, len(page_urls), BATCH_SIZE):
            batch = page_urls[batch_start : batch_start + BATCH_SIZE]
            batch_pages = [page for page, _ in batch]

            logger.info(
                "Scraping batch: pages %d-%d (%d pages)",
                batch_pages[0],
                batch_pages[-1],
                len(batch),
            )

            batch_results = await scrape_pages_batch(page_urls=batch, timeout=input.timeout)

            batch_had_failures = False
            batch_had_empty = False
            batch_items_count = 0

            for result in batch_results:
                if result.success:
                    if not result.items:
                        batch_had_empty = True
                    else:
                        all_records.extend(result.items)
                        batch_items_count += len(result.items)
                        total_pages_scraped += 1
                else:
                    batch_had_failures = True
                    consecutive_failures += 1
                    logger.warning("Page %d failed in batch: %s", result.page, result.error)

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

            if batch_had_empty:
                logger.info(
                    "Empty page detected in batch %d-%d, stopping pagination",
                    batch_pages[0],
                    batch_pages[-1],
                )
                total_pages_scraped = min(input.max_pages, batch_pages[0] + len(batch) - 1)
                break

            if consecutive_failures >= 3:
                logger.warning(
                    "3 consecutive batch failures at pages %d-%d, stopping pagination",
                    batch_pages[0],
                    batch_pages[-1],
                )
                break

            if batch_start + BATCH_SIZE < len(page_urls):
                await asyncio.sleep(SCRAPE_BATCH_DELAY)

        if total_pages_scraped == 0 and page_urls:
            total_pages_scraped = min(input.max_pages, len(page_urls))

        results = transform_to_output(all_records)
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

        saved_path = await save_results(output, input.output_path)

        if input.stop_after_parse:
            logger.info(
                "stop_after_parse=True: skipping download and extraction. Saved parsed results to %s",
                saved_path,
            )
            return output

        output.download_stats = await download_all_results_files(results, saved_path)
        output.extraction_stats = await extract_all_downloaded_archives(results, saved_path)
        await save_results(output, input.output_path)

        logger.info(
            "Collect-assets complete: %d companies, %d total records, %d files downloaded, %d archives extracted",
            len(results),
            len(all_records),
            output.download_stats.get("successful", 0),
            output.extraction_stats.get("successful", 0),
        )
        return output


@workflows.workflow.define(
    name="centraldepo-distill-dividends",
    workflow_display_name="CentralDepo Distill Dividends",
    workflow_description="Converts extracted files to markdown and distills structured dividends data.",
)
class CentralDepoDistillDividendsWorkflow:
    """Workflow that converts extracted files to markdown and distills dividends."""

    @workflows.workflow.entrypoint
    async def run(self, input: DistillDividendsInput) -> WorkflowOutput:
        """Run distillation workflow."""
        results = load_company_results(input.input_path)

        output = WorkflowOutput(
            results=results,
            stats={
                "source_input_path": input.input_path,
                "companies_loaded": len(results),
            },
        )

        output.conversion_stats = await convert_all_downloaded_files(results, input.input_path)

        reference_date = input.reference_date or workflows.workflow.now().strftime("%Y-%m-%d")
        distillation_data, distillation_stats = await run_ai_data_distillation(
            results,
            input.input_path,
            reference_date,
        )
        output.distillation_stats = distillation_stats

        output_root = str(Path(input.input_path).parent)
        if distillation_data:
            await save_distillation_results(distillation_data, output_root)

        final_json_path = await generate_final_json(results, output_root)

        logger.info(
            "Distill-dividends complete: %d companies loaded, %d files converted, %d files distilled, mapping saved to %s",
            len(results),
            output.conversion_stats.get("total_successful", 0),
            output.distillation_stats.get("successful", 0),
            final_json_path,
        )
        return output

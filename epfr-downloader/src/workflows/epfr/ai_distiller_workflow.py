"""Workflow for AI dividend distillation from EPFR markdown files.

The workflow is split into 3 activities for UI progress tracking:
1. scan_ai_distiller_files - Discover markdown files in the mapping
2. process_ai_distillation - Run AI extraction on discovered files
3. finalize_ai_distillation - Save distilled JSON output
"""

import asyncio
import json
import logging
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import mistralai.workflows as workflows

from .ai_distiller import AIDistiller, normalize_and_fill_dividend
from .config import resolve_ai_distiller_input
from .models import (
    AiDistillerFileResult,
    AiDistillerProcessResult,
    AiDistillerScanResult,
    AiDistillerWorkItem,
    EpfrAiDistillerInput,
    EpfrAiDistillerOutput,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Activity 1: Scan AI Distiller Files
# =============================================================================


@workflows.activity()
async def scan_ai_distiller_files(input: EpfrAiDistillerInput) -> AiDistillerScanResult:
    """Scan mapping file and identify all markdown files for AI distillation.

    This is Step 1/3 of the AI Distiller workflow. It reads the UNP file mapping
    JSON, filters by optional UNP list, and collects all markdown file entries
    that need AI extraction.

    Args:
        input: AI distiller workflow input with output location and filters.

    Returns:
        AiDistillerScanResult containing full mapping, work items, and discovery stats.

    Raises:
        FileNotFoundError: If the mapping file does not exist.

    """
    resolved = resolve_ai_distiller_input(
        output_dir=input.output_dir,
        mapping_filename=input.mapping_filename,
        output_filename=input.output_filename,
        model_name=input.model_name,
        temperature=input.temperature,
        max_retries=input.max_retries,
        file_delay_seconds=input.file_delay_seconds,
    )
    output_root = Path(resolved["output_dir"])
    mapping_path = output_root / resolved["mapping_filename"]

    if not mapping_path.exists():
        raise FileNotFoundError(f"Mapping file not found: {mapping_path}")

    logger.info(f"Scanning mapping for AI distillation: {mapping_path}")

    mapping: dict[str, Any] = json.loads(mapping_path.read_text(encoding="utf-8"))
    selected_unps = set(input.unps) if input.unps else None

    work_items: list[AiDistillerWorkItem] = []
    total_companies = 0
    total_files = 0

    for unp, company_data_any in mapping.items():
        if selected_unps is not None and unp not in selected_unps:
            continue

        company_data = company_data_any if isinstance(company_data_any, dict) else {}
        files = company_data.get("files", [])
        if not isinstance(files, list):
            continue

        total_companies += 1
        company_title = str(company_data.get("title", ""))
        holder_id = int(company_data.get("holder_id", 0))

        for entry_any in files:
            if not isinstance(entry_any, dict):
                continue
            filename = str(entry_any.get("filename", ""))
            if not filename.lower().endswith(".md"):
                continue

            total_files += 1
            work_items.append(
                AiDistillerWorkItem(
                    unp=unp,
                    company_title=company_title,
                    holder_id=holder_id,
                    file_path=str(output_root / unp / filename),
                    filename=filename,
                    original_name=str(entry_any.get("original_name", "")),
                    upload_date=str(entry_any.get("upload_date", "")),
                    file_id=int(entry_any.get("id", 0)),
                    extracted_from=str(entry_any.get("extracted_from")) if entry_any.get("extracted_from") else None,
                    converted_from=str(entry_any.get("converted_from")) if entry_any.get("converted_from") else None,
                )
            )

    logger.info(
        f"Scan complete: {total_companies} companies, {total_files} markdown files, {len(work_items)} work items"
    )

    return AiDistillerScanResult(
        mapping_path=str(mapping_path.resolve()),
        total_companies=total_companies,
        total_files=total_files,
        work_items=work_items,
        output_dir=resolved["output_dir"],
        output_filename=resolved["output_filename"],
        model_name=resolved["model_name"],
        temperature=resolved["temperature"],
        max_retries=resolved["max_retries"],
        file_delay_seconds=resolved["file_delay_seconds"],
    )


# =============================================================================
# Activity 2: Process AI Distillation
# =============================================================================


@workflows.activity()
async def process_ai_distillation(
    scan_result: AiDistillerScanResult,
) -> AiDistillerProcessResult:
    """Perform AI extraction on all identified markdown files.

    This is Step 2/3 of the AI Distiller workflow. It takes the scan result,
    processes all markdown files sequentially with AI extraction, and
    returns the processed data (no file I/O in this activity).

    Args:
        scan_result: Output from scan_ai_distiller_files activity.

    Returns:
        AiDistillerProcessResult with per-file results and stats.

    """
    work_items = scan_result.work_items

    if not work_items:
        logger.info("No markdown files to process")
        return AiDistillerProcessResult(
            results={},
            total_files=0,
            successful=0,
            failed=0,
            failed_files=[],
            total_companies=0,
        )

    logger.info(f"Starting AI distillation processing for {len(work_items)} markdown files")

    reference_date = datetime.now(UTC).date().isoformat()
    distiller = AIDistiller(
        model_name=scan_result.model_name,
        temperature=scan_result.temperature,
        reference_date=reference_date,
    )

    results: dict[str, AiDistillerFileResult] = {}
    total_companies = 0
    successful = 0
    failed = 0
    failed_files: list[str] = []

    # Group work items by UNP for sequential processing
    by_unp: dict[str, list[AiDistillerWorkItem]] = {}
    for item in work_items:
        if item.unp not in by_unp:
            by_unp[item.unp] = []
        by_unp[item.unp].append(item)

    for unp, items in by_unp.items():
        total_companies += 1
        logger.info(f"Processing {len(items)} files for UNP: {unp}")

        for item in items:
            md_path = Path(item.file_path)
            logger.info(f"  Processing file: {item.filename}")

            try:
                if not md_path.exists():
                    raise FileNotFoundError(f"Markdown file not found: {md_path}")

                md_content = md_path.read_text(encoding="utf-8")
                if not md_content.strip():
                    raise ValueError("Markdown file is empty")

                # AI extraction with retry
                raw_extraction = await distiller.extract_with_retry(md_content, scan_result.max_retries, md_path)

                # Normalize dividends
                dividends: list[dict[str, Any]] = []
                autofilled_fields: list[str] = []

                if raw_extraction.dividends:
                    logger.info(
                        f"  AI returned {len(raw_extraction.dividends)} dividend entries, "
                        f"comment={raw_extraction.ai_comment!r}"
                    )
                    for raw_div in raw_extraction.dividends:
                        normalized, filled = normalize_and_fill_dividend(raw_div, item.upload_date)
                        dividends.append(normalized.model_dump(mode="json"))
                        autofilled_fields.extend(filled)

                # Use key that includes both unp and filename to avoid overwriting
                result_key = f"{unp}/{item.filename}"
                results[result_key] = AiDistillerFileResult(
                    unp=unp,
                    filename=item.filename,
                    status="SUCCESS",
                    has_dividends=raw_extraction.has_dividends,
                    ai_comment=raw_extraction.ai_comment,
                    dividends=dividends,
                    autofilled_fields=sorted(set(autofilled_fields)),
                    error=None,
                    file_id=item.file_id,
                )
                successful += 1
                logger.info(f"  File processed successfully: {item.filename}")

            except Exception as exc:
                failed += 1
                failed_files.append(str(md_path))
                result_key = f"{unp}/{item.filename}"
                results[result_key] = AiDistillerFileResult(
                    unp=unp,
                    filename=item.filename,
                    status="FAILED",
                    has_dividends=False,
                    ai_comment="",
                    dividends=[],
                    autofilled_fields=[],
                    error=f"{type(exc).__name__}: {exc}",
                    file_id=item.file_id,
                )
                logger.error(f"  FAILED processing {item.filename}: {type(exc).__name__}: {exc}")

            # Rate limiting: delay between files
            if scan_result.file_delay_seconds > 0:
                await asyncio.sleep(scan_result.file_delay_seconds)

    logger.info(
        f"AI distillation processing complete: {successful} successful, {failed} failed, {total_companies} companies"
    )

    return AiDistillerProcessResult(
        results=results,
        total_files=len(work_items),
        successful=successful,
        failed=failed,
        failed_files=failed_files,
        total_companies=total_companies,
    )


# =============================================================================
# Activity 3: Finalize AI Distillation
# =============================================================================


@workflows.activity()
async def finalize_ai_distillation(
    scan_result: AiDistillerScanResult,
    process_result: AiDistillerProcessResult,
) -> dict[str, Any]:
    """Save distilled JSON output after AI processing.

    This is Step 3/3 of the AI Distiller workflow. It performs the atomic write
    of the ai_distilled_dividends.json file after all AI extractions are complete.

    Args:
        scan_result: Output from scan_ai_distiller_files activity.
        process_result: Output from process_ai_distillation activity.

    Returns:
        Final stats dictionary with output_path, counts, and details.

    """
    output_root = Path(scan_result.output_dir)
    output_path = output_root / scan_result.output_filename

    # Build export data structure matching existing format
    # Group file results by UNP and build company structures
    export_data: dict[str, dict[str, Any]] = {}

    # Build a lookup from work items for metadata
    work_item_by_key: dict[str, AiDistillerWorkItem] = {}
    for item in scan_result.work_items:
        work_item_by_key[f"{item.unp}/{item.filename}"] = item

    # Process results and group by UNP
    for key, file_result in process_result.results.items():
        # Parse the key to get unp and filename
        if "/" not in key:
            unp = key
            filename = ""
        else:
            unp, filename = key.rsplit("/", 1)

        if unp not in export_data:
            # Find a work item for this UNP to get company metadata
            work_item = work_item_by_key.get(key)
            if work_item:
                export_data[unp] = {
                    "company_name": work_item.company_title,
                    "unp": unp,
                    "holder_id": work_item.holder_id,
                    "files": [],
                }
            else:
                export_data[unp] = {
                    "company_name": "",
                    "unp": unp,
                    "holder_id": 0,
                    "files": [],
                }

        file_entry: dict[str, Any] = {
            "id": file_result.file_id,
            "file_path": key,
            "filename": file_result.filename,
            "has_dividends": file_result.has_dividends,
            "ai_comment": file_result.ai_comment,
            "dividends": file_result.dividends,
            "autofilled_fields": file_result.autofilled_fields,
        }
        if file_result.error:
            file_entry["error"] = file_result.error

        # Add original_name, upload_date, etc. from work item if available
        work_item = work_item_by_key.get(key)
        if work_item:
            file_entry["original_name"] = work_item.original_name
            file_entry["upload_date"] = work_item.upload_date
            if work_item.extracted_from:
                file_entry["extracted_from"] = work_item.extracted_from
            if work_item.converted_from:
                file_entry["converted_from"] = work_item.converted_from

        export_data[unp]["files"].append(file_entry)

    # Atomic write
    fd, tmp_path = tempfile.mkstemp(
        dir=str(output_path.parent),
        prefix=".ai_distilled_",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(export_data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, str(output_path))
        logger.info(f"Distilled AI output saved: {output_path}")
    except Exception as exc:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise RuntimeError(f"Failed to save distilled output: {exc}") from exc

    # Build final stats matching existing run_ai_distillation output format
    stats: dict[str, Any] = {
        "output_path": str(output_path.resolve()),
        "total_companies": len(export_data),
        "total_files": process_result.total_files,
        "successful": process_result.successful,
        "failed": process_result.failed,
        "failed_files": process_result.failed_files,
    }

    logger.info(
        f"AI distillation complete: {stats['successful']}/{stats['total_files']} files successful, "
        f"{stats['failed']} failed, {stats['total_companies']} companies"
    )
    if process_result.failed_files:
        logger.warning(f"Failed files: {process_result.failed_files}")

    return stats


# =============================================================================
# Workflow Class (Orchestrator)
# =============================================================================


@workflows.workflow.define(
    name="epfr-ai-distiller",
    workflow_display_name="EPFR AI Distiller",
    workflow_description="Extracts structured dividend payouts from mapped EPFR markdown files and saves JSON output.",
)
class EpfrAiDistillerWorkflow:
    """Run AI extraction for all mapped EPFR markdown documents.

    The workflow is split into 3 activities for granular UI progress tracking:
    1. scan_ai_distiller_files: Discover markdown files in the mapping
    2. process_ai_distillation: Run AI extraction on discovered files
    3. finalize_ai_distillation: Save distilled JSON output

    This allows the Mistral Workflows UI to display progress as:
    - Step 1/3: Scanning mapping for markdown files...
    - Step 2/3: Processing AI distillation...
    - Step 3/3: Finalizing and saving output...
    """

    @workflows.workflow.entrypoint
    async def run(self, input: EpfrAiDistillerInput) -> EpfrAiDistillerOutput:
        """Run the EPFR AI distillation workflow with 3 tracked steps.

        Coordinates the 3 activities to provide granular progress tracking
        in the UI while maintaining the same external API contract.

        Args:
            input: AI distiller workflow input containing mapping location and process flags.

        Returns:
            Structured AI distiller output with totals, failures, and raw stats.

        """
        logger.info(
            f"Workflow epfr-ai-distiller started: output_dir={input.output_dir}, "
            f"mapping_filename={input.mapping_filename}, unps={input.unps}"
        )

        # Step 1/3: Scan mapping for markdown files
        logger.info("Starting Step 1/3: Scanning mapping for markdown files...")
        scan_result = await scan_ai_distiller_files(input)

        if scan_result.total_files == 0:
            logger.info("No markdown files found in mapping")
            return EpfrAiDistillerOutput(
                output_path=str(Path(scan_result.output_dir) / scan_result.output_filename),
                total_companies=0,
                total_files=0,
                successful=0,
                failed=0,
                stats={
                    "output_path": str(Path(scan_result.output_dir) / scan_result.output_filename),
                    "total_companies": 0,
                    "total_files": 0,
                    "successful": 0,
                    "failed": 0,
                    "failed_files": [],
                },
            )

        logger.info(
            f"Step 1/3 complete: Found {scan_result.total_files} markdown files "
            f"across {scan_result.total_companies} companies"
        )

        # Step 2/3: Process AI distillation
        logger.info("Starting Step 2/3: Processing AI distillation...")
        process_result = await process_ai_distillation(scan_result)

        logger.info(f"Step 2/3 complete: {process_result.successful} successful, {process_result.failed} failed")

        # Step 3/3: Finalize and save
        logger.info("Starting Step 3/3: Finalizing and saving distilled output...")
        final_stats = await finalize_ai_distillation(scan_result, process_result)

        logger.info("Step 3/3 complete: Distilled output saved")

        output = EpfrAiDistillerOutput(
            output_path=str(final_stats.get("output_path", "")),
            total_companies=int(final_stats.get("total_companies", 0)),
            total_files=int(final_stats.get("total_files", 0)),
            successful=int(final_stats.get("successful", 0)),
            failed=int(final_stats.get("failed", 0)),
            stats=final_stats,
        )

        logger.info(
            f"AI distillation complete: {output.successful}/{output.total_files} files, "
            f"{output.total_companies} companies, output={output.output_path}"
        )

        return output

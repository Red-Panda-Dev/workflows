"""Workflow for OCR-converting EPFR PDFs to markdown and updating mapping.

The workflow is split into 3 activities for UI progress tracking:
1. scan_pdf_entries - Discover PDF entries in the mapping
2. process_pdf_ocr - Perform OCR on discovered PDFs
3. finalize_ocr_mapping - Save updated mapping
"""

import asyncio
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

import mistralai.workflows as workflows

from .config import load_epfr_config, resolve_pdf_ocr_input
from .models import (
    EpfrPdfOcrInput,
    EpfrPdfOcrOutput,
    PdfOcrFileResult,
    PdfOcrProcessResult,
    PdfOcrScanResult,
    PdfOcrWorkItem,
)
from .pdf_ocr import ocr_pdf_to_markdown

logger = logging.getLogger(__name__)


# =============================================================================
# Activity 1: Scan PDF Entries
# =============================================================================


@workflows.activity()
async def scan_pdf_entries(input: EpfrPdfOcrInput) -> PdfOcrScanResult:
    """Scan mapping file and identify all PDF entries for OCR.

    This is Step 1/3 of the PDF OCR workflow. It reads the UNP file mapping
    JSON, filters by optional UNP list, and collects all PDF file entries
    that need OCR conversion.

    Args:
        input: OCR workflow input with output location and filters.

    Returns:
        PdfOcrScanResult containing full mapping, work items, and discovery stats.

    Raises:
        FileNotFoundError: If the mapping file does not exist.

    """
    resolved = resolve_pdf_ocr_input(
        output_dir=input.output_dir,
        mapping_filename=input.mapping_filename,
        cleanup_source=input.cleanup_source,
    )
    output_root = Path(resolved["output_dir"])
    mapping_path = output_root / resolved["mapping_filename"]

    if not mapping_path.exists():
        raise FileNotFoundError(f"Mapping file not found: {mapping_path}")

    logger.info(f"Scanning mapping for PDF entries: {mapping_path}")

    mapping: dict[str, Any] = json.loads(mapping_path.read_text(encoding="utf-8"))
    selected_unps = set(input.unps) if input.unps else None

    work_items: list[PdfOcrWorkItem] = []
    by_unp: dict[str, dict[str, Any]] = {}
    total_unps_scanned = 0
    total_pdf_entries = 0

    for unp, company_data_any in mapping.items():
        if selected_unps is not None and unp not in selected_unps:
            continue

        company_data = company_data_any if isinstance(company_data_any, dict) else {}
        files = company_data.get("files", [])
        if not isinstance(files, list):
            continue

        total_unps_scanned += 1
        by_unp[unp] = {"pdf_entries": 0}

        for idx, entry_any in enumerate(files):
            if not isinstance(entry_any, dict):
                continue

            safe_entry: dict[str, Any] = {str(k): v for k, v in entry_any.items()}
            filename = str(safe_entry.get("filename", ""))

            if not filename.lower().endswith(".pdf"):
                continue

            by_unp[unp]["pdf_entries"] += 1
            total_pdf_entries += 1

            file_path = str(output_root / unp / filename)
            work_items.append(
                PdfOcrWorkItem(
                    unp=unp,
                    file_index=idx,
                    filename=filename,
                    file_path=file_path,
                    entry=safe_entry,
                )
            )

    logger.info(
        f"Scan complete: {total_unps_scanned} UNPs, {total_pdf_entries} PDF entries, {len(work_items)} work items"
    )

    return PdfOcrScanResult(
        mapping_path=str(mapping_path.resolve()),
        mapping_raw=mapping,
        total_unps_scanned=total_unps_scanned,
        total_pdf_entries=total_pdf_entries,
        work_items=work_items,
        by_unp=by_unp,
        output_dir=resolved["output_dir"],
        mapping_filename=resolved["mapping_filename"],
        cleanup_source=resolved["cleanup_source"],
    )


# =============================================================================
# Activity 2: Process PDF OCR
# =============================================================================


async def _process_work_item(
    unp: str,
    output_root: Path,
    item: PdfOcrWorkItem,
    overwrite: bool,
) -> tuple[int, int, str, dict[str, Any] | None, str | None, str | None]:
    """Process a single PDF work item: OCR the PDF and return result tuple.

    This helper function is used by process_pdf_ocr to process individual
    PDF files within the semaphore-limited concurrency pool.

    Args:
        unp: Company UNP identifier.
        output_root: Root output directory.
        item: Work item containing PDF file details.
        overwrite: Whether to overwrite existing .md files.

    Returns:
        Tuple of (dummy_int, file_index, status, updated_entry, source_path, error)
        for compatibility with asyncio.gather unpacking.

    """
    filename = item.filename
    pdf_path = Path(item.file_path)

    if not pdf_path.exists():
        logger.warning(f"PDF not found, skipping: {pdf_path}")
        return (0, item.file_index, "FAILED", None, str(pdf_path), "PDF_NOT_FOUND")

    md_path = pdf_path.with_suffix(".md")
    if md_path.exists() and not overwrite:
        logger.info(f"Markdown already exists, skipping: {md_path}")
        return (0, item.file_index, "SKIPPED", None, str(pdf_path), "MD_ALREADY_EXISTS")

    try:
        cfg = load_epfr_config()
        raw_bytes = await asyncio.to_thread(pdf_path.read_bytes)
        logger.debug(f"Read PDF bytes: path={pdf_path}, size={len(raw_bytes)}")

        if len(raw_bytes) > cfg.max_pdf_size_bytes:
            logger.warning(f"PDF exceeds max size: {pdf_path} ({len(raw_bytes)} > {cfg.max_pdf_size_bytes})")
            return (
                0,
                item.file_index,
                "FAILED",
                None,
                str(pdf_path),
                f"PDF_TOO_LARGE({len(raw_bytes)} bytes)",
            )

        success, actual_md_path, err = await ocr_pdf_to_markdown(pdf_path, overwrite)

        if not success:
            if err == "MD_ALREADY_EXISTS":
                return (0, item.file_index, "SKIPPED", None, str(pdf_path), err)
            return (0, item.file_index, "FAILED", None, str(pdf_path), err)

        # Build updated entry for the mapping
        updated_entry = dict(item.entry)
        updated_entry["filename"] = actual_md_path.name if actual_md_path else Path(filename).with_suffix(".md").name
        updated_entry["converted_from"] = filename

        return (0, item.file_index, "SUCCESS", updated_entry, str(pdf_path), None)

    except Exception as exc:
        logger.error(f"OCR failed for {pdf_path}: {type(exc).__name__}: {exc}")
        return (0, item.file_index, "FAILED", None, str(pdf_path), f"{type(exc).__name__}: {exc}")


@workflows.activity()
async def process_pdf_ocr(
    output_root: str,
    scan_result: PdfOcrScanResult,
    overwrite: bool,
) -> PdfOcrProcessResult:
    """Perform OCR on all identified PDF entries.

    This is Step 2/3 of the PDF OCR workflow. It takes the scan result,
    processes all PDF files concurrently (with semaphore limit), and
    returns the updated mapping with results.

    Args:
        output_root: Root directory path (string for serialization).
        scan_result: Output from scan_pdf_entries activity.
        overwrite: Whether to overwrite existing .md files.

    Returns:
        PdfOcrProcessResult with updated mapping, per-file results, and stats.

    """
    output_root_path = Path(output_root)
    # Deep copy mapping for mutation
    mapping: dict[str, Any] = dict(scan_result.mapping_raw)
    work_items = scan_result.work_items

    if not work_items:
        logger.info("No PDF entries to process")
        return PdfOcrProcessResult(
            updated_mapping=mapping,
            results=[],
            total_successful=0,
            total_failed=0,
            total_skipped=0,
            failed_files=[],
            skipped_files=[],
            cleaned_up_files=[],
        )

    logger.info(f"Starting OCR processing for {len(work_items)} PDF entries")

    cfg = load_epfr_config()
    semaphore = asyncio.Semaphore(cfg.max_concurrent_ocr)
    logger.info(f"OCR concurrency limit: {cfg.max_concurrent_ocr}")

    results: list[PdfOcrFileResult] = []
    failed_files: list[str] = []
    skipped_files: list[str] = []
    cleaned_up_files: list[str] = []
    total_successful = 0
    total_failed = 0
    total_skipped = 0

    # Group work items by UNP for processing
    by_unp_items: dict[str, list[tuple[int, PdfOcrWorkItem]]] = {}
    for idx, item in enumerate(work_items):
        if item.unp not in by_unp_items:
            by_unp_items[item.unp] = []
        by_unp_items[item.unp].append((idx, item))

    for unp, items in by_unp_items.items():
        company_data = mapping.get(unp, {})
        if not isinstance(company_data, dict):
            company_data = {}
        files = company_data.get("files", [])
        if not isinstance(files, list):
            files = []

        logger.info(f"Processing {len(items)} PDF entries for UNP: {unp}")

        tasks: list[asyncio.Task] = []
        item_indexes: list[tuple[int, int]] = []  # (index_in_items_list, file_index)

        for idx_in_list, (orig_idx, item) in enumerate(items):

            async def _process_with_limit(
                u: str, i: PdfOcrWorkItem
            ) -> tuple[int, int, str, dict[str, Any] | None, str | None, str | None]:
                async with semaphore:
                    return await _process_work_item(u, output_root_path, i, overwrite)

            tasks.append(asyncio.create_task(_process_with_limit(unp, item)))
            item_indexes.append((idx_in_list, item.file_index))

        if not tasks:
            continue

        raw_results = await asyncio.gather(*tasks, return_exceptions=True)

        for (idx_in_list, file_idx), result in zip(item_indexes, raw_results, strict=True):
            if isinstance(result, BaseException):
                total_failed += 1
                orig_filename = items[idx_in_list][1].filename
                source_path = items[idx_in_list][1].file_path
                results.append(
                    PdfOcrFileResult(
                        unp=unp,
                        file_index=file_idx,
                        status="FAILED",
                        original_filename=orig_filename,
                        new_filename=None,
                        source_path=source_path,
                        error=f"{type(result).__name__}: {result}",
                        converted_from=None,
                    )
                )
                if source_path:
                    failed_files.append(source_path)
                logger.error(f"OCR task raised {type(result).__name__} for {source_path}: {result}")
                continue

            _, result_file_idx, status, updated_entry, source_path, err = result

            if status == "SUCCESS":
                files[file_idx] = updated_entry
                total_successful += 1
                results.append(
                    PdfOcrFileResult(
                        unp=unp,
                        file_index=file_idx,
                        status="SUCCESS",
                        original_filename=items[idx_in_list][1].filename,
                        new_filename=updated_entry.get("filename") if updated_entry else None,
                        source_path=source_path,
                        error=None,
                        converted_from=updated_entry.get("converted_from") if updated_entry else None,
                    )
                )

                # Cleanup source PDF immediately after successful OCR
                if source_path:
                    source = Path(source_path)
                    md_exists = source.with_suffix(".md").exists()
                    if md_exists and source.exists():
                        try:
                            source.unlink()
                            cleaned_up_files.append(str(source))
                            logger.info(f"Cleaned up source PDF after OCR: {source}")
                        except Exception as exc:
                            logger.warning(f"Failed to cleanup source PDF {source}: {exc}")

            elif status == "SKIPPED":
                total_skipped += 1
                results.append(
                    PdfOcrFileResult(
                        unp=unp,
                        file_index=file_idx,
                        status="SKIPPED",
                        original_filename=items[idx_in_list][1].filename,
                        new_filename=None,
                        source_path=source_path,
                        error=err,
                        converted_from=None,
                    )
                )
                if source_path:
                    skipped_files.append(source_path)

            elif status == "FAILED":
                total_failed += 1
                results.append(
                    PdfOcrFileResult(
                        unp=unp,
                        file_index=file_idx,
                        status="FAILED",
                        original_filename=items[idx_in_list][1].filename,
                        new_filename=None,
                        source_path=source_path,
                        error=err,
                        converted_from=None,
                    )
                )
                if source_path:
                    failed_files.append(source_path)

    logger.info(
        f"OCR processing complete: {total_successful} successful, {total_failed} failed, {total_skipped} skipped"
    )

    return PdfOcrProcessResult(
        updated_mapping=mapping,
        results=results,
        total_successful=total_successful,
        total_failed=total_failed,
        total_skipped=total_skipped,
        failed_files=failed_files,
        skipped_files=skipped_files,
        cleaned_up_files=cleaned_up_files,
    )


# =============================================================================
# Activity 3: Finalize OCR Mapping
# =============================================================================


@workflows.activity()
async def finalize_ocr_mapping(
    output_root: str,
    mapping_filename: str,
    process_result: PdfOcrProcessResult,
    cleanup_source: bool,
) -> dict[str, Any]:
    """Save updated mapping after OCR processing.

    This is Step 3/3 of the PDF OCR workflow. It performs the atomic write
    of the updated mapping JSON file after all PDF OCR operations are complete.

    Args:
        output_root: Root directory path.
        mapping_filename: Name of the mapping JSON file.
        process_result: Output from process_pdf_ocr activity.
        cleanup_source: Whether source PDFs should be cleaned up (already
            done in process phase, but kept for API compatibility).

    Returns:
        Final stats dictionary matching existing EpfrPdfOcrOutput structure.

    """
    output_root_path = Path(output_root)
    mapping_path = output_root_path / mapping_filename
    mapping = process_result.updated_mapping

    # Cleaned up files list from process phase
    cleaned_up_files = list(process_result.cleaned_up_files)

    # Atomic write: create temp file, write, replace
    fd, tmp_path = tempfile.mkstemp(
        dir=str(mapping_path.parent),
        prefix=".mapping_ocr_",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(mapping, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, str(mapping_path))
        logger.info(f"Updated OCR mapping saved: {mapping_path}")
    except Exception as exc:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise RuntimeError(f"Failed to save mapping: {exc}") from exc

    # Build final stats matching existing output structure
    stats: dict[str, Any] = {
        "mapping_path": str(mapping_path.resolve()),
        "total_unps_scanned": 0,
        "total_pdf_entries": process_result.total_successful
        + process_result.total_failed
        + process_result.total_skipped,
        "total_successful": process_result.total_successful,
        "total_failed": process_result.total_failed,
        "total_skipped": process_result.total_skipped,
        "failed_files": process_result.failed_files,
        "skipped_files": process_result.skipped_files,
        "cleaned_up_files": cleaned_up_files,
        "by_unp": {},
    }

    # Reconstruct by_unp stats from process results
    by_unp: dict[str, dict[str, Any]] = {}
    for result in process_result.results:
        unp = result.unp
        if unp not in by_unp:
            by_unp[unp] = {
                "pdf_entries": 0,
                "successful": 0,
                "failed": 0,
                "skipped": 0,
                "converted_files": [],
            }

        by_unp[unp]["pdf_entries"] += 1

        if result.status == "SUCCESS":
            by_unp[unp]["successful"] += 1
            if result.original_filename and result.new_filename:
                by_unp[unp]["converted_files"].append(
                    {"source": result.original_filename, "markdown": result.new_filename}
                )
        elif result.status == "FAILED":
            by_unp[unp]["failed"] += 1
        elif result.status == "SKIPPED":
            by_unp[unp]["skipped"] += 1

    stats["total_unps_scanned"] = len(by_unp)
    stats["by_unp"] = by_unp

    logger.info(
        f"Mapping OCR pass complete: unps={stats['total_unps_scanned']}, "
        f"pdf_entries={stats['total_pdf_entries']}, "
        f"successful={stats['total_successful']}, "
        f"failed={stats['total_failed']}, "
        f"skipped={stats['total_skipped']}"
    )

    return stats


# =============================================================================
# Workflow Class (Orchestrator)
# =============================================================================


@workflows.workflow.define(
    name="epfr-pdf-ocr-converter",
    workflow_display_name="EPFR PDF OCR Converter",
    workflow_description="Converts downloaded EPFR PDF files to markdown using Mistral OCR and updates mapping.",
)
class EpfrPdfOcrConverter:
    """Convert mapped EPFR PDF disclosures to Markdown via Mistral OCR.

    The workflow is split into 3 activities for granular UI progress tracking:
    1. scan_pdf_entries: Discover PDF entries in the mapping
    2. process_pdf_ocr: Perform OCR on discovered PDFs
    3. finalize_ocr_mapping: Save the updated mapping

    This allows the Mistral Workflows UI to display progress as:
    - Step 1/3: Scanning mapping for PDF entries...
    - Step 2/3: Processing PDF OCR...
    - Step 3/3: Finalizing and saving mapping...
    """

    @workflows.workflow.entrypoint
    async def run(self, input: EpfrPdfOcrInput) -> EpfrPdfOcrOutput:
        """Run the EPFR PDF OCR conversion workflow with 3 tracked steps.

        Coordinates the 3 activities to provide granular progress tracking
        in the UI while maintaining the same external API contract.

        Args:
            input: OCR workflow input containing mapping location and process flags.

        Returns:
            Structured OCR output with totals, failed files, cleanup list, and raw stats.

        """
        logger.info(
            f"Workflow epfr-pdf-ocr-converter started: output_dir={input.output_dir}, "
            f"mapping_filename={input.mapping_filename}, overwrite={input.overwrite}, "
            f"cleanup_source={input.cleanup_source}, unps={input.unps}"
        )

        # Step 1/3: Scan mapping for PDF entries
        logger.info("Starting Step 1/3: Scanning mapping for PDF entries...")
        scan_result = await scan_pdf_entries(input)

        if scan_result.total_pdf_entries == 0:
            logger.info("No PDF entries found in mapping")
            return EpfrPdfOcrOutput(
                mapping_path=str(Path(scan_result.output_dir) / scan_result.mapping_filename),
                total_pdf_entries=0,
                total_successful=0,
                total_failed=0,
                total_skipped=0,
                cleaned_up_files=[],
                failed_files=[],
                stats={
                    "mapping_path": str(Path(scan_result.output_dir) / scan_result.mapping_filename),
                    "total_unps_scanned": scan_result.total_unps_scanned,
                    "total_pdf_entries": 0,
                    "total_successful": 0,
                    "total_failed": 0,
                    "total_skipped": 0,
                    "failed_files": [],
                    "skipped_files": [],
                    "cleaned_up_files": [],
                    "by_unp": scan_result.by_unp,
                },
            )

        logger.info(
            f"Step 1/3 complete: Found {scan_result.total_pdf_entries} PDF entries "
            f"across {scan_result.total_unps_scanned} UNPs"
        )

        # Step 2/3: Process PDF OCR
        logger.info("Starting Step 2/3: Processing PDF OCR...")
        process_result = await process_pdf_ocr(
            output_root=scan_result.output_dir,
            scan_result=scan_result,
            overwrite=input.overwrite,
        )

        logger.info(
            f"Step 2/3 complete: {process_result.total_successful} successful, "
            f"{process_result.total_failed} failed, "
            f"{process_result.total_skipped} skipped"
        )

        # Step 3/3: Finalize and save
        logger.info("Starting Step 3/3: Finalizing and saving mapping...")
        final_stats = await finalize_ocr_mapping(
            output_root=scan_result.output_dir,
            mapping_filename=scan_result.mapping_filename,
            process_result=process_result,
            cleanup_source=scan_result.cleanup_source,
        )

        logger.info("Step 3/3 complete: Mapping saved")

        output = EpfrPdfOcrOutput(
            mapping_path=str(final_stats.get("mapping_path", "")),
            total_pdf_entries=int(final_stats.get("total_pdf_entries", 0)),
            total_successful=int(final_stats.get("total_successful", 0)),
            total_failed=int(final_stats.get("total_failed", 0)),
            total_skipped=int(final_stats.get("total_skipped", 0)),
            cleaned_up_files=list(final_stats.get("cleaned_up_files", [])),
            failed_files=list(final_stats.get("failed_files", [])),
            stats=final_stats,
        )

        logger.info(
            f"PDF OCR complete: {output.total_pdf_entries} entries, "
            f"{output.total_successful} successful, "
            f"{output.total_failed} failed, "
            f"{output.total_skipped} skipped"
        )

        return output

"""Main workflow for EPFR files downloader.

Iterates paginated API pages from epfr.gov.by, downloads file content
for each record, extracts archives, converts documents to Markdown,
saves to output/<UNP>/, and produces a JSON mapping.
"""

import asyncio
import json
import logging
import os
import tempfile
from pathlib import Path

import aiohttp
import mistralai.workflows as workflows

from .client import _get_unp, download_all_files, fetch_page
from .config import load_epfr_config, resolve_workflow_input
from .converter import convert_all_files
from .extractor import extract_all_archives
from .models import (
    EpfrFileRecord,
    EpfrRecord,
    EpfrWorkflowInput,
    EpfrWorkflowOutput,
)

logger = logging.getLogger(__name__)


@workflows.activity()
async def fetch_all_pages(input: EpfrWorkflowInput) -> list[EpfrRecord]:
    """Fetch EPFR dividend disclosure records across paginated API pages.

    Starts at EPFR page zero and stops early when the upstream API marks a page
    as the last page, limiting work to the requested maximum page count.

    Args:
        input: Workflow input with max_pages, date_from, timeout.

    Returns:
        Flattened list of all EpfrRecord objects across fetched pages.

    """
    resolved = resolve_workflow_input(**input.model_dump(exclude_none=True))
    max_pages = resolved["max_pages"]
    date_from = resolved["date_from"]
    timeout = resolved["timeout"]

    cfg = load_epfr_config()

    all_records: list[EpfrRecord] = []

    async with aiohttp.ClientSession() as session:
        for page_no in range(cfg.first_page_no, cfg.first_page_no + max_pages):
            logger.info("Fetching page %d (date_from=%s)", page_no, date_from)

            response = await fetch_page(session, page_no, date_from, timeout)

            records = response.content
            all_records.extend(records)

            logger.info(
                "Page %d: %d records, total=%d, totalPages=%d, last=%s",
                page_no,
                len(records),
                len(all_records),
                response.total_pages,
                response.last,
            )

            if response.last:
                logger.info("Last page reached at pageNo=%d", page_no)
                break

            await asyncio.sleep(cfg.page_delay)

    logger.info("Fetched %d total records across pages", len(all_records))
    return all_records


@workflows.activity()
async def download_all_epfr_files(records: list[EpfrRecord], output_dir: str) -> dict:
    """Download disclosure files into UNP-specific output folders.

    Persists the raw EPFR file content for each fetched disclosure so later
    activities can extract archives, convert office documents, and build the
    final company-file mapping.

    Args:
        records: List of EpfrRecord objects from the API.
        output_dir: Root directory for downloaded files.

    Returns:
        Download statistics dictionary.

    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    stats = await download_all_files(records, output_path)

    logger.info(
        "Download complete: %d files (%d successful, %d failed)",
        stats["total_files_attempted"],
        stats["successful"],
        stats["failed"],
    )

    if stats["failed_ids"]:
        logger.warning(
            "Failed to download %d files: %s",
            len(stats["failed_ids"]),
            stats["failed_ids"][:10],
        )

    return stats


@workflows.activity()
async def extract_all_epfr_archives(output_dir: str, download_stats: dict) -> dict:
    """Extract downloaded archives before document conversion.

    Processes each UNP folder found in download statistics and preserves
    archive lineage for the final mapping output.

    Args:
        output_dir: Root output directory.
        download_stats: Download stats from download_all_epfr_files.

    Returns:
        Extraction statistics dictionary.

    """
    output_path = Path(output_dir)

    # Get list of UNP folders from download stats
    by_unp = download_stats.get("by_unp", {})
    unp_folders = list(by_unp.keys())

    if not unp_folders:
        logger.info("No UNP folders to process for extraction")
        return {
            "total_unps": 0,
            "total_archives": 0,
            "successful": 0,
            "failed": 0,
            "failed_archives": [],
            "files_extracted": 0,
            "by_unp": {},
        }

    stats = await extract_all_archives(unp_folders, output_path)

    logger.info(
        "Extraction complete: %d archives (%d successful, %d failed), %d files extracted",
        stats["total_archives"],
        stats["successful"],
        stats["failed"],
        stats["files_extracted"],
    )

    return stats


@workflows.activity()
async def convert_all_epfr_files(output_dir: str, download_stats: dict) -> dict:
    """Convert downloaded office documents to Markdown for mapping output.

    Runs after archive extraction and deletes source office files after a
    successful conversion so each business document has one final mapping entry.

    Args:
        output_dir: Root output directory.
        download_stats: Download stats from download_all_epfr_files.

    Returns:
        Conversion statistics dictionary.

    """
    output_path = Path(output_dir)

    # Get list of UNP folders from download stats
    by_unp = download_stats.get("by_unp", {})
    unp_folders = list(by_unp.keys())

    if not unp_folders:
        logger.info("No UNP folders to process for conversion")
        return {
            "total_unps": 0,
            "total_files_attempted": 0,
            "total_successful": 0,
            "total_failed": 0,
            "failed_files": [],
            "cleaned_up_files": [],
            "by_unp": {},
        }

    stats = await convert_all_files(unp_folders, output_path, overwrite=True, cleanup_source=True)

    logger.info(
        "Conversion complete: %d files (%d successful, %d failed), %d source files cleaned up",
        stats["total_files_attempted"],
        stats["total_successful"],
        stats["total_failed"],
        len(stats["cleaned_up_files"]),
    )

    return stats


@workflows.activity()
async def save_unp_mapping(
    records: list[EpfrRecord],
    output_dir: str,
    download_stats: dict,
    extraction_stats: dict,
    conversion_stats: dict,
) -> str:
    """Generate the UNP-to-company-files mapping artifact.

    Applies the pipeline lineage rules that make the JSON artifact the final
    business output: extracted archives are represented by their extracted
    files, converted office documents are represented by Markdown files, and
    failed transformations keep the original source file.

    Creates output/unp_file_mapping.json with structure:
    {
        "<UNP>": {
            "title": "...",
            "holder_id": ...,
            "files": [
                {"id": ..., "filename": "...", "original_name": "...", "upload_date": "...",
                 "extracted_from": null, "converted_from": null}
            ]
        }
    }

    Args:
        records: List of EpfrRecord objects.
        output_dir: Root output directory.
        download_stats: Download stats from download_all_epfr_files.
        extraction_stats: Extraction stats from extract_all_epfr_archives.
        conversion_stats: Conversion stats from convert_all_epfr_files.

    Returns:
        Absolute path to the saved mapping file.

    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    # Build lookup tables from stats
    file_map_raw: dict = download_stats.get("file_map", {})
    # JSON serialization converts int keys to strings — normalize to int
    file_map: dict[int, str] = {int(k): v for k, v in file_map_raw.items()}

    # Build archive_to_files mapping per UNP: {unp: {archive_filename: [extracted_files]}}
    archive_to_files_by_unp: dict[str, dict[str, list[str]]] = {}
    for unp, unp_data in extraction_stats.get("by_unp", {}).items():
        archive_to_files = unp_data.get("archive_to_files", {})
        if archive_to_files:
            archive_to_files_by_unp[unp] = archive_to_files

    # Build set of successfully cleaned up source files (deleted after conversion)
    cleaned_up_files: set[str] = set(conversion_stats.get("cleaned_up_files", []))

    grouped_files: dict[str, list[dict[str, object]]] = {}
    grouped_titles: dict[str, str] = {}
    grouped_holder_ids: dict[str, int] = {}

    skipped_no_holder = 0

    for rec in records:
        unp = _get_unp(rec)
        if rec.holder is None:
            skipped_no_holder += 1
            if skipped_no_holder <= 3:
                logger.warning(
                    "Record %d: holder=None",
                    rec.id,
                )
            continue

        if unp not in grouped_files:
            grouped_titles[unp] = rec.holder.title
            grouped_holder_ids[unp] = rec.holder.id
            grouped_files[unp] = []

        if rec.id not in file_map:
            continue

        original_filename = file_map[rec.id]
        upload_date = rec.real_upload_date.split(" ")[0] if rec.real_upload_date else ""
        unp_folder = output_path / unp
        original_filepath = unp_folder / original_filename

        # Check if this file was an archive that was extracted
        archive_to_files = archive_to_files_by_unp.get(unp, {})
        if original_filename in archive_to_files:
            # Archive was extracted - add extracted files with lineage
            extracted_files = archive_to_files[original_filename]
            for extracted_name in extracted_files:
                extracted_path = unp_folder / extracted_name

                # Check if extracted file was then converted
                source_file_str = str(extracted_path)
                if source_file_str in cleaned_up_files:
                    # File was converted and deleted - find the .md
                    md_name = Path(extracted_name).with_suffix(".md").name
                    md_path = unp_folder / md_name
                    if md_path.exists():
                        entry = EpfrFileRecord(
                            id=rec.id,
                            filename=md_name,
                            original_name=rec.name,
                            upload_date=upload_date,
                            extracted_from=original_filename,
                            converted_from=extracted_name,
                        )
                        grouped_files[unp].append(entry.model_dump())
                elif extracted_path.exists():
                    # Extracted file still exists (not converted or conversion failed)
                    entry = EpfrFileRecord(
                        id=rec.id,
                        filename=extracted_name,
                        original_name=rec.name,
                        upload_date=upload_date,
                        extracted_from=original_filename,
                    )
                    grouped_files[unp].append(entry.model_dump())
            continue

        # File was not an archive - check if it still exists
        if not original_filepath.exists():
            # File doesn't exist and wasn't an archive - skip (download failed?)
            continue

        # File still exists - check if it was converted
        source_file_str = str(original_filepath)
        if source_file_str in cleaned_up_files:
            # File was converted and deleted - find the .md
            md_name = Path(original_filename).with_suffix(".md").name
            md_path = unp_folder / md_name
            if md_path.exists():
                entry = EpfrFileRecord(
                    id=rec.id,
                    filename=md_name,
                    original_name=rec.name,
                    upload_date=upload_date,
                    converted_from=original_filename,
                )
                grouped_files[unp].append(entry.model_dump())
            continue

        # File exists and wasn't converted - keep as is
        entry = EpfrFileRecord(
            id=rec.id,
            filename=original_filename,
            original_name=rec.name,
            upload_date=upload_date,
        )
        grouped_files[unp].append(entry.model_dump())

    if skipped_no_holder:
        logger.warning(
            "Skipped %d/%d records in mapping: missing holder.id",
            skipped_no_holder,
            len(records),
        )

    grouped: dict[str, dict[str, object]] = {
        unp: {
            "title": grouped_titles[unp],
            "holder_id": grouped_holder_ids[unp],
            "files": files,
        }
        for unp, files in grouped_files.items()
        if files
    }

    cfg = load_epfr_config()

    mapping_path = output_path / cfg.mapping_filename

    fd, tmp_path = tempfile.mkstemp(
        dir=str(mapping_path.parent),
        prefix=".mapping_",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(grouped, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, str(mapping_path))
        logger.info("Saved UNP mapping (%d companies) to %s", len(grouped), mapping_path)
        return str(mapping_path.resolve())
    except Exception as exc:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise RuntimeError(f"Failed to save mapping: {exc}") from exc


@workflows.workflow.define(
    name="epfr-files-downloader",
    workflow_display_name="EPFR Files Downloader",
    workflow_description="Downloads dividend disclosure files from epfr.gov.by API, "
    "extracts archives, converts documents to Markdown, "
    "saves them organized by company UNP, and produces a JSON mapping.",
)
class EpfrFilesDownloader:
    """Download and normalize EPFR dividend disclosure files by company UNP.

    The workflow fetches paginated disclosure metadata, downloads raw file
    content, extracts archives, converts supported office documents to
    Markdown, and writes the JSON mapping consumed by downstream processing.
    """

    @workflows.workflow.entrypoint
    async def run(self, input: EpfrWorkflowInput) -> EpfrWorkflowOutput:
        """Run the complete EPFR download and normalization pipeline.

        Coordinates all side-effectful activities while keeping environment and
        filesystem access inside workflow activities, as required by the
        Mistral workflow runtime.

        Args:
            input: EpfrWorkflowInput with max_pages, date_from, timeout, output_dir.

        Returns:
            EpfrWorkflowOutput with totals, mapping path, and stats.

        """
        resolved = resolve_workflow_input(**input.model_dump(exclude_none=True))
        output_dir = resolved["output_dir"]

        logger.info(
            "Starting EPFR download: max_pages=%s, date_from=%s, output_dir=%s",
            input.max_pages,
            input.date_from,
            output_dir,
        )

        records = await fetch_all_pages(input)

        if not records:
            logger.warning("No records found")
            return EpfrWorkflowOutput(
                total_records=0,
                total_files_downloaded=0,
                total_companies=0,
                mapping_path="",
                stats={"error": "No records found"},
            )

        download_stats = await download_all_epfr_files(records, output_dir)

        extraction_stats = await extract_all_epfr_archives(output_dir, download_stats)

        conversion_stats = await convert_all_epfr_files(output_dir, download_stats)

        mapping_path = await save_unp_mapping(records, output_dir, download_stats, extraction_stats, conversion_stats)

        unps = set()
        for rec in records:
            unps.add(_get_unp(rec))

        output = EpfrWorkflowOutput(
            total_records=len(records),
            total_files_downloaded=download_stats["successful"],
            total_companies=len(unps),
            mapping_path=mapping_path,
            stats={
                "pages_requested": resolved["max_pages"],
                "date_from": resolved["date_from"],
                "download_successful": download_stats["successful"],
                "download_failed": download_stats["failed"],
                "download_failed_ids": download_stats["failed_ids"],
                "archives_extracted": extraction_stats["successful"],
                "archives_failed": extraction_stats["failed"],
                "files_extracted": extraction_stats["files_extracted"],
                "conversions_successful": conversion_stats["total_successful"],
                "conversions_failed": conversion_stats["total_failed"],
                "source_files_cleaned": len(conversion_stats["cleaned_up_files"]),
            },
        )

        logger.info(
            "Workflow complete: %d records, %d files downloaded, %d companies, "
            "%d archives extracted, %d files converted, mapping at %s",
            output.total_records,
            output.total_files_downloaded,
            output.total_companies,
            extraction_stats["successful"],
            conversion_stats["total_successful"],
            output.mapping_path,
        )

        return output

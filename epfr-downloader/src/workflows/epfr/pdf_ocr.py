"""PDF OCR conversion and mapping update logic for EPFR workflow."""

import asyncio
import base64
import json
import logging
import os
from pathlib import Path
import tempfile
from typing import Any

from mistralai.client.models import DocumentURLChunk
from mistralai.workflows.plugins.mistralai import OCRRequest, mistralai_ocr as _mistralai_ocr

from .config import get_ocr_mime_type, load_epfr_config
from .markdown_cleanup import clean_markdown_text


logger = logging.getLogger(__name__)


async def mistralai_ocr(request: Any) -> Any:
    """Submit an OCR request through the Mistral Workflows plugin.

    Accepts the lightweight dictionary payload used by the EPFR OCR business
    flow and converts it to the plugin model required by the runtime.

    Args:
        request: Plugin request model or dictionary with OCR model, document
            URL, and document name fields.

    Returns:
        OCR response from the Mistral plugin.

    """
    if isinstance(request, dict):
        request = OCRRequest(
            model=request["model"],
            document=DocumentURLChunk(
                document_url=request["document_url"],
                document_name=request["document_name"],
            ),
        )

    return await _mistralai_ocr(request)


async def ocr_file_to_markdown(
    file_path: Path,
    overwrite: bool = True,
) -> tuple[bool, Path | None, str | None]:
    """Convert one EPFR file (PDF, PNG, JPG, JPEG) to Markdown with OCR.

    Reads a downloaded file, submits it to Mistral OCR as a data URI, and writes
    a sibling ``.md`` file used by the company mapping.

    Args:
        file_path: Path to the downloaded file (PDF, PNG, JPG, or JPEG).
        overwrite: Whether an existing Markdown file may be replaced.

    Returns:
        Tuple of success flag, Markdown path when available, and an error code
        or message when conversion is skipped or fails.

    """
    ext = file_path.suffix.lower()
    md_path = file_path.with_suffix(".md")
    logger.info(f"Starting OCR for file: {file_path}")

    # Validate extension
    cfg = load_epfr_config()
    if ext not in cfg.ocr_supported_extensions:
        logger.warning(f"Unsupported extension for OCR, skipping: {file_path} (ext={ext})")
        return (False, None, f"UNSUPPORTED_EXTENSION({ext})")

    if not file_path.exists():
        logger.warning(f"File not found, skipping OCR: {file_path}")
        return (False, None, "FILE_NOT_FOUND")

    if md_path.exists() and not overwrite:
        logger.info(f"Markdown already exists and overwrite is disabled: {md_path}")
        return (False, md_path, "MD_ALREADY_EXISTS")

    try:
        raw_bytes = await asyncio.to_thread(file_path.read_bytes)
        logger.debug(f"Read file bytes: path={file_path}, size={len(raw_bytes)}")
        if len(raw_bytes) > cfg.max_pdf_size_bytes:
            logger.warning(
                f"File exceeds max size and will not be OCRed: {file_path} ({len(raw_bytes)} > {cfg.max_pdf_size_bytes})"
            )
            return (False, None, f"FILE_TOO_LARGE({len(raw_bytes)} bytes)")

        # Use appropriate MIME type based on file extension
        mime_type = get_ocr_mime_type(ext)
        b64 = base64.b64encode(raw_bytes).decode("ascii")
        data_uri = f"data:{mime_type};base64,{b64}"

        request = {
            "model": cfg.ocr_model,
            "document_url": data_uri,
            "document_name": file_path.name,
        }
        logger.debug(f"Sending OCR request: model={cfg.ocr_model}, document={file_path.name}")
        result = await mistralai_ocr(request)
        ocr_text = clean_markdown_text("\n\n".join(page.markdown for page in result.pages))
        logger.debug(f"OCR response received: pages={len(result.pages)}, chars={len(ocr_text)}")

        await asyncio.to_thread(md_path.write_text, ocr_text, "utf-8")
        logger.info(f"OCR conversion successful: {file_path} -> {md_path}")
        return (True, md_path, None)
    except Exception as exc:
        logger.error(f"OCR conversion failed for {file_path}: {type(exc).__name__}: {exc}")
        return (False, None, f"{type(exc).__name__}: {exc}")


async def _process_ocr_entry(
    unp: str,
    output_root: Path,
    entry: dict[str, Any],
    overwrite: bool,
) -> tuple[str, dict[str, Any], str, str | None]:
    """Process a single mapping file entry when it references an OCR-able file.

    Args:
        unp: Company tax identifier used to locate the file folder.
        output_root: Root directory containing UNP folders.
        entry: Mapping file entry to inspect and update.
        overwrite: Whether OCR may replace an existing Markdown file.

    Returns:
        Tuple of status, updated mapping entry, source file path, and optional
        error details.

    """
    filename = str(entry.get("filename", ""))
    cfg = load_epfr_config()

    # Check if file is OCR-able
    if not any(filename.lower().endswith(ext) for ext in cfg.ocr_supported_extensions):
        return ("SKIP_NON_OCR", entry, "", None)

    file_path = output_root / unp / filename
    success, md_path, err = await ocr_file_to_markdown(file_path, overwrite)

    if not success:
        if err == "MD_ALREADY_EXISTS":
            return ("SKIPPED", entry, str(file_path), err)
        return ("FAILED", entry, str(file_path), err)

    updated = dict(entry)
    updated["filename"] = md_path.name if md_path else Path(filename).with_suffix(".md").name
    updated["converted_from"] = filename
    return ("SUCCESS", updated, str(file_path), None)


async def ocr_mapping_files(
    output_root: Path,
    mapping_filename: str,
    overwrite: bool = True,
    cleanup_source: bool = True,
    unps: list[str] | None = None,
) -> dict:
    """OCR all OCR-able entries (PDF, PNG, JPG, JPEG) referenced by the UNP mapping file.

    Updates ``unp_file_mapping.json`` in place so successfully OCRed files
    become Markdown entries while preserving existing extraction lineage.

    Args:
        output_root: Root folder containing the mapping file and UNP folders.
        mapping_filename: Mapping JSON filename inside ``output_root``.
        overwrite: Whether existing Markdown files may be replaced.
        cleanup_source: Whether source files should be deleted after successful
            Markdown creation.
        unps: Optional subset of company UNPs to process.

    Returns:
        OCR statistics including totals, failures, skipped files, cleanup list,
        and per-UNP details.

    Raises:
        FileNotFoundError: If the mapping JSON file does not exist.

    """
    mapping_path = output_root / mapping_filename
    if not mapping_path.exists():
        raise FileNotFoundError(f"Mapping file not found: {mapping_path}")

    logger.info(
        f"Starting mapping OCR pass: mapping={mapping_path}, output_root={output_root}, "
        f"overwrite={overwrite}, cleanup_source={cleanup_source}, unp_filter={unps}"
    )

    mapping: dict[str, Any] = json.loads(mapping_path.read_text(encoding="utf-8"))

    selected_unps = set(unps) if unps else None

    stats: dict[str, Any] = {
        "mapping_path": str(mapping_path.resolve()),
        "total_unps_scanned": 0,
        "total_ocr_entries": 0,
        "total_successful": 0,
        "total_failed": 0,
        "total_skipped": 0,
        "failed_files": [],
        "skipped_files": [],
        "cleaned_up_files": [],
        "by_unp": {},
    }

    cfg = load_epfr_config()
    semaphore = asyncio.Semaphore(cfg.max_concurrent_ocr)
    logger.info(f"OCR concurrency limit: {cfg.max_concurrent_ocr}")

    for unp, company_data_any in mapping.items():
        company_data = company_data_any if isinstance(company_data_any, dict) else {}
        if selected_unps is not None and unp not in selected_unps:
            continue

        logger.info(f"Scanning UNP for OCR: {unp}")

        stats["total_unps_scanned"] = int(stats["total_unps_scanned"]) + 1
        files = company_data.get("files", [])
        if not isinstance(files, list):
            continue

        stats["by_unp"][unp] = {
            "ocr_entries": 0,
            "successful": 0,
            "failed": 0,
            "skipped": 0,
            "converted_files": [],
        }

        tasks: list[asyncio.Task] = []
        entry_indexes: list[int] = []
        for idx, entry_any in enumerate(files):
            if not isinstance(entry_any, dict):
                continue

            safe_entry: dict[str, Any] = {str(k): v for k, v in entry_any.items()}
            filename = str(safe_entry.get("filename", ""))

            # Check if file is OCR-able
            if not any(filename.lower().endswith(ext) for ext in cfg.ocr_supported_extensions):
                continue

            stats["total_ocr_entries"] = int(stats["total_ocr_entries"]) + 1
            stats["by_unp"][unp]["ocr_entries"] += 1

            async def _run_with_limit(u: str, e: dict[str, Any]) -> tuple[str, dict[str, Any], str, str | None]:
                async with semaphore:
                    return await _process_ocr_entry(u, output_root, e, overwrite)

            tasks.append(asyncio.create_task(_run_with_limit(unp, safe_entry)))
            entry_indexes.append(idx)

        if not tasks:
            logger.info(f"No OCR-able entries found for UNP: {unp}")
            continue

        logger.info(f"Submitting {len(tasks)} OCR task(s) for UNP: {unp}")
        results = await asyncio.gather(*tasks)

        for idx, result in zip(entry_indexes, results, strict=True):
            status, updated_entry, source_path, err = result

            if status == "SUCCESS":
                old_filename = str(files[idx].get("filename", ""))
                files[idx] = updated_entry
                logger.info(f"OCR succeeded for UNP {unp}: {old_filename} -> {updated_entry.get('filename', '')}")
                stats["total_successful"] = int(stats["total_successful"]) + 1
                stats["by_unp"][unp]["successful"] += 1
                stats["by_unp"][unp]["converted_files"].append(
                    {
                        "source": old_filename,
                        "markdown": str(updated_entry.get("filename", "")),
                    }
                )

                if cleanup_source and source_path:
                    source = Path(source_path)
                    md_exists = (source.with_suffix(".md")).exists()
                    if md_exists and source.exists():
                        try:
                            source.unlink()
                            stats["cleaned_up_files"].append(str(source))
                            logger.info(f"Cleaned up source file after OCR: {source}")
                        except Exception as exc:
                            logger.warning(f"Failed to cleanup source file {source}: {exc}")
            elif status == "SKIPPED":
                logger.info(f"OCR skipped for UNP {unp}: {source_path} ({err})")
                stats["total_skipped"] = int(stats["total_skipped"]) + 1
                stats["by_unp"][unp]["skipped"] += 1
                if source_path:
                    stats["skipped_files"].append(source_path)
            elif status == "FAILED":
                logger.error(f"OCR failed for UNP {unp}: {source_path} ({err})")
                stats["total_failed"] = int(stats["total_failed"]) + 1
                stats["by_unp"][unp]["failed"] += 1
                if source_path:
                    stats["failed_files"].append(source_path)

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
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise

    logger.info(
        f"Mapping OCR pass complete: unps={stats['total_unps_scanned']}, ocr_entries={stats['total_ocr_entries']}, "
        f"successful={stats['total_successful']}, failed={stats['total_failed']}, skipped={stats['total_skipped']}"
    )

    return stats


# Backward compatibility aliases
ocr_pdf_to_markdown = ocr_file_to_markdown
ocr_mapping_pdfs = ocr_mapping_files

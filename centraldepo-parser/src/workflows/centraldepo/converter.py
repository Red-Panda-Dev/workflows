"""Document conversion logic for CentralDepo workflow.

Converts company files (docx, doc, xls) to Markdown format.
PDF files are converted using Mistral OCR via the workflows plugin.
"""

import asyncio
import logging
from pathlib import Path
from typing import List, Tuple

import docx2txt
import xlrd
from docx import Document
from mistralai.workflows.plugins.mistralai import OCRRequest, mistralai_ocr

from .config import MAX_CONCURRENT_CONVERSIONS
from .downloader import get_company_folder_name

logger = logging.getLogger(__name__)


def _table_to_md(table) -> str:
    """Convert a python-docx table to markdown format.

    Args:
        table: A python-docx table object

    Returns:
        Markdown table string
    """
    md_lines = []
    for i, row in enumerate(table.rows):
        cells = [cell.text.strip() for cell in row.cells]
        if i == 0:
            # Header row
            md_lines.append("|" + "|".join(cells) + "|")
            md_lines.append("|" + "|".join("-" for _ in cells) + "|")
        else:
            # Data row
            md_lines.append("|" + "|".join(cells) + "|")
    return "\n".join(md_lines)


def _extract_docx(file_path: Path) -> str:
    """Extract text and tables from .docx file.

    Args:
        file_path: Path to the .docx file

    Returns:
        Markdown content string
    """
    doc = Document(file_path)
    md_content = []

    for para in doc.paragraphs:
        text = para.text.strip()
        if text:
            md_content.append(text)

    for table in doc.tables:
        md_table = _table_to_md(table)
        if md_table:
            md_content.append("")
            md_content.append(md_table)
            md_content.append("")

    return "\n\n".join(md_content)


def _extract_doc(file_path: Path) -> str:
    """Extract text from .doc file using docx2txt.

    Args:
        file_path: Path to the .doc file

    Returns:
        Extracted text as string
    """
    return docx2txt.process(file_path)


def _extract_xls(file_path: Path) -> str:
    """Extract data from .xls file to markdown table.

    Args:
        file_path: Path to the .xls file

    Returns:
        Markdown table string
    """
    workbook = xlrd.open_workbook(file_path)
    sheet = workbook.sheet_by_index(0)

    md_lines = []

    # Header row
    headers = [str(cell.value).strip() for cell in sheet.row(0)]
    if headers:
        md_lines.append("|" + "|".join(headers) + "|")
        md_lines.append("|" + "|".join("-" for _ in headers) + "|")

    # Data rows
    for row_idx in range(1, sheet.nrows):
        row_data = [str(cell.value).strip() if cell.value else "" for cell in sheet.row(row_idx)]
        md_lines.append("|" + "|".join(row_data) + "|")

    return "\n".join(md_lines)


def convert_to_markdown(file_path: Path, overwrite: bool = True) -> Tuple[bool, str | None, str | None]:
    """Convert a single file to markdown based on its extension.

    Args:
        file_path: Path to the file to convert
        overwrite: If True, overwrite existing .md file (default: True)

    Returns:
        Tuple of (success, markdown_content_or_none, error_msg_or_none)
        - success: True if conversion succeeded and file was written
        - markdown_content: The extracted content (if successful)
        - error_msg: Error message (if failed) or special codes:
          - "IS_PDF": File is PDF, skip (handled separately)
          - "MD_ALREADY_EXISTS": MD file exists and overwrite=False
    """
    ext = file_path.suffix.lower()

    try:
        if ext == ".docx":
            content = _extract_docx(file_path)
        elif ext == ".doc":
            content = _extract_doc(file_path)
        elif ext == ".xls":
            content = _extract_xls(file_path)
        elif ext == ".pdf":
            # PDF handled separately via OCR
            return (False, None, "IS_PDF")
        else:
            return (False, None, f"Unsupported extension: {ext}")

        # Write MD file
        md_path = file_path.with_suffix(".md")

        if md_path.exists() and not overwrite:
            return (False, None, "MD_ALREADY_EXISTS")

        md_path.write_text(content, encoding="utf-8")
        logger.debug("Converted %s to %s", file_path, md_path)
        return (True, content, None)

    except Exception as e:
        error_msg = f"{type(e).__name__}: {e}"
        logger.error("Failed to convert %s: %s", file_path, error_msg)
        return (False, None, error_msg)


async def process_pdf_files(
    folder_path: Path,
    overwrite: bool = True,
) -> Tuple[int, int, List[str], int]:
    """Process all PDF files in a folder with Mistral OCR.

    Args:
        folder_path: Path to folder containing PDF files
        overwrite: Whether to overwrite existing .md files (default: True)

    Returns:
        Tuple of (success_count, failure_count, failed_files, skipped_count)
    """
    pdf_files = [f for f in folder_path.iterdir() if f.is_file() and f.suffix.lower() == ".pdf"]

    if not pdf_files:
        return (0, 0, [], 0)

    results = []
    for pdf_path in pdf_files:
        try:
            md_path = pdf_path.with_suffix(".md")

            # Skip if MD exists and not overwriting
            if md_path.exists() and not overwrite:
                results.append((False, "MD_ALREADY_EXISTS", str(pdf_path)))
                continue

            with open(pdf_path, "rb") as f:
                content = f.read()

            request = OCRRequest(
                model="mistral-ocr-latest",
                document=content,
            )
            result = await mistralai_ocr(request)

            # Write MD file
            md_path.write_text(result.text, encoding="utf-8")
            logger.info("OCR converted %s to %s", pdf_path, md_path)
            results.append((True, None, str(pdf_path)))

        except Exception as e:
            error_msg = f"{type(e).__name__}: {e}"
            logger.error("OCR failed for %s: %s", pdf_path, error_msg)
            results.append((False, error_msg, str(pdf_path)))

    success = sum(1 for suc, _, _ in results if suc)
    failure = sum(1 for suc, err, _ in results if not suc and err != "MD_ALREADY_EXISTS")
    skipped = sum(1 for _, err, _ in results if err == "MD_ALREADY_EXISTS")
    failed_files = [path for suc, err, path in results if not suc and err != "MD_ALREADY_EXISTS"]

    return (success, failure, failed_files, skipped)


async def convert_company_files(
    company_name: str,
    folder_path: Path,
    semaphore: asyncio.Semaphore,
    overwrite: bool = True,
) -> Tuple[str, int, int, List[str]]:
    """Convert all eligible non-PDF files in a company folder.

    Args:
        company_name: Company name for logging
        folder_path: Path to company folder
        semaphore: Concurrency limiter
        overwrite: Whether to overwrite existing .md files (default: True)

    Returns:
        Tuple of (company_name, success_count, failure_count, failed_files)
    """
    if not folder_path.exists():
        logger.warning("Company folder does not exist: %s", folder_path)
        return (company_name, 0, 0, [])

    # Find non-PDF files only
    files = [f for f in folder_path.iterdir() if f.is_file() and f.suffix.lower() != ".pdf"]

    if not files:
        logger.debug("No non-PDF files found for %s", company_name)
        return (company_name, 0, 0, [])

    logger.info("Found %d non-PDF files to convert for %s", len(files), company_name)

    tasks = []
    for file_path in files:
        async with semaphore:
            task = asyncio.create_task(asyncio.to_thread(convert_to_markdown, file_path, overwrite))
            tasks.append(task)

    results = await asyncio.gather(*tasks)

    # Filter out PDF markers
    success = sum(1 for suc, _, err in results if suc and err != "IS_PDF")
    failure = sum(1 for suc, _, err in results if not suc and err != "IS_PDF")
    failed_files = [str(files[i]) for i, (suc, _, err) in enumerate(results) if not suc and err != "IS_PDF"]

    logger.info(
        "Converted %d/%d non-PDF files for %s (%d failed)",
        success,
        len(files),
        company_name,
        failure,
    )

    return (company_name, success, failure, failed_files)


async def convert_all_files(
    results: List[Tuple[str, List[str]]],
    output_root: Path,
    overwrite: bool = True,
) -> dict:
    """Convert all files for all companies to markdown.

    Handles both non-PDF files (using Python libraries) and PDF files
    (using Mistral OCR separately).

    Args:
        results: List of (company_name, urls) tuples
        output_root: Root output directory
        overwrite: Whether to overwrite existing .md files (default: True)

    Returns:
        Dictionary with conversion statistics:
        - total_companies
        - total_files_attempted
        - total_successful
        - total_failed
        - total_skipped
        - failed_files (list)
        - by_company (per-company breakdown)
    """
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_CONVERSIONS)

    # Process non-PDF files for all companies
    non_pdf_tasks = []
    pdf_folders = []

    for company_name, _ in results:
        folder_name = get_company_folder_name(company_name)
        folder_path = output_root / folder_name

        # Non-PDF conversion
        task = asyncio.create_task(convert_company_files(company_name, folder_path, semaphore, overwrite))
        non_pdf_tasks.append(task)
        pdf_folders.append((company_name, folder_path))

    # Run non-PDF conversions
    non_pdf_results = await asyncio.gather(*non_pdf_tasks)

    # Process PDF files for all companies (using Mistral OCR)
    pdf_tasks = []
    for _, folder_path in pdf_folders:
        task = asyncio.create_task(process_pdf_files(folder_path, overwrite))
        pdf_tasks.append(task)

    pdf_results = await asyncio.gather(*pdf_tasks)

    # Aggregate stats
    stats = {
        "total_companies": len(results),
        "total_files_attempted": 0,
        "total_successful": 0,
        "total_failed": 0,
        "total_skipped": 0,
        "failed_files": [],
        "by_company": {},
    }

    # Aggregate non-PDF results
    for company, success, failure, failed_files in non_pdf_results:
        stats["by_company"][company] = {
            "non_pdf_success": success,
            "non_pdf_failed": failure,
            "non_pdf_failed_files": failed_files,
        }
        stats["total_files_attempted"] += success + failure
        stats["total_successful"] += success
        stats["total_failed"] += failure
        stats["failed_files"].extend(failed_files)

    # Aggregate PDF results
    for idx, (company_name, _) in enumerate(pdf_folders):
        success, failure, failed_files, skipped = pdf_results[idx]
        if company_name not in stats["by_company"]:
            stats["by_company"][company_name] = {}
        stats["by_company"][company_name]["pdf_success"] = success
        stats["by_company"][company_name]["pdf_failed"] = failure
        stats["by_company"][company_name]["pdf_failed_files"] = failed_files
        stats["total_files_attempted"] += success + failure + skipped
        stats["total_successful"] += success
        stats["total_failed"] += failure
        stats["total_skipped"] += skipped
        stats["failed_files"].extend(failed_files)

    logger.info(
        "Conversion complete: %d attempted, %d successful, %d failed, %d skipped",
        stats["total_files_attempted"],
        stats["total_successful"],
        stats["total_failed"],
        stats["total_skipped"],
    )

    return stats

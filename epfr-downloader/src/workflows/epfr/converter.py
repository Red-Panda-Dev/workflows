"""Document conversion logic for EPFR workflow.

Converts company files (docx, doc, xls, xlsx) to Markdown format.
No PDF support - this workflow does not use OCR.
"""

import asyncio
import logging
import shutil
import subprocess
from pathlib import Path

import docx2txt
import openpyxl
import xlrd
from docx import Document

logger = logging.getLogger(__name__)

MAX_CONCURRENT_CONVERSIONS = 5


def _table_to_md(table) -> str:
    """Convert a python-docx table to markdown format."""
    md_lines = []
    for i, row in enumerate(table.rows):
        cells = [cell.text.strip() for cell in row.cells]
        if i == 0:
            md_lines.append("|" + "|".join(cells) + "|")
            md_lines.append("|" + "|".join("-" for _ in cells) + "|")
        else:
            md_lines.append("|" + "|".join(cells) + "|")
    return "\n".join(md_lines)


def _extract_docx(file_path: Path) -> str:
    """Extract text and tables from .docx file."""
    doc = Document(str(file_path))
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
    """Extract text from .doc file using multiple methods.

    Tries multiple approaches in order:
    1. python-docx (for .docx files mislabeled as .doc)
    2. docx2txt (for .docx files)
    3. antiword/catdoc subprocess extraction (for binary .doc - requires system deps)
    4. Raw binary reading with common encodings
    """
    # Try 1: python-docx (for .docx files mislabeled as .doc)
    try:
        doc = Document(str(file_path))
        return "\n".join([p.text for p in doc.paragraphs])
    except Exception:
        pass

    # Try 2: docx2txt
    try:
        return docx2txt.process(file_path)
    except Exception:
        pass

    # Try 3: antiword/catdoc subprocess extraction for binary .doc
    for tool_name in ("antiword", "catdoc"):
        tool_path = shutil.which(tool_name)
        if not tool_path:
            continue

        try:
            result = subprocess.run(
                [tool_path, str(file_path)],
                capture_output=True,
                check=False,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except Exception:
            continue

        if result.returncode == 0 and result.stdout.strip():
            logger.debug("Extracted %s with %s", file_path, tool_name)
            return result.stdout

    # Try 4: Raw binary reading with common encodings
    try:
        raw = file_path.read_bytes()
        for encoding in ["utf-8", "cp1251", "cp1252", "iso-8859-1", "utf-16"]:
            try:
                return raw.decode(encoding, errors="replace")
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", errors="replace")
    except Exception as e:
        raise ValueError(f"Failed to extract text from {file_path}: {e}") from e


def _extract_xls(file_path: Path) -> str:
    """Extract data from .xls file to markdown table."""
    workbook = xlrd.open_workbook(file_path)
    sheet = workbook.sheet_by_index(0)

    md_lines = []

    # Header row
    if sheet.nrows > 0:
        headers = [str(cell.value).strip() for cell in sheet.row(0)]
        if headers:
            md_lines.append("|" + "|".join(headers) + "|")
            md_lines.append("|" + "|".join("-" for _ in headers) + "|")

    # Data rows
    for row_idx in range(1, sheet.nrows):
        row_data = [str(cell.value).strip() if cell.value else "" for cell in sheet.row(row_idx)]
        md_lines.append("|" + "|".join(row_data) + "|")

    return "\n".join(md_lines)


def _extract_xlsx(file_path: Path) -> str:
    """Extract data from .xlsx file to markdown table."""
    workbook = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
    sheet = workbook.active

    md_lines = []
    rows = list(sheet.iter_rows(values_only=True))

    if not rows:
        return ""

    # Header row
    headers = [str(cell).strip() if cell is not None else "" for cell in rows[0]]
    if headers:
        md_lines.append("|" + "|".join(headers) + "|")
        md_lines.append("|" + "|".join("-" for _ in headers) + "|")

    # Data rows
    for row in rows[1:]:
        row_data = [str(cell).strip() if cell is not None else "" for cell in row]
        md_lines.append("|" + "|".join(row_data) + "|")

    workbook.close()
    return "\n".join(md_lines)


def convert_to_markdown(file_path: Path, overwrite: bool = True) -> tuple[bool, str | None, str | None, Path | None]:
    """Convert a single file to markdown based on its extension.

    Args:
        file_path: Path to the file to convert
        overwrite: If True, overwrite existing .md file (default: True)

    Returns:
        Tuple of (success, markdown_content_or_none, error_msg_or_none, md_path_or_none)
    """
    ext = file_path.suffix.lower()

    try:
        if ext == ".docx":
            content = _extract_docx(file_path)
        elif ext == ".doc":
            content = _extract_doc(file_path)
        elif ext == ".xls":
            content = _extract_xls(file_path)
        elif ext == ".xlsx":
            content = _extract_xlsx(file_path)
        else:
            return (False, None, f"Unsupported extension: {ext}", None)

        # Write MD file
        md_path = file_path.with_suffix(".md")

        if md_path.exists() and not overwrite:
            return (False, None, "MD_ALREADY_EXISTS", None)

        md_path.write_text(content, encoding="utf-8")
        logger.debug("Converted %s to %s", file_path, md_path)
        return (True, content, None, md_path)

    except Exception as e:
        error_msg = f"{type(e).__name__}: {e}"
        logger.error("Failed to convert %s: %s", file_path, error_msg)
        return (False, None, error_msg, None)


async def convert_unp_files(
    unp: str,
    folder_path: Path,
    semaphore: asyncio.Semaphore,
    overwrite: bool = True,
) -> tuple[str, int, int, list[str], list[tuple[Path, Path]]]:
    """Convert all eligible files in a UNP folder.

    Args:
        unp: UNP identifier for logging
        folder_path: Path to UNP folder
        semaphore: Concurrency limiter
        overwrite: Whether to overwrite existing .md files (default: True)

    Returns:
        Tuple of (unp, success_count, failure_count, failed_files, converted_pairs)
        where converted_pairs is a list of (source_path, md_path) tuples
    """
    if not folder_path.exists():
        logger.warning("UNP folder does not exist: %s", folder_path)
        return (unp, 0, 0, [], [])

    # Find convertible files (docx, doc, xls, xlsx - not md or other types)
    convertible_extensions = {".docx", ".doc", ".xls", ".xlsx"}
    files = [f for f in folder_path.iterdir() if f.is_file() and f.suffix.lower() in convertible_extensions]

    if not files:
        logger.debug("No convertible files found for %s", unp)
        return (unp, 0, 0, [], [])

    logger.info("Found %d convertible files for %s", len(files), unp)

    tasks = []
    for file_path in files:
        async with semaphore:
            task = asyncio.create_task(asyncio.to_thread(convert_to_markdown, file_path, overwrite))
            tasks.append(task)

    results = await asyncio.gather(*tasks)

    converted_pairs: list[tuple[Path, Path]] = []
    success = 0
    failure = 0
    failed_files = []

    for i, file_path in enumerate(files):
        suc, _, err, md_path = results[i]
        if suc:
            success += 1
            if md_path:
                converted_pairs.append((file_path, md_path))
        else:
            failure += 1
            failed_files.append(str(file_path))

    logger.info("Converted %d/%d files for %s (%d failed)", success, len(files), unp, failure)

    return (unp, success, failure, failed_files, converted_pairs)


async def convert_all_files(
    unp_folders: list[str],
    output_root: Path,
    overwrite: bool = True,
    cleanup_source: bool = True,
) -> dict:
    """Convert all files for all UNPs to markdown.

    Args:
        unp_folders: List of UNP folder names to process
        output_root: Root output directory
        overwrite: Whether to overwrite existing .md files (default: True)
        cleanup_source: If True, remove source files after successful conversion (default: True)

    Returns:
        Dictionary with conversion statistics:
        - total_unps: number of UNP folders processed
        - total_files_attempted: total files attempted
        - total_successful: successful conversions
        - total_failed: failed conversions
        - failed_files: list of failed file paths
        - cleaned_up_files: list of source files removed after conversion
        - by_unp: per-UNP breakdown with converted_pairs
    """
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_CONVERSIONS)

    tasks = []
    for unp in unp_folders:
        folder_path = output_root / unp
        task = asyncio.create_task(convert_unp_files(unp, folder_path, semaphore, overwrite))
        tasks.append(task)

    unp_results = await asyncio.gather(*tasks)

    stats: dict = {
        "total_unps": len(unp_results),
        "total_files_attempted": 0,
        "total_successful": 0,
        "total_failed": 0,
        "failed_files": [],
        "cleaned_up_files": [],
        "by_unp": {},
    }

    all_converted_pairs: list[tuple[Path, Path]] = []

    for unp, success, failure, failed_files, converted_pairs in unp_results:
        stats["by_unp"][unp] = {
            "success": success,
            "failed": failure,
            "failed_files": failed_files,
            "converted_pairs": [(str(src), str(md)) for src, md in converted_pairs],
        }
        stats["total_files_attempted"] += success + failure
        stats["total_successful"] += success
        stats["total_failed"] += failure
        stats["failed_files"].extend(failed_files)
        all_converted_pairs.extend(converted_pairs)

    # Cleanup source files after successful conversion
    if cleanup_source and all_converted_pairs:
        for source_path, md_path in all_converted_pairs:
            if md_path.exists():
                try:
                    source_path.unlink()
                    stats["cleaned_up_files"].append(str(source_path))
                    logger.info("Removed source file after conversion: %s", source_path)
                except Exception as e:
                    logger.warning("Failed to remove source file %s: %s", source_path, e)

    logger.info(
        "Conversion complete: %d attempted, %d successful, %d failed, %d source files cleaned up",
        stats["total_files_attempted"],
        stats["total_successful"],
        stats["total_failed"],
        len(stats["cleaned_up_files"]),
    )

    return stats

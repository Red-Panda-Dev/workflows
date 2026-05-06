"""Archive extraction logic for EPFR workflow.

Extracts archives (ZIP, TAR, GZ, TGZ) and preserves original filenames.
Detects OOXML documents (DOCX, XLSX, PPTX) disguised as ZIP and renames them.
"""

import asyncio
import logging
import os
import shutil
import tarfile
import tempfile
import zipfile
from pathlib import Path

logger = logging.getLogger(__name__)

ARCHIVE_EXTENSIONS = {".zip", ".tar", ".gz", ".tgz", ".tar.gz"}

MAX_CONCURRENT_EXTRACTS = 10

# OOXML marker files that indicate a ZIP is actually a document
OOXML_MARKERS = {"[Content_Types].xml", "word/document.xml", "xl/workbook.xml", "ppt/presentation.xml"}

# OOXML extension mapping based on internal structure
OOXML_EXTENSION_MAP = {
    "word/document.xml": ".docx",
    "xl/workbook.xml": ".xlsx",
    "ppt/presentation.xml": ".pptx",
}


def is_archive(filename: str) -> bool:
    """Return whether a downloaded EPFR file should be extracted.

    Args:
        filename: File name or path to inspect.

    Returns:
        True when the name uses a supported archive extension.
    """
    name_lower = filename.lower()
    if Path(filename).suffix.lower() in ARCHIVE_EXTENSIONS:
        return True
    return any(name_lower.endswith(ext) for ext in ARCHIVE_EXTENSIONS)


def _detect_ooxml_type(archive_path: Path) -> str | None:
    """Detect whether a ZIP payload is actually an OOXML document.

    EPFR returns bytes without trustworthy filenames, so ZIP files that contain
    Word, Excel, or PowerPoint structures are renamed instead of extracted.

    Args:
        archive_path: Path to the ZIP file.

    Returns:
        Correct OOXML extension if detected; otherwise None.
    """
    try:
        with zipfile.ZipFile(archive_path, "r") as zf:
            names = set(zf.namelist())

            # Must have [Content_Types].xml to be OOXML
            if "[Content_Types].xml" not in names:
                return None

            # Check for specific document types
            for marker, ext in OOXML_EXTENSION_MAP.items():
                if marker in names:
                    return ext

            return None
    except Exception:
        return None


def extract_zip(archive_path: Path, extract_dir: Path) -> tuple[int, list[str]]:
    """Extract a ZIP disclosure archive preserving original filenames.

    Args:
        archive_path: Path to the ZIP file.
        extract_dir: Temporary directory to extract into.

    Returns:
        Number of extracted files and their archive-provided names.
    """
    extracted_files: list[str] = []
    with zipfile.ZipFile(archive_path, "r") as zf:
        for info in zf.infolist():
            if not info.is_dir():
                zf.extract(info, extract_dir)
                extracted_files.append(info.filename)
    return len(extracted_files), extracted_files


def extract_tar(archive_path: Path, extract_dir: Path) -> tuple[int, list[str]]:
    """Extract a TAR/GZ disclosure archive preserving original filenames.

    Args:
        archive_path: Path to the TAR, GZ, or TGZ file.
        extract_dir: Temporary directory to extract into.

    Returns:
        Number of extracted files and their archive-provided names.
    """
    name_lower = str(archive_path).lower()

    # Determine mode based on extension
    if name_lower.endswith(".tar.gz") or name_lower.endswith(".tgz") or name_lower.endswith(".gz"):
        tf = tarfile.open(archive_path, "r:gz")
    else:
        tf = tarfile.open(archive_path, "r")

    extracted_files: list[str] = []
    try:
        for member in tf.getmembers():
            if member.isfile():
                tf.extract(member, extract_dir)
                extracted_files.append(member.name)
    finally:
        tf.close()
    return len(extracted_files), extracted_files


def extract_archive(archive_path: Path) -> tuple[bool, str | None, int, list[str]]:
    """Extract one archive into its UNP folder or rename OOXML content.

    Uses a temporary extraction directory and then flattens extracted files into
    the company folder so the mapping can reference simple file names.

    Args:
        archive_path: Path to the archive file to extract.

    Returns:
        Success flag, optional error message, number of extracted files, and
        flattened file names written to the company folder.
    """
    try:
        parent_dir = archive_path.parent
        filename_lower = archive_path.name.lower()

        # Check if this is a ZIP that's actually an OOXML document
        if filename_lower.endswith(".zip"):
            ooxml_ext = _detect_ooxml_type(archive_path)
            if ooxml_ext:
                # Rename to correct extension instead of extracting
                new_name = archive_path.stem + ooxml_ext
                new_path = parent_dir / new_name
                if new_path.exists():
                    new_path.unlink()
                archive_path.rename(new_path)
                logger.info("Renamed OOXML document %s to %s", archive_path.name, new_name)
                return (True, None, 1, [new_name])

        # Create temp directory for extraction
        temp_dir = Path(tempfile.mkstemp(dir=str(parent_dir), prefix=".extract_")[1])
        os.unlink(temp_dir)  # mkstemp creates a file, we need a dir
        temp_dir.mkdir()

        try:
            # Extract based on file type
            if filename_lower.endswith(".zip"):
                count, extracted_names = extract_zip(archive_path, temp_dir)
            else:
                count, extracted_names = extract_tar(archive_path, temp_dir)

            # Flatten: move all files to parent directory, regardless of nesting
            final_files: list[str] = []

            def move_files_recursive(source_dir: Path) -> None:
                """Move extracted files into the company folder.

                Args:
                    source_dir: Current temporary extraction directory to flatten.
                """
                for item in source_dir.iterdir():
                    if item.is_dir():
                        move_files_recursive(item)
                        # Remove empty directory after processing
                        shutil.rmtree(item, ignore_errors=True)
                    else:
                        dest = parent_dir / item.name
                        if dest.exists():
                            dest.unlink()
                        shutil.move(str(item), str(dest))
                        final_files.append(item.name)

            move_files_recursive(temp_dir)

            # Remove archive file - extraction successful
            archive_path.unlink()

            # Clean up temp dir
            shutil.rmtree(temp_dir, ignore_errors=True)

            logger.info("Extracted %s: %d files (flattened)", archive_path, len(final_files))
            return (True, None, len(final_files), final_files)

        except Exception as e:
            # Clean up temp dir on error
            shutil.rmtree(temp_dir, ignore_errors=True)
            error = f"{type(e).__name__}: {e}"
            logger.error("Failed to extract %s: %s", archive_path, error)
            return (False, error, 0, [])

    except Exception as e:
        error = f"{type(e).__name__}: {e}"
        logger.error("Unexpected error processing %s: %s", archive_path, error)
        return (False, error, 0, [])


async def extract_unp_archives(
    unp: str,
    folder_path: Path,
    semaphore: asyncio.Semaphore,
) -> tuple[str, int, int, list[str], int, list[str], dict[str, list[str]]]:
    """Extract all supported archives for one company UNP folder.

    Produces archive-to-file lineage so the final mapping can replace archive
    entries with the actual disclosure documents that were inside them.

    Args:
        unp: Company tax identifier for logging and statistics.
        folder_path: Path to the company folder.
        semaphore: Concurrency limiter shared across UNP folders.

    Returns:
        Tuple containing UNP, archive success and failure counts, failed archive
        paths, extracted file count, extracted file names, and archive lineage.
    """
    if not folder_path.exists():
        logger.warning("UNP folder does not exist: %s", folder_path)
        return (unp, 0, 0, [], 0, [], {})

    # Find all archive files in folder
    archive_files = [f for f in folder_path.iterdir() if f.is_file() and is_archive(f.name)]

    if not archive_files:
        logger.debug("No archives found for %s", unp)
        return (unp, 0, 0, [], 0, [], {})

    logger.info("Found %d archives to extract for %s", len(archive_files), unp)

    tasks = []
    for archive_path in archive_files:
        async with semaphore:
            task = asyncio.create_task(asyncio.to_thread(extract_archive, archive_path))
            tasks.append(task)

    results = await asyncio.gather(*tasks)

    success = sum(1 for suc, _, _, _ in results if suc)
    failure = sum(1 for suc, _, _, _ in results if not suc)
    failed_archives = [str(archive_files[i]) for i, (suc, _, _, _) in enumerate(results) if not suc]
    files_extracted = sum(count for _, _, count, _ in results)
    all_extracted_files = [name for _, _, _, names in results for name in names]

    # Build archive -> extracted files mapping
    archive_to_files: dict[str, list[str]] = {}
    for i, (suc, _, _, names) in enumerate(results):
        if suc and names:
            archive_to_files[archive_files[i].name] = names

    logger.info(
        "Extracted %d files from %d archives for %s (%d failed)",
        files_extracted,
        success,
        unp,
        failure,
    )

    return (unp, success, failure, failed_archives, files_extracted, all_extracted_files, archive_to_files)


async def extract_all_archives(
    unp_folders: list[str],
    output_root: Path,
) -> dict:
    """Extract downloaded archives across all company folders.

    Aggregates per-company archive processing results for the mapping activity
    that decides which original archive entries should be replaced.

    Args:
        unp_folders: Company UNP folder names to process.
        output_root: Root output directory containing UNP folders.

    Returns:
        Extraction statistics with aggregate totals, failed archive paths, and
        per-UNP archive lineage details.
    """
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_EXTRACTS)

    tasks = []
    for unp in unp_folders:
        folder_path = output_root / unp
        task = asyncio.create_task(extract_unp_archives(unp, folder_path, semaphore))
        tasks.append(task)

    unp_results = await asyncio.gather(*tasks)

    # Aggregate statistics
    stats: dict = {
        "total_unps": len(unp_results),
        "total_archives": 0,
        "successful": 0,
        "failed": 0,
        "failed_archives": [],
        "files_extracted": 0,
        "by_unp": {},
    }

    for unp, success, failure, failed_archives, files_extracted, extracted_files, archive_to_files in unp_results:
        stats["total_archives"] += success + failure
        stats["successful"] += success
        stats["failed"] += failure
        stats["failed_archives"].extend(failed_archives)
        stats["files_extracted"] += files_extracted
        stats["by_unp"][unp] = {
            "archives_extracted": success,
            "archives_failed": failure,
            "failed_archives": failed_archives,
            "extracted_files": extracted_files,
            "archive_to_files": archive_to_files,
        }

    return stats

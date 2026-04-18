"""Archive extraction logic for CentralDepo workflow."""

import asyncio
import logging
import os
import shutil
import tarfile
import tempfile
import zipfile
from pathlib import Path
from typing import List, Tuple

from .downloader import get_company_folder_name

logger = logging.getLogger(__name__)

# Archive extensions to detect
ARCHIVE_EXTENSIONS = {".zip", ".tar", ".gz", ".tgz", ".tar.gz"}

# Reuse the same concurrency limit as downloads
MAX_CONCURRENT_EXTRACTS = 10


def is_archive(filename: str) -> bool:
    """Check if a file is a supported archive format.

    Args:
        filename: Filename to check (can include path)

    Returns:
        True if file is a supported archive format
    """
    name_lower = filename.lower()
    # Check simple extensions
    if Path(filename).suffix.lower() in ARCHIVE_EXTENSIONS:
        return True
    # Check compound extensions like .tar.gz
    return any(name_lower.endswith(ext) for ext in ARCHIVE_EXTENSIONS)


def extract_zip(archive_path: Path, extract_dir: Path) -> int:
    """Extract a ZIP archive.

    Args:
        archive_path: Path to the ZIP file
        extract_dir: Directory to extract to

    Returns:
        Number of files extracted
    """
    count = 0
    with zipfile.ZipFile(archive_path, "r") as zf:
        for info in zf.infolist():
            if not info.is_dir():
                zf.extract(info, extract_dir)
                count += 1
    return count


def extract_tar(archive_path: Path, extract_dir: Path) -> int:
    """Extract a TAR, GZ, or TGZ archive.

    Args:
        archive_path: Path to the TAR file
        extract_dir: Directory to extract to

    Returns:
        Number of files extracted
    """
    mode_map = {
        ".tar": "r",
        ".gz": "r:gz",
        ".tgz": "r:gz",
        ".tar.gz": "r:gz",
    }

    # Detect mode from extension
    name_lower = str(archive_path).lower()
    mode = "r"
    for ext, m in mode_map.items():
        if name_lower.endswith(ext):
            mode = m
            break

    count = 0
    with tarfile.open(archive_path, mode) as tf:
        for member in tf.getmembers():
            if member.isfile():
                tf.extract(member, extract_dir)
                count += 1
    return count


def extract_archive(archive_path: Path) -> Tuple[bool, str | None, int]:
    """Extract a single archive file.

    Uses atomic extraction pattern: extract to temp dir first, then move files.

    Args:
        archive_path: Path to the archive file to extract

    Returns:
        Tuple of (success, error_message, files_extracted_count)
    """
    try:
        parent_dir = archive_path.parent

        # Create temp directory for extraction
        temp_dir = Path(tempfile.mkstemp(dir=str(parent_dir), prefix=".extract_")[1])
        os.unlink(temp_dir)  # mkstemp creates a file, we need a dir
        temp_dir.mkdir()

        try:
            filename_lower = archive_path.name.lower()

            # Extract based on file type
            if filename_lower.endswith(".zip"):
                count = extract_zip(archive_path, temp_dir)
            else:
                count = extract_tar(archive_path, temp_dir)

            # Move extracted files to parent directory with lowercase names
            for item in temp_dir.iterdir():
                dest = parent_dir / item.name.lower()
                if dest.exists():
                    # Overwrite existing files
                    if dest.is_dir():
                        shutil.rmtree(dest)
                    else:
                        dest.unlink()
                shutil.move(str(item), str(dest))

            # Remove archive file - extraction successful
            archive_path.unlink()

            # Clean up temp dir
            shutil.rmtree(temp_dir, ignore_errors=True)

            logger.info("Extracted %s: %d files", archive_path, count)
            return (True, None, count)

        except Exception as e:
            # Clean up temp dir on error
            shutil.rmtree(temp_dir, ignore_errors=True)
            error = f"{type(e).__name__}: {e}"
            logger.error("Failed to extract %s: %s", archive_path, error)
            return (False, error, 0)

    except Exception as e:
        error = f"{type(e).__name__}: {e}"
        logger.error("Unexpected error processing %s: %s", archive_path, error)
        return (False, error, 0)


async def extract_company_archives(
    company_name: str,
    folder_path: Path,
    semaphore: asyncio.Semaphore,
) -> Tuple[str, int, int, List[str]]:
    """Extract all archives for a single company.

    Args:
        company_name: Company name for logging
        folder_path: Path to the company folder
        semaphore: Concurrency limiter

    Returns:
        Tuple of (company_name, success_count, failure_count, failed_archives, files_extracted)
    """
    if not folder_path.exists():
        logger.warning("Company folder does not exist: %s", folder_path)
        return (company_name, 0, 0, [], 0)

    # Find all archive files in folder
    archive_files = [f for f in folder_path.iterdir() if f.is_file() and is_archive(f.name)]

    if not archive_files:
        logger.debug("No archives found for %s", company_name)
        return (company_name, 0, 0, [], 0)

    logger.info("Found %d archives to extract for %s", len(archive_files), company_name)

    tasks = []
    for archive_path in archive_files:
        async with semaphore:
            # Use to_thread to avoid blocking event loop
            task = asyncio.create_task(asyncio.to_thread(extract_archive, archive_path))
            tasks.append(task)

    results = await asyncio.gather(*tasks)

    success = sum(1 for suc, _, _ in results if suc)
    failure = sum(1 for suc, _, _ in results if not suc)
    failed_archives = [str(archive_files[i]) for i, (suc, _, _) in enumerate(results) if not suc]
    files_extracted = sum(count for _, _, count in results)

    logger.info(
        "Extracted %d files from %d archives for %s (%d failed)",
        files_extracted,
        success,
        company_name,
        failure,
    )

    return (company_name, success, failure, failed_archives, files_extracted)


async def extract_all_archives(
    results: List[Tuple[str, List[str]]],
    output_root: Path,
) -> dict:
    """Extract archives for all companies in parallel.

    Args:
        results: List of (company_name, urls) tuples (same as for download)
        output_root: Root output directory

    Returns:
        Dictionary with extraction statistics:
        - total_companies
        - total_archives
        - successful
        - failed
        - failed_archives (list of paths that failed)
        - files_extracted (total count)
        - by_company (per-company breakdown)
    """
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_EXTRACTS)

    tasks = []
    for company_name, _ in results:
        folder_name = get_company_folder_name(company_name)
        folder_path = output_root / folder_name
        task = asyncio.create_task(extract_company_archives(company_name, folder_path, semaphore))
        tasks.append(task)

    company_results = await asyncio.gather(*tasks)

    # Aggregate statistics
    stats = {
        "total_companies": len(company_results),
        "total_archives": 0,
        "successful": 0,
        "failed": 0,
        "failed_archives": [],
        "files_extracted": 0,
        "by_company": {},
    }

    for company, success, failure, failed_archives, files_extracted in company_results:
        stats["total_archives"] += success + failure
        stats["successful"] += success
        stats["failed"] += failure
        stats["failed_archives"].extend(failed_archives)
        stats["files_extracted"] += files_extracted
        stats["by_company"][company] = {
            "archives_extracted": success,
            "archives_failed": failure,
            "failed_archives": failed_archives,
        }

    return stats

"""HTTP client for EPFR workflow.

Handles two concerns:
  1. Fetching paginated JSON from the EPFR securities-market API.
  2. Streaming file downloads with magic-byte extension detection.
"""

import asyncio
import logging
import os
from pathlib import Path
import tempfile
from urllib.parse import urlencode

import aiohttp

from .config import load_epfr_config
from .detector import build_filename
from .models import EpfrApiResponse, EpfrRecord


logger = logging.getLogger(__name__)


def build_page_url(page_no: int, date_from: str, date_to: str | None = None) -> str:
    """Build the EPFR dividend disclosure search URL.

    Keeps EPFR's zero-based pagination and configured dividend search filters
    in one place so workflow activities call the upstream API consistently.

    Args:
        page_no: Zero-based page number.
        date_from: Date filter in YYYY-MM-DD format.
        date_to: Optional end date filter in YYYY-MM-DD format.

    Returns:
        Full URL string with query parameters.

    """
    cfg = load_epfr_config()
    params = {
        "search": cfg.default_search_query,
        "pageNo": page_no,
        "sortField": cfg.default_sort_field,
        "sortDir": cfg.default_sort_dir,
        "searchDateFrom": date_from,
        "subCategoryId": cfg.default_sub_category_id,
    }
    if date_to:
        params["searchDateTo"] = date_to
    return f"{cfg.base_api_url}?{urlencode(params)}"


def build_download_url(record_id: int) -> str:
    """Build the raw file-content download URL for a disclosure record.

    EPFR stores disclosure attachments behind record-specific portal URLs;
    downloaded bytes are later grouped by company UNP.

    Args:
        record_id: EPFR record ID.

    Returns:
        Download URL string.

    """
    cfg = load_epfr_config()
    return cfg.file_download_url_template.format(record_id=record_id)


async def fetch_page(
    session: aiohttp.ClientSession,
    page_no: int,
    date_from: str,
    timeout: int = 60,
    date_to: str | None = None,
) -> EpfrApiResponse:
    """Fetch one EPFR disclosure page with transient-error retries.

    Retrieves dividend disclosure metadata from the upstream securities-market
    endpoint and normalizes the response into local Pydantic models.

    Args:
        session: Shared aiohttp session.
        page_no: Zero-based page number.
        date_from: Date filter in YYYY-MM-DD format.
        timeout: Per-request timeout in seconds.
        date_to: Optional end date filter in YYYY-MM-DD format.

    Returns:
        Parsed EpfrApiResponse.

    Raises:
        RuntimeError: If all retry attempts fail.

    """
    url = build_page_url(page_no, date_from, date_to)
    cfg = load_epfr_config()

    for attempt in range(1, cfg.max_retries + 1):
        try:
            async with session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                if resp.status >= 500:
                    text = await resp.text()
                    logger.warning(
                        "Page %d attempt %d/%d: HTTP %d: %s",
                        page_no,
                        attempt,
                        cfg.max_retries,
                        resp.status,
                        text[:200],
                    )
                    if attempt < cfg.max_retries:
                        backoff = min(cfg.retry_backoff_max, cfg.retry_backoff_base**attempt)
                        await asyncio.sleep(backoff)
                    continue

                if resp.status != 200:
                    text = await resp.text()
                    raise RuntimeError(f"Page {page_no}: HTTP {resp.status}: {text[:200]}")

                data = await resp.json()
                return EpfrApiResponse.model_validate(data)

        except (TimeoutError, aiohttp.ClientError) as exc:
            logger.warning(
                "Page %d attempt %d/%d: %s: %s",
                page_no,
                attempt,
                cfg.max_retries,
                type(exc).__name__,
                exc,
            )
            if attempt < cfg.max_retries:
                backoff = min(cfg.retry_backoff_max, cfg.retry_backoff_base**attempt)
                await asyncio.sleep(backoff)

    raise RuntimeError(f"Page {page_no}: all {cfg.max_retries} attempts failed")


async def download_file(
    session: aiohttp.ClientSession,
    record_id: int,
    company_dir: Path,
    semaphore: asyncio.Semaphore,
) -> tuple[int, bool, str | None, str]:
    """Download one EPFR disclosure attachment into a company folder.

    Streams raw portal bytes, detects the extension from the first chunk, and
    writes through a temporary file so partial downloads are never exposed as
    final business artifacts.

    Args:
        session: Shared aiohttp session.
        record_id: EPFR record ID.
        company_dir: Target directory for the file.
        semaphore: Concurrency limiter.

    Returns:
        Tuple of (record_id, success, error_message, filename).

    """
    url = build_download_url(record_id)
    cfg = load_epfr_config()

    async with semaphore:
        for attempt in range(1, cfg.download_retries + 1):
            try:
                company_dir.mkdir(parents=True, exist_ok=True)

                fd, tmp_path = tempfile.mkstemp(
                    dir=str(company_dir),
                    prefix=".download_",
                    suffix=".tmp",
                )

                try:
                    async with session.get(
                        url,
                        timeout=aiohttp.ClientTimeout(total=cfg.download_timeout),
                    ) as resp:
                        if resp.status != 200:
                            text = await resp.text()
                            error = f"HTTP {resp.status}: {text[:200]}"
                            logger.warning(
                                "Download %d attempt %d/%d: %s",
                                record_id,
                                attempt,
                                cfg.download_retries,
                                error,
                            )
                            os.close(fd)
                            if os.path.exists(tmp_path):
                                os.unlink(tmp_path)
                            if attempt < cfg.download_retries:
                                await asyncio.sleep(2**attempt)
                            continue

                        first_chunk = True
                        extension = ".bin"
                        total_bytes = 0

                        async for chunk in resp.content.iter_chunked(cfg.chunk_size):
                            if first_chunk and chunk:
                                extension = build_filename(record_id, chunk).split(".", maxsplit=1)[1]
                                extension = f".{extension}"
                                first_chunk = False
                            total_bytes += len(chunk)
                            os.write(fd, chunk)

                        filename = f"{record_id}{extension}"
                        final_path = company_dir / filename

                        os.close(fd)
                        os.replace(tmp_path, str(final_path))

                        logger.info(
                            "Downloaded %s (%d bytes) to %s",
                            url,
                            total_bytes,
                            final_path,
                        )
                        return (record_id, True, None, filename)

                except (TimeoutError, aiohttp.ClientError) as exc:
                    os.close(fd)
                    if os.path.exists(tmp_path):
                        os.unlink(tmp_path)
                    error = f"{type(exc).__name__}: {exc}"
                    logger.warning(
                        "Download %d attempt %d/%d: %s",
                        record_id,
                        attempt,
                        cfg.download_retries,
                        error,
                    )
                    if attempt < cfg.download_retries:
                        await asyncio.sleep(2**attempt)
                    continue

            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                logger.error(
                    "Unexpected error downloading %d (attempt %d/%d): %s",
                    record_id,
                    attempt,
                    cfg.download_retries,
                    error,
                )
                if attempt < cfg.download_retries:
                    await asyncio.sleep(2**attempt)
                continue

        return (record_id, False, f"Failed after {cfg.download_retries} attempts", "")


async def download_all_files(
    records: list[EpfrRecord],
    output_dir: Path,
) -> dict:
    """Download all disclosure attachments grouped by company UNP.

    Each EPFR record is routed to the holder UNP, matching the mapping
    layout consumed by later extraction and conversion stages.

    Args:
        records: List of EpfrRecord objects from the API.
        output_dir: Root output directory (e.g. Path('output')).

    Returns:
        Dictionary with download statistics:
        - total_records, total_files_attempted, successful, failed
        - failed_ids: list of record IDs that failed
        - file_map: {record_id: filename} for successful downloads
        - by_unp: {unp: {success, failed, files: [{id, filename}]}}

    """
    cfg = load_epfr_config()
    semaphore = asyncio.Semaphore(cfg.max_concurrent_downloads)

    grouped: dict[str, list[EpfrRecord]] = {}
    for rec in records:
        unp = _get_unp(rec)
        grouped.setdefault(unp, []).append(rec)

    async with aiohttp.ClientSession() as session:
        tasks = []
        for unp, unp_records in grouped.items():
            company_dir = output_dir / unp
            for rec in unp_records:
                tasks.append(asyncio.create_task(download_file(session, rec.id, company_dir, semaphore)))

        results = await asyncio.gather(*tasks)

    failed_ids: list[int] = []
    file_map: dict[int, str] = {}
    successful = 0
    failed = 0

    unp_lookup: dict[int, str] = {}
    for rec in records:
        unp_lookup[rec.id] = _get_unp(rec)

    by_unp_success: dict[str, int] = dict.fromkeys(grouped, 0)
    by_unp_failed: dict[str, int] = dict.fromkeys(grouped, 0)
    by_unp_files: dict[str, list[dict[str, object]]] = {unp: [] for unp in grouped}

    for record_id, success, error, filename in results:
        unp = unp_lookup.get(record_id, "unknown")
        if success:
            successful += 1
            file_map[record_id] = filename
            by_unp_success[unp] += 1
            by_unp_files[unp].append({"id": record_id, "filename": filename})
        else:
            failed += 1
            failed_ids.append(record_id)
            by_unp_failed[unp] += 1
            logger.warning("Failed to download record %d: %s", record_id, error)

    by_unp = {
        unp: {
            "success": by_unp_success[unp],
            "failed": by_unp_failed[unp],
            "files": by_unp_files[unp],
        }
        for unp in grouped
    }

    return {
        "total_records": len(records),
        "total_files_attempted": len(results),
        "successful": successful,
        "failed": failed,
        "failed_ids": failed_ids,
        "file_map": file_map,
        "by_unp": by_unp,
    }


def _get_unp(record: EpfrRecord) -> str:
    """Return the holder UNP used for output grouping.

    Args:
        record: EPFR disclosure record with optional holder.

    Returns:
        Holder UNP when present, otherwise ``"unknown"`` for records
        that cannot be attributed.

    """
    if record.holder and record.holder.unp:
        return record.holder.unp
    return "unknown"

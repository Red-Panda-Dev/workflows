"""HTML parsing logic for CentralDepo workflow."""

import re
from collections import defaultdict
from urllib.parse import urljoin

from .config import BASE_URL
from .downloader import get_company_folder_name
from .models import CompanyResult, DividendRecord

# Regex to extract href from HTML
HREF_RE = re.compile(r'href="([^"]+)"')


def parse_items(raw_items: list[dict], page: int) -> list[DividendRecord]:
    """Parse raw HTML elements into DividendRecord objects.

    Args:
        raw_items: List of element dicts from CF scrape response, each with
                   "text" and "html" keys.
        page: Current page number, used for logging skipped items.

    Returns:
        List of valid DividendRecord instances.
    """
    import logging

    logger = logging.getLogger(__name__)

    records: list[DividendRecord] = []
    for idx, item in enumerate(raw_items, 1):
        text = (item.get("text") or "").strip()
        html = item.get("html") or ""

        href_match = HREF_RE.search(html)
        if not href_match:
            logger.warning("Page %d item %d: no href found, skipping", page, idx)
            continue

        company_name = text
        archive_url = urljoin(BASE_URL, href_match.group(1))

        if not company_name:
            logger.warning("Page %d item %d: empty company name, skipping", page, idx)
            continue

        records.append(DividendRecord(company_name=company_name, archive_url=archive_url))

    return records


def transform_to_output(records: list[DividendRecord]) -> list[CompanyResult]:
    """Transform list of records to output format.

    Groups all URLs by company name (lowercase for consistent grouping).
    Each company appears once with all its URLs.
    Results are sorted alphabetically by lowercase company name.
    Original case is preserved in the output.

    Args:
        records: List of DividendRecord objects from scraping

    Returns:
        List of CompanyResult objects grouped by lowercase company name
    """
    # Group by lowercase company name for consistent grouping
    company_urls: dict[str, list[str]] = defaultdict(list)

    for record in records:
        company_name_lower = record.company_name.lower()
        company_urls[company_name_lower].append(record.archive_url)

    # Preserve original case from first occurrence of each company
    name_mapping: dict[str, str] = {}
    for record in records:
        lower = record.company_name.lower()
        if lower not in name_mapping:
            name_mapping[lower] = record.company_name

    # Build results with original case, deduplicated URLs
    results: list[CompanyResult] = []
    for name_lower, urls in company_urls.items():
        original_name = name_mapping.get(name_lower, name_lower)
        results.append(
            CompanyResult(
                company_name=original_name,
                company_hash=get_company_folder_name(original_name),
                urls=sorted(set(urls)),
            )
        )

    # Sort by lowercase name for consistency
    results.sort(key=lambda x: x.company_name.lower())

    return results

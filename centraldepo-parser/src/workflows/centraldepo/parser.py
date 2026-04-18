"""HTML parsing logic for CentralDepo workflow."""

import re
from typing import List
from urllib.parse import urljoin

from .config import BASE_URL
from .models import CompanyResult, DividendRecord

# Regex to extract href from HTML
HREF_RE = re.compile(r'href="([^"]+)"')


def parse_items(raw_items: List[dict], page: int) -> List[DividendRecord]:
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

    records: List[DividendRecord] = []
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


def transform_to_output(records: List[DividendRecord]) -> List[CompanyResult]:
    """Transform list of records to output format: [{company_name, urls: [...]}, ...].

    Groups all URLs by company name. Each company appears once with all its URLs.
    Results are sorted alphabetically by company name.

    Args:
        records: List of DividendRecord objects from scraping

    Returns:
        List of CompanyResult objects grouped by company name
    """
    from collections import defaultdict

    company_urls = defaultdict(list)

    for record in records:
        company_urls[record.company_name].append(record.archive_url)

    # Convert to list of CompanyResult objects, sorted by company name
    results = [CompanyResult(company_name=name, urls=sorted(set(urls))) for name, urls in company_urls.items()]
    results.sort(key=lambda x: x.company_name)

    return results

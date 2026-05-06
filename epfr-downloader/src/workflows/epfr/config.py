"""Configuration constants for EPFR disclosure processing workflows.

The values define upstream API access, retry and concurrency limits, default
workflow inputs, output artifact names, and OCR safety limits shared by the
EPFR downloader and PDF OCR workflows.
"""

from pathlib import Path

BASE_API_URL = "https://epfr.gov.by/portal/reporting/securities-market"
FILE_DOWNLOAD_URL_TEMPLATE = "https://epfr.gov.by/portal/file/{record_id}/content"

DEFAULT_SEARCH_QUERY = "дивиденд"
DEFAULT_SUB_CATEGORY_ID = 1
DEFAULT_SORT_FIELD = "realUploadDate"
DEFAULT_SORT_DIR = "desc"

DEFAULT_MAX_PAGES = 10
DEFAULT_TIMEOUT = 60

MAX_CONCURRENT_DOWNLOADS = 10
DOWNLOAD_TIMEOUT = 120
DOWNLOAD_RETRIES = 3
CHUNK_SIZE = 8192

MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 2
RETRY_BACKOFF_MAX = 30

MAX_CONCURRENT_OCR = 2
MAX_PDF_SIZE_BYTES = 50 * 1024 * 1024
OCR_MODEL = "mistral-ocr-latest"

AI_MODEL = "mistral-large-latest"
AI_TEMPERATURE = 0.0
AI_TIMEOUT = 60
AI_MAX_RETRIES = 3
AI_RETRY_BACKOFF_BASE = 2
AI_FILE_DELAY = 1

DEFAULT_OUTPUT_DIR = Path("output")
MAPPING_FILENAME = "unp_file_mapping.json"
AI_DISTILLED_FILENAME = "ai_distilled_dividends.json"

FIRST_PAGE_NO = 0
PAGE_DELAY = 1.0

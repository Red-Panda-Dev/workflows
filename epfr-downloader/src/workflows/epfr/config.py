"""Configuration constants for EPFR disclosure processing workflows.

The values define upstream API access, retry and concurrency limits, default
workflow inputs, output artifact names, and OCR safety limits shared by the
EPFR downloader and PDF OCR workflows.

Two APIs coexist:
  - **Module-level constants** (backward compat) — e.g. ``BASE_API_URL``.
  - **Typed dataclass** — ``EpfrConfig`` / ``load_epfr_config()`` for new code.
"""

from dataclasses import dataclass, replace
from datetime import date
import os
from pathlib import Path
from typing import TypedDict


class ResolvedWorkflowInput(TypedDict):
    """Resolved runtime values for the main downloader workflow."""

    max_pages: int
    date_from: str
    date_to: str
    timeout: int
    output_dir: str


class ResolvedPdfOcrInput(TypedDict):
    """Resolved runtime values for the PDF OCR workflow."""

    output_dir: str
    mapping_filename: str
    cleanup_source: bool


class ResolvedAiDistillerInput(TypedDict):
    """Resolved runtime values for the AI distiller workflow."""

    output_dir: str
    mapping_filename: str
    output_filename: str
    model_name: str
    temperature: float
    max_retries: int
    max_tokens: int
    file_delay_seconds: float


class ResolvedSharePayoutExportInput(TypedDict):
    """Resolved runtime values for the share payout export workflow."""

    output_dir: str
    input_filename: str
    output_filename: str


@dataclass(frozen=True)
class EpfrConfig:
    """Immutable configuration for all four EPFR workflows."""

    base_api_url: str
    file_download_url_template: str
    default_search_query: str
    default_sub_category_id: int
    default_sort_field: str
    default_sort_dir: str

    max_pages: int
    first_page_no: int
    page_delay: float

    max_concurrent_downloads: int
    download_timeout: int
    download_retries: int
    chunk_size: int

    max_retries: int
    retry_backoff_base: int
    retry_backoff_max: int

    max_concurrent_ocr: int
    max_pdf_size_bytes: int
    ocr_model: str
    ocr_supported_extensions: set[str]

    ai_model: str
    ai_temperature: float
    ai_timeout: int
    ai_max_retries: int
    ai_max_tokens: int
    ai_retry_backoff_base: int
    ai_retry_backoff_max: int
    ai_retry_backoff_max_429: int
    ai_retry_jitter_ratio: float
    ai_file_delay: int

    output_dir: Path
    mapping_filename: str
    ai_distilled_filename: str

    default_date_from: str
    default_date_to: str
    default_timeout: int

    share_payout_export_filename: str
    share_dividends_sql_filename: str

    cleanup_source: bool

    mistral_api_key: str | None

    server_url: str
    deployment_name: str


EPFR_DEFAULTS = EpfrConfig(
    base_api_url="https://epfr.gov.by/portal/reporting/securities-market",
    file_download_url_template="https://epfr.gov.by/portal/file/{record_id}/content",
    default_search_query="дивиденд",
    default_sub_category_id=1,
    default_sort_field="realUploadDate",
    default_sort_dir="desc",
    max_pages=10,
    first_page_no=0,
    page_delay=1.0,
    max_concurrent_downloads=10,
    download_timeout=120,
    download_retries=3,
    chunk_size=8192,
    max_retries=3,
    retry_backoff_base=2,
    retry_backoff_max=30,
    max_concurrent_ocr=2,
    max_pdf_size_bytes=50 * 1024 * 1024,
    ocr_model="mistral-ocr-latest",
    ocr_supported_extensions={".pdf", ".png", ".jpg", ".jpeg"},
    ai_model="ministral-8b-latest",
    ai_temperature=0.0,
    ai_timeout=60,
    ai_max_retries=3,
    ai_max_tokens=4000,
    ai_retry_backoff_base=2,
    ai_retry_backoff_max=30,
    ai_retry_backoff_max_429=90,
    ai_retry_jitter_ratio=0.25,
    ai_file_delay=1,
    output_dir=Path("output"),
    mapping_filename="unp_file_mapping.json",
    ai_distilled_filename="ai_distilled_dividends.json",
    default_date_from="2022-01-01",
    default_date_to="",
    default_timeout=60,
    share_payout_export_filename="share_payouts_by_unp.json",
    share_dividends_sql_filename="share_dividends_insert.sql",
    cleanup_source=True,
    mistral_api_key=None,
    server_url="https://api.mistral.ai",
    deployment_name="default",
)


_ENV_FIELDS: list[tuple[str, type, str, str | None]] = [
    ("base_api_url", str, "EPFR_BASE_API_URL", None),
    ("file_download_url_template", str, "EPFR_FILE_DOWNLOAD_URL_TEMPLATE", None),
    ("default_search_query", str, "EPFR_DEFAULT_SEARCH_QUERY", None),
    ("default_sub_category_id", int, "EPFR_DEFAULT_SUB_CATEGORY_ID", "positive_int"),
    ("default_sort_field", str, "EPFR_DEFAULT_SORT_FIELD", None),
    ("default_sort_dir", str, "EPFR_DEFAULT_SORT_DIR", None),
    ("max_pages", int, "EPFR_MAX_PAGES", "positive_int"),
    ("first_page_no", int, "EPFR_FIRST_PAGE_NO", "non_negative_int"),
    ("page_delay", float, "EPFR_PAGE_DELAY", "positive_float"),
    ("max_concurrent_downloads", int, "EPFR_MAX_CONCURRENT_DOWNLOADS", "positive_int"),
    ("download_timeout", int, "EPFR_DOWNLOAD_TIMEOUT", "positive_int"),
    ("download_retries", int, "EPFR_DOWNLOAD_RETRIES", "positive_int"),
    ("chunk_size", int, "EPFR_CHUNK_SIZE", "positive_int"),
    ("max_retries", int, "EPFR_MAX_RETRIES", "positive_int"),
    ("retry_backoff_base", int, "EPFR_RETRY_BACKOFF_BASE", "positive_int"),
    ("retry_backoff_max", int, "EPFR_RETRY_BACKOFF_MAX", "positive_int"),
    ("max_concurrent_ocr", int, "EPFR_MAX_CONCURRENT_OCR", "positive_int"),
    ("max_pdf_size_bytes", int, "EPFR_MAX_PDF_SIZE_BYTES", "positive_int"),
    ("ocr_model", str, "EPFR_OCR_MODEL", "mistral-ocr-latest"),
    ("ocr_supported_extensions", set, "EPFR_OCR_SUPPORTED_EXTENSIONS", None),
    ("ai_model", str, "EPFR_AI_MODEL", "ministral-14b-latest"),
    ("ai_temperature", float, "EPFR_AI_TEMPERATURE", "non_negative_float"),
    ("ai_timeout", int, "EPFR_AI_TIMEOUT", "positive_int"),
    ("ai_max_retries", int, "EPFR_AI_MAX_RETRIES", "positive_int"),
    ("ai_max_tokens", int, "EPFR_AI_MAX_TOKENS", "positive_int"),
    ("ai_retry_backoff_base", int, "EPFR_AI_RETRY_BACKOFF_BASE", "positive_int"),
    ("ai_retry_backoff_max", int, "EPFR_AI_RETRY_BACKOFF_MAX", "positive_int"),
    ("ai_retry_backoff_max_429", int, "EPFR_AI_RETRY_BACKOFF_MAX_429", "positive_int"),
    ("ai_retry_jitter_ratio", float, "EPFR_AI_RETRY_JITTER_RATIO", "non_negative_float"),
    ("ai_file_delay", int, "EPFR_AI_FILE_DELAY", "non_negative_int"),
    ("output_dir", Path, "EPFR_OUTPUT_DIR", None),
    ("mapping_filename", str, "EPFR_MAPPING_FILENAME", None),
    ("ai_distilled_filename", str, "EPFR_AI_DISTILLED_FILENAME", None),
    ("default_date_from", str, "EPFR_DEFAULT_DATE_FROM", None),
    ("default_date_to", str, "EPFR_DEFAULT_DATE_TO", None),
    ("default_timeout", int, "EPFR_DEFAULT_TIMEOUT", "positive_int"),
    ("share_payout_export_filename", str, "EPFR_SHARE_PAYOUT_EXPORT_FILENAME", None),
    ("share_dividends_sql_filename", str, "EPFR_SHARE_DIVIDENDS_SQL_FILENAME", None),
    ("cleanup_source", bool, "EPFR_CLEANUP_SOURCE", "bool"),
    ("server_url", str, "SERVER_URL", None),
    ("deployment_name", str, "DEPLOYMENT_NAME", None),
]


def _parse_value(raw: str, target_type: type, env_key: str) -> object:
    if target_type is bool:
        lower = raw.lower()
        if lower == "true":
            return True
        if lower == "false":
            return False
        raise ValueError(f"Invalid boolean value for {env_key}: {raw!r} (expected 'true' or 'false')")
    try:
        if target_type is int:
            return int(raw)
        if target_type is float:
            return float(raw)
        if target_type is Path:
            return Path(raw)
        return raw
    except (ValueError, TypeError) as exc:
        raise ValueError(f"Invalid value for {env_key}: {raw!r}") from exc


def _validate(value: object, validation_kind: str | None, env_key: str) -> None:
    if validation_kind is None:
        return
    if validation_kind == "positive_int":
        if not isinstance(value, int) or value < 1:
            raise ValueError(f"{env_key} must be a positive integer, got {value!r}")
    elif validation_kind == "non_negative_int":
        if not isinstance(value, int) or value < 0:
            raise ValueError(f"{env_key} must be a non-negative integer, got {value!r}")
    elif validation_kind == "positive_float":
        if not isinstance(value, (int, float)) or value <= 0:
            raise ValueError(f"{env_key} must be a positive number, got {value!r}")
    elif validation_kind == "non_negative_float":
        if not isinstance(value, (int, float)) or value < 0:
            raise ValueError(f"{env_key} must be a non-negative number, got {value!r}")
    elif validation_kind == "bool":
        pass


def get_ocr_mime_type(extension: str) -> str:
    """Return MIME type for OCR file extension.

    Args:
        extension: File extension (e.g., '.pdf', '.png', '.jpg', '.jpeg')

    Returns:
        MIME type string for the OCR API data URI.
    """
    mime_types = {
        ".pdf": "application/pdf",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
    }
    return mime_types.get(extension.lower(), "application/octet-stream")


def load_epfr_config() -> EpfrConfig:
    """Build an ``EpfrConfig`` from environment variables, falling back to ``EPFR_DEFAULTS``."""
    kwargs: dict[str, object] = {}

    for field_name, target_type, env_key, validation_kind in _ENV_FIELDS:
        raw = os.environ.get(env_key)
        if raw is not None:
            value = _parse_value(raw, target_type, env_key)
            _validate(value, validation_kind, env_key)
            kwargs[field_name] = value

    api_key = os.environ.get("MISTRAL_API_KEY")
    if api_key is not None:
        kwargs["mistral_api_key"] = api_key

    return replace(EPFR_DEFAULTS, **kwargs)


def get_dotenv_path() -> Path:
    """Return the absolute path to ``epfr-downloader/.env``."""
    return Path(__file__).resolve().parents[3] / ".env"


def require_mistral_api_key(cfg: EpfrConfig) -> str:
    """Return the Mistral API key or raise if it is absent."""
    if cfg.mistral_api_key:
        return cfg.mistral_api_key
    raise ValueError("MISTRAL_API_KEY is required but was not provided")


def resolve_workflow_input(
    max_pages: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    timeout: int | None = None,
    output_dir: str | None = None,
) -> ResolvedWorkflowInput:
    """Resolve EpfrWorkflowInput kwargs from env config when omitted."""
    cfg = load_epfr_config()
    resolved_date_to = date_to if date_to is not None else (cfg.default_date_to or date.today().isoformat())
    return {
        "max_pages": max_pages if max_pages is not None else cfg.max_pages,
        "date_from": date_from if date_from is not None else cfg.default_date_from,
        "date_to": resolved_date_to,
        "timeout": timeout if timeout is not None else cfg.default_timeout,
        "output_dir": output_dir if output_dir is not None else str(cfg.output_dir),
    }


def resolve_pdf_ocr_input(
    output_dir: str | None = None,
    mapping_filename: str | None = None,
    cleanup_source: bool | None = None,
) -> ResolvedPdfOcrInput:
    """Resolve EpfrPdfOcrInput kwargs from env config when omitted."""
    cfg = load_epfr_config()
    return {
        "output_dir": output_dir if output_dir is not None else str(cfg.output_dir),
        "mapping_filename": mapping_filename if mapping_filename is not None else cfg.mapping_filename,
        "cleanup_source": cleanup_source if cleanup_source is not None else cfg.cleanup_source,
    }


def resolve_ai_distiller_input(
    output_dir: str | None = None,
    mapping_filename: str | None = None,
    output_filename: str | None = None,
    model_name: str | None = None,
    temperature: float | None = None,
    max_retries: int | None = None,
    max_tokens: int | None = None,
    file_delay_seconds: float | None = None,
) -> ResolvedAiDistillerInput:
    """Resolve EpfrAiDistillerInput kwargs from env config when omitted."""
    cfg = load_epfr_config()
    return {
        "output_dir": output_dir if output_dir is not None else str(cfg.output_dir),
        "mapping_filename": mapping_filename if mapping_filename is not None else cfg.mapping_filename,
        "output_filename": output_filename if output_filename is not None else cfg.ai_distilled_filename,
        "model_name": model_name if model_name is not None else cfg.ai_model,
        "temperature": temperature if temperature is not None else cfg.ai_temperature,
        "max_retries": max_retries if max_retries is not None else cfg.ai_max_retries,
        "max_tokens": max_tokens if max_tokens is not None else cfg.ai_max_tokens,
        "file_delay_seconds": file_delay_seconds if file_delay_seconds is not None else float(cfg.ai_file_delay),
    }


def resolve_share_payout_export_input(
    output_dir: str | None = None,
    input_filename: str | None = None,
    output_filename: str | None = None,
) -> ResolvedSharePayoutExportInput:
    """Resolve EpfrSharePayoutExportInput kwargs from env config when omitted."""
    cfg = load_epfr_config()
    return {
        "output_dir": output_dir if output_dir is not None else str(cfg.output_dir),
        "input_filename": input_filename if input_filename is not None else cfg.ai_distilled_filename,
        "output_filename": output_filename if output_filename is not None else cfg.share_payout_export_filename,
    }


BASE_API_URL: str = EPFR_DEFAULTS.base_api_url
FILE_DOWNLOAD_URL_TEMPLATE: str = EPFR_DEFAULTS.file_download_url_template
DEFAULT_SEARCH_QUERY: str = EPFR_DEFAULTS.default_search_query
DEFAULT_SUB_CATEGORY_ID: int = EPFR_DEFAULTS.default_sub_category_id
DEFAULT_SORT_FIELD: str = EPFR_DEFAULTS.default_sort_field
DEFAULT_SORT_DIR: str = EPFR_DEFAULTS.default_sort_dir
DEFAULT_MAX_PAGES: int = EPFR_DEFAULTS.max_pages
DEFAULT_TIMEOUT: int = EPFR_DEFAULTS.default_timeout
MAX_CONCURRENT_DOWNLOADS: int = EPFR_DEFAULTS.max_concurrent_downloads
DOWNLOAD_TIMEOUT: int = EPFR_DEFAULTS.download_timeout
DOWNLOAD_RETRIES: int = EPFR_DEFAULTS.download_retries
CHUNK_SIZE: int = EPFR_DEFAULTS.chunk_size
MAX_RETRIES: int = EPFR_DEFAULTS.max_retries
RETRY_BACKOFF_BASE: int = EPFR_DEFAULTS.retry_backoff_base
RETRY_BACKOFF_MAX: int = EPFR_DEFAULTS.retry_backoff_max
MAX_CONCURRENT_OCR: int = EPFR_DEFAULTS.max_concurrent_ocr
MAX_PDF_SIZE_BYTES: int = EPFR_DEFAULTS.max_pdf_size_bytes
OCR_MODEL: str = EPFR_DEFAULTS.ocr_model
OCR_SUPPORTED_EXTENSIONS: set[str] = EPFR_DEFAULTS.ocr_supported_extensions
AI_MODEL: str = EPFR_DEFAULTS.ai_model
AI_TEMPERATURE: float = EPFR_DEFAULTS.ai_temperature
AI_TIMEOUT: int = EPFR_DEFAULTS.ai_timeout
AI_MAX_RETRIES: int = EPFR_DEFAULTS.ai_max_retries
AI_MAX_TOKENS: int = EPFR_DEFAULTS.ai_max_tokens
AI_RETRY_BACKOFF_BASE: int = EPFR_DEFAULTS.ai_retry_backoff_base
AI_RETRY_BACKOFF_MAX: int = EPFR_DEFAULTS.ai_retry_backoff_max
AI_RETRY_BACKOFF_MAX_429: int = EPFR_DEFAULTS.ai_retry_backoff_max_429
AI_RETRY_JITTER_RATIO: float = EPFR_DEFAULTS.ai_retry_jitter_ratio
AI_FILE_DELAY: int = EPFR_DEFAULTS.ai_file_delay
DEFAULT_OUTPUT_DIR: Path = EPFR_DEFAULTS.output_dir
MAPPING_FILENAME: str = EPFR_DEFAULTS.mapping_filename
AI_DISTILLED_FILENAME: str = EPFR_DEFAULTS.ai_distilled_filename
FIRST_PAGE_NO: int = EPFR_DEFAULTS.first_page_no
PAGE_DELAY: float = EPFR_DEFAULTS.page_delay
DEFAULT_DATE_TO: str = EPFR_DEFAULTS.default_date_to
SHARE_PAYOUT_EXPORT_WORKFLOW_NAME = "epfr-share-payout-exporter"
SHARE_PAYOUT_EXPORT_FILENAME: str = EPFR_DEFAULTS.share_payout_export_filename
SHARE_DIVIDENDS_SQL_FILENAME: str = EPFR_DEFAULTS.share_dividends_sql_filename
SERVER_URL: str = EPFR_DEFAULTS.server_url
DEPLOYMENT_NAME: str = EPFR_DEFAULTS.deployment_name


def get_shares_source_data_csv() -> Path:
    """Return the absolute path to the shares source data CSV file.

    Returns:
        Path: Absolute path to ``shares_source_data.csv`` at the repository root,
            resolved by navigating 4 parent directories up from this module.
    """
    return Path(__file__).resolve().parents[4] / "shares_source_data.csv"

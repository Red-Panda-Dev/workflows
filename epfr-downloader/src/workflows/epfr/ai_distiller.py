"""AI dividend distillation workflow for EPFR markdown files.

This module implements the third stage of the EPFR pipeline: it takes markdown files
produced by the download and OCR stages, and uses Mistral AI (chat.parse with structured
output) to extract normalized dividend payout data.

The workflow reads the UNP file mapping JSON, processes each markdown file sequentially,
extracts dividend information (share type, period, amounts, dates), normalizes and validates
the data, and produces ai_distilled_dividends.json as output. This structured output feeds
the fourth stage (share payout exporter) which joins dividends with share reference data.

Key business rules enforced:
    - Dividend dates must be ordered: decision_date >= record_date, payment_date > decision_date
    - Period numbers validated against period types (annual=1, halfyear=1-2, quarterly=1-4)
    - Share types normalized to "common" or "preferred"
    - Amounts converted to Decimal for 8-decimal precision
    - Missing fields auto-filled with heuristics and tracked in autofilled_fields list

Error handling:
    - Transient AI errors (503, 502, 429, timeout) retried with exponential backoff
    - Rate limit (429) uses separate higher backoff cap (ai_retry_backoff_max_429)
    - Non-retryable errors fail immediately; file-level failures recorded but don't stop workflow
"""

import asyncio
from datetime import UTC, date, datetime
from decimal import Decimal
import json
import logging
import os
from pathlib import Path
import random
import tempfile
from typing import Any

from mistralai.extra import response_format_from_pydantic_model
from mistralai.workflows.plugins.mistralai import ChatCompletionRequest, ResponseFormat, mistralai_chat_parse
from pydantic import BaseModel, Field

from .config import load_epfr_config
from .models import (
    EpfrAiDistilledCompany,
    EpfrAiDistilledFile,
    EpfrAiDistillerInput,
    EpfrDividendEntry,
    EpfrDividendExtraction,
    PeriodType,
    ShareType,
)


logger = logging.getLogger(__name__)

_PROMPT_TEMPLATE: str | None = None


class _RawDividendEntry(BaseModel):
    """Internal model for raw AI extraction output before normalization.

    Represents a single dividend entry as returned by Mistral AI chat.parse, before
    type conversion, validation, and auto-filling. All fields are optional because
    the AI may not extract every field from every document.

    This is an internal-only model used during extraction. The normalized equivalent
    is EpfrDividendEntry which enforces business constraints.

    Attributes:
        share_type: Share classification as extracted by AI (e.g., "common", "preferred", or raw text).
        period_year: The year of the dividend period.
        period_type: The type of period (e.g., "annual", "halfyear", "quarterly").
        period_number: The number within the period type (1 for annual, 1-2 for halfyear, 1-4 for quarterly).
        amount_per_share: Dividend amount per share, may be float, int, or string representation.
        decision_date: Date when dividend was decided, in ISO 8601 format (YYYY-MM-DD).
        record_date: Date of record for dividend eligibility, in ISO 8601 format.
        payment_date: Date when dividend is paid, in ISO 8601 format.
    """

    share_type: str | None = None
    period_year: int | None = None
    period_type: str | None = None
    period_number: int | None = None
    amount_per_share: float | int | str | None = None
    decision_date: str | None = None
    record_date: str | None = None
    payment_date: str | None = None


class _RawExtraction(BaseModel):
    """Container for AI extraction results for a single markdown file.

    Wraps the structured output from Mistral AI chat.parse for one document.
    This is the top-level model returned by the AI for each file processed.

    Attributes:
        has_dividends: Whether the AI found any dividend information in the document.
        ai_comment: AI-generated commentary or explanation about the extraction result.
            May contain reasons why no dividends were found, or notes about the data.
        dividends: List of raw dividend entries extracted from the document. Empty
            if has_dividends is False.
    """

    has_dividends: bool = False
    ai_comment: str = ""
    dividends: list[_RawDividendEntry] = Field(default_factory=list)


class AIDistiller:
    """Reusable Mistral chat.parse client for EPFR dividend extraction.

    Central client class that manages all AI extraction operations for the EPFR pipeline.
    It wraps the Mistral AI chat API with structured output parsing (chat.parse) to extract
    dividend data from markdown documents.

    The class loads the prompt template once on initialization, constructs the system
    instruction with a reference date, and provides methods for single extraction calls
    and retry-enabled extraction for production robustness.

    Attributes:
        system_instruction: The full system prompt sent to Mistral, including the
            loaded template with reference date substituted.
        model_name: Identifier of the Mistral model to use (e.g., "ministral-8b-latest").
        temperature: Model temperature for controlling randomness (0.0 for deterministic).
        max_tokens: Maximum tokens to generate in the response.
    """

    def __init__(self, model_name: str, temperature: float, reference_date: str, max_tokens: int = 4000) -> None:
        """Initialize AI distiller with model config and reference date.

        Args:
            model_name: Mistral model identifier (e.g., "ministral-8b-latest", "mistral-large-latest").
            temperature: Model temperature for controlling output randomness. Use 0.0 for
                deterministic extractions (recommended for structured data extraction).
            reference_date: ISO 8601 date string (YYYY-MM-DD) used as the reference
                point in the system prompt. Typically the current date when the distiller
                is created.
            max_tokens: Maximum tokens allowed in the model response.

        Side effects:
            - Loads the prompt template from prompts/dividends_parsing.md
            - Substitutes {{REFERENCE_DATE}} placeholder in the template
            - Logs initialization details for audit trail
        """
        logger.info(
            f"Initializing AIDistiller: model={model_name}, temperature={temperature}, reference_date={reference_date}"
        )

        prompt_template = _load_prompt_template()
        self.system_instruction = prompt_template.replace("{{REFERENCE_DATE}}", reference_date)
        self.model_name = model_name
        self.temperature = temperature
        self.max_tokens = max_tokens
        logger.info("AIDistiller ready: prompt_template_loaded=True")

    async def extract(self, markdown_text: str) -> _RawExtraction:
        """Send markdown text to Mistral AI for structured dividend extraction.

        Performs a single AI call to extract dividend information from a markdown document.
        Uses chat.parse with structured output to get a strongly-typed _RawExtraction result.

        Args:
            markdown_text: The full content of the markdown file to extract dividends from.

        Returns:
            _RawExtraction: Parsed extraction result containing has_dividends flag,
                AI comment, and list of raw dividend entries.

        Raises:
            ValueError: If the Mistral API returns an empty or invalid response (no
                choices, no message, or no content in the parsed response).
        """
        logger.debug(f"Sending extraction request: text_length={len(markdown_text)} chars")
        request = ChatCompletionRequest(
            model=self.model_name,
            messages=[
                {"role": "system", "content": self.system_instruction},
                {"role": "user", "content": markdown_text},
            ],
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )
        response_format_dict = response_format_from_pydantic_model(_RawExtraction)
        response_format_obj = ResponseFormat(**response_format_dict)
        response = await mistralai_chat_parse(request, response_format_obj)
        if not response.choices or not response.choices[0].message or not response.choices[0].message.content:
            raise ValueError("No parsed response from Mistral")
        return _RawExtraction.model_validate_json(str(response.choices[0].message.content))

    async def extract_with_retry(self, markdown_text: str, max_retries: int, file_path: Path) -> _RawExtraction:
        """Extract with exponential backoff retry for transient AI failures.

        Wraps the extract method with retry logic for handling transient API errors.
        Implements capped exponential backoff with jitter, with special handling for
        rate limits (429).

        Args:
            markdown_text: The full content of the markdown file to extract dividends from.
            max_retries: Maximum number of retry attempts (including the initial call).
            file_path: Path to the file being processed, used for logging context only.

        Returns:
            _RawExtraction: Parsed extraction result after successful AI call.

        Raises:
            RuntimeError: If all retry attempts are exhausted. The exception message
                includes the file path and the final error details.
            asyncio.CancelledError: If the operation is cancelled during retry.

        Business logic:
            - Retries on 503, 502, 429, timeout, overload, and CancelledError
            - Rate limit (429) triggers use ai_retry_backoff_max_429 cap (90s default)
            - Other errors use ai_retry_backoff_max cap (30s default)
            - Jitter is applied to prevent thundering herd problems
            - Wait times are capped at configured maximums from EPFR config
        """
        logger.info(f"Starting extraction with retries: file={file_path.name}, max_retries={max_retries}")
        for attempt in range(max_retries):
            try:
                logger.info(f"AI extraction attempt {attempt + 1}/{max_retries} for {file_path.name}")
                result = await self.extract(markdown_text)
                logger.info(f"Extraction succeeded on attempt {attempt + 1} for {file_path.name}")
                return result
            except (Exception, asyncio.CancelledError) as exc:
                error_str = str(exc).lower() if isinstance(exc, Exception) else "cancellederror"
                is_retryable = any(
                    marker in error_str
                    for marker in (
                        "503",
                        "502",
                        "429",
                        "timeout",
                        "overload",
                        "cancelled",
                        "invalid json",
                        "eof while parsing",
                    )
                )
                if is_retryable and attempt < max_retries - 1:
                    is_rate_limited = "429" in error_str or "rate limit" in error_str or "rate_limited" in error_str
                    wait = _compute_retry_wait_seconds(attempt, is_rate_limited=is_rate_limited)
                    logger.warning(
                        f"Retryable error on attempt {attempt + 1}/{max_retries} for {file_path.name}: {type(exc).__name__}: {exc}. Retrying in {wait}s"
                    )
                    await asyncio.sleep(wait)
                    continue
                logger.error(
                    f"Extraction failed after {attempt + 1} attempts for {file_path.name}: {type(exc).__name__}: {exc}"
                )
                raise RuntimeError(f"AI extraction failed for {file_path}: {type(exc).__name__}: {exc}") from exc

        raise RuntimeError(f"Unexpected retry loop completion for {file_path}")


def _compute_retry_wait_seconds(attempt: int, *, is_rate_limited: bool) -> float:
    """Compute capped exponential backoff with jitter for AI retries.

    Calculates the wait time before the next retry attempt using exponential backoff
    with random jitter. The wait time is capped at a configured maximum to prevent
    excessively long delays.

    Args:
        attempt: Zero-based attempt number (0 for first retry, 1 for second, etc.).
        is_rate_limited: If True, the error was a 429 rate limit, which uses a
            higher maximum backoff cap (ai_retry_backoff_max_429).

    Returns:
        float: Wait time in seconds, rounded to 2 decimal places. Minimum is 0.1s.

    Business logic:
        - Base wait = (ai_retry_backoff_base ^ (attempt + 1))
        - Capped at ai_retry_backoff_max (30s default) or ai_retry_backoff_max_429 (90s for rate limits)
        - Jitter = random value in [-jitter_span, +jitter_span] where jitter_span = base_wait * ai_retry_jitter_ratio
        - Final wait = base_wait + jitter, clamped to minimum 0.1s
        - Jitter prevents synchronized retry storms from multiple workers
    """
    cfg = load_epfr_config()
    capped_max = cfg.ai_retry_backoff_max_429 if is_rate_limited else cfg.ai_retry_backoff_max
    base_wait = min(float(cfg.ai_retry_backoff_base ** (attempt + 1)), float(capped_max))
    jitter_span = base_wait * cfg.ai_retry_jitter_ratio
    jitter = random.uniform(-jitter_span, jitter_span)
    wait = base_wait + jitter
    return max(0.1, round(wait, 2))


def _load_prompt_template() -> str:
    """Load AI prompt template from filesystem with caching.

    Loads the dividend parsing prompt template from the prompts directory.
    The template is cached globally in the _PROMPT_TEMPLATE module variable to
    avoid repeated file I/O across multiple extraction calls.

    Returns:
        str: The prompt template content as a string.

    Raises:
        FileNotFoundError: If the template file is not found at the expected path
            (prompts/dividends_parsing.md relative to this module).

    Business logic:
        - Template path: <module_dir>/prompts/dividends_parsing.md
        - Template contains {{REFERENCE_DATE}} placeholder that must be substituted
          before use (done in AIDistiller.__init__)
        - Caching ensures the template is loaded only once per process lifetime
    """
    global _PROMPT_TEMPLATE
    if _PROMPT_TEMPLATE is None:
        prompt_path = Path(__file__).parent / "prompts" / "dividends_parsing.md"
        if not prompt_path.exists():
            raise FileNotFoundError(f"Prompt template not found at {prompt_path}")
        _PROMPT_TEMPLATE = prompt_path.read_text(encoding="utf-8")
    return _PROMPT_TEMPLATE


def _parse_iso_date(value: str | None) -> date | None:
    """Parse an exact date-only ISO value for legacy callers."""
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d").date()


def _normalize_iso_date(value: str | None, field_name: str) -> tuple[date | None, str | None]:
    """Return a document-derived date or an extraction warning.

    A date-only ISO value is preferred. ISO datetimes and one trailing punctuation
    mark are accepted because providers commonly serialize otherwise valid dates
    that way. Any prose-contaminated value is discarded instead of being guessed.

    Args:
        value: Raw date value emitted by the extraction model.
        field_name: Output field used in a warning identifier.

    Returns:
        A normalized date and an optional warning identifier.
    """
    if value is None or not value.strip():
        return None, None

    candidate = value.strip()
    if candidate[-1:] in {",", ".", ";"}:
        candidate = candidate[:-1].rstrip()
    try:
        if "T" in candidate:
            return datetime.fromisoformat(candidate.replace("Z", "+00:00")).date(), None
        return date.fromisoformat(candidate), None
    except ValueError:
        return None, f"invalid_{field_name}"


def _shift_months(value: date, months: int) -> date:
    """Shift a date by months while clamping the day to the target month."""
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    if month == 2:
        day_max = 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28
    elif month in {4, 6, 9, 11}:
        day_max = 30
    else:
        day_max = 31
    return date(year, month, min(value.day, day_max))


def _safe_replace_year(value: date, new_year: int) -> date:
    """Replace a date year while safely handling February 29."""
    try:
        return value.replace(year=new_year)
    except ValueError:
        return date(new_year, value.month, 28)


def _validate_and_correct_dates(
    period_type: str,
    period_year: int,
    decision_date: date,
    record_date: date,
    payment_date: date,
    autofilled: list[str],
) -> tuple[int, date, date, date]:
    """Return legacy date arguments unchanged for compatibility.

    Production normalization validates date relationships without rewriting
    document facts. This helper remains available to callers of the old helper
    API but no longer applies heuristic corrections.
    """
    del period_type, autofilled
    return period_year, decision_date, record_date, payment_date


def _normalize_share_type(value: str | None) -> ShareType | None:
    """Map explicit English and Belarusian share labels to the output enum."""
    if value is None:
        return None
    normalized = value.casefold().strip()
    if normalized in {"common", "ordinary", "common shares", "ordinary shares"} or any(
        label in normalized for label in ("обыкнов", "обычные", "простые")
    ):
        return "common"
    if normalized in {"preferred", "preferred shares"} or "привилег" in normalized:
        return "preferred"
    return None


def _normalize_period_type(value: str | None) -> PeriodType | None:
    """Map explicit period labels to the supported period enum without inferring a period."""
    if value is None:
        return None
    normalized = value.casefold().strip()
    if normalized in {"annual", "yearly", "год", "годовой"}:
        return "annual"
    if normalized in {"halfyear", "half-year", "semiannual", "полугодие"}:
        return "halfyear"
    if normalized in {"quarterly", "quarter", "квартал"}:
        return "quarterly"
    return None


def normalize_and_fill_dividend(raw: _RawDividendEntry, upload_date: str) -> tuple[EpfrDividendEntry, list[str]]:
    """Normalize one model entry and calculate missing payout dates.

    Explicit document dates are preserved. When a date is absent or invalid, the
    export contract uses deterministic fallbacks: the decision date is one month
    before the file upload date, the record date is one month before the decision,
    and the payment date is one day (zero amount) or two months (positive amount)
    after the decision.

    Args:
        raw: Raw dividend entry returned by the model.
        upload_date: Source file upload date used to derive a missing decision date.

    Returns:
        The validated dividend and warnings for discarded or calculated dates.

    Raises:
        ValueError: If a required non-date dividend fact is absent or unsupported.
    """
    warnings: list[str] = []

    share_type = _normalize_share_type(raw.share_type)
    if share_type is None and raw.share_type is None:
        share_type = "common"
        warnings.append("share_type_defaulted")
    elif share_type is None:
        raise ValueError("share_type must be common or preferred")

    period_type = _normalize_period_type(raw.period_type)
    if period_type is None and raw.period_type is None:
        period_type = "annual"
        warnings.append("period_type_defaulted")
    elif period_type is None:
        raise ValueError("period_type must be annual, halfyear, or quarterly")

    period_number = raw.period_number
    if period_number is None:
        period_number = 1
        warnings.append("period_number_defaulted")
    if raw.amount_per_share is None:
        raise ValueError("amount_per_share is required")
    amount_per_share = Decimal(str(raw.amount_per_share))

    decision_date, warning = _normalize_iso_date(raw.decision_date, "decision_date")
    if warning:
        warnings.append(warning)
    record_date, warning = _normalize_iso_date(raw.record_date, "record_date")
    if warning:
        warnings.append(warning)
    payment_date, warning = _normalize_iso_date(raw.payment_date, "payment_date")
    if warning:
        warnings.append(warning)

    if decision_date is not None and record_date is not None and record_date > decision_date:
        record_date = None
        warnings.append("record_date_after_decision_date")
    if decision_date is not None and payment_date is not None and payment_date <= decision_date:
        payment_date = None
        warnings.append("payment_date_not_after_decision_date")

    if decision_date is None:
        upload_reference, _ = _normalize_iso_date(upload_date, "upload_date")
        decision_date = _shift_months(upload_reference or datetime.now(UTC).date(), -1)
        warnings.append("decision_date_defaulted")
    if record_date is None:
        record_date = _shift_months(decision_date, -1)
        warnings.append("record_date_defaulted")
    if payment_date is None:
        if amount_per_share == 0:
            payment_date = decision_date.fromordinal(decision_date.toordinal() + 1)
        else:
            payment_date = _shift_months(decision_date, 2)
        warnings.append("payment_date_defaulted")

    period_year = raw.period_year
    if period_year is None:
        period_year = decision_date.year - 1 if period_type == "annual" else decision_date.year
        warnings.append("period_year_defaulted")

    dividend = EpfrDividendEntry(
        share_type=share_type,
        period_year=period_year,
        period_type=period_type,
        period_number=period_number,
        amount_per_share=amount_per_share,
        decision_date=decision_date,
        record_date=record_date,
        payment_date=payment_date,
    )
    return dividend, warnings


def _deduplicate_dividends(dividends: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """Remove duplicate facts and retain the final deadline for repeated payouts.

    The prompt defines the latest applicable deadline as the stored payment date.
    Repeated entries with identical share, period, and amount therefore collapse
    to the entry with the latest explicit payment date.
    """
    exact_unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    warnings: list[str] = []

    for dividend in dividends:
        fingerprint = json.dumps(dividend, ensure_ascii=False, sort_keys=True)
        if fingerprint in seen:
            if "duplicate_dividend_entries_removed" not in warnings:
                warnings.append("duplicate_dividend_entries_removed")
            continue
        seen.add(fingerprint)
        exact_unique.append(dividend)

    grouped: dict[tuple[str, int, str, int, str], list[dict[str, Any]]] = {}
    for dividend in exact_unique:
        identity = (
            str(dividend["share_type"]),
            int(dividend["period_year"]),
            str(dividend["period_type"]),
            int(dividend["period_number"]),
            str(dividend["amount_per_share"]),
        )
        grouped.setdefault(identity, []).append(dividend)

    unique: list[dict[str, Any]] = []
    for candidates in grouped.values():
        if len(candidates) == 1:
            unique.append(candidates[0])
            continue
        if "conflicting_dividend_entries_resolved" not in warnings:
            warnings.append("conflicting_dividend_entries_resolved")
        unique.append(max(candidates, key=lambda entry: (entry["payment_date"] or "", entry["decision_date"] or "")))

    return unique, warnings


async def run_ai_distillation(input: EpfrAiDistillerInput) -> dict[str, Any]:
    """Main entry point for AI distillation workflow.

    Orchestrates the complete AI distillation pipeline: reads the UNP file mapping,
    processes each markdown file sequentially with AI extraction, normalizes the
    results, and writes the output JSON atomically.

    This is the primary function called by the epfr-ai-distiller workflow.

    Args:
        input: Configuration for the AI distillation run. Must have all fields resolved
            (not None) before calling. Contains output_dir, mapping_filename, output_filename,
            model_name, temperature, max_retries, file_delay_seconds, and optional unps filter.

    Returns:
        dict: Statistics dictionary with keys:
            - output_path: Absolute path to the output JSON file
            - total_companies: Number of companies processed
            - total_files: Total number of markdown files processed
            - successful: Count of files successfully processed
            - failed: Count of files that failed processing
            - failed_files: List of file paths that failed

    Raises:
        FileNotFoundError: If the mapping file does not exist at the expected path.
        AssertionError: If any required input field is None (not pre-resolved).

    Business logic:
        - Reads mapping JSON from output_dir/mapping_filename to get company/file list
        - Supports optional UNP filtering via input.unps (processes only specified UNPs)
        - Creates AIDistiller instance with configured model and temperature
        - Processes companies sequentially (not in parallel) to avoid rate limiting
        - For each company, processes each markdown file:
          - Loads file content from output_dir/<unp>/<filename>
          - Calls AI extraction with retry (extract_with_retry)
          - Normalizes each dividend entry (normalize_and_fill_dividend)
          - Tracks autofilled fields per file
          - Records errors but continues with remaining files
        - Respects file_delay_seconds between file processing to avoid rate limits
        - Writes output atomically using tempfile.mkstemp() + os.replace() pattern
        - Logs progress at company and file level for monitoring
    """
    assert input.output_dir is not None, "output_dir must be resolved before calling run_ai_distillation"
    assert input.mapping_filename is not None, "mapping_filename must be resolved"
    assert input.output_filename is not None, "output_filename must be resolved"
    assert input.model_name is not None, "model_name must be resolved"
    assert input.temperature is not None, "temperature must be resolved"
    assert input.max_retries is not None, "max_retries must be resolved"
    assert input.file_delay_seconds is not None, "file_delay_seconds must be resolved"
    output_root = Path(input.output_dir)
    mapping_path = output_root / input.mapping_filename
    output_path = output_root / input.output_filename

    logger.info(
        f"Starting AI distillation: output_dir={input.output_dir}, mapping={input.mapping_filename}, output={input.output_filename}"
    )

    if not mapping_path.exists():
        raise FileNotFoundError(f"Mapping file not found: {mapping_path}")

    mapping: dict[str, Any] = json.loads(mapping_path.read_text(encoding="utf-8"))
    selected_unps = set(input.unps) if input.unps else None
    reference_date = datetime.now(UTC).date().isoformat()

    total_unps = len(mapping)
    logger.info(f"Loaded mapping: {total_unps} companies in mapping file")
    if selected_unps is not None:
        logger.info(f"Filtering to {len(selected_unps)} selected UNPs: {sorted(selected_unps)}")
    else:
        logger.info("No UNP filter — processing all companies")

    distiller = AIDistiller(input.model_name, input.temperature, reference_date)

    result: dict[str, dict[str, Any]] = {}
    total_files = 0
    successful = 0
    failed = 0
    failed_files: list[str] = []
    company_index = 0

    for unp, company_data_any in mapping.items():
        if selected_unps is not None and unp not in selected_unps:
            logger.debug(f"Skipping UNP {unp} — not in selected filter")
            continue

        company_index += 1
        company_data = company_data_any if isinstance(company_data_any, dict) else {}
        files_any = company_data.get("files", [])
        files = files_any if isinstance(files_any, list) else []
        company_title = str(company_data.get("title", ""))

        logger.info(
            f"[{company_index}] Processing company: unp={unp}, title={company_title!r}, files_in_mapping={len(files)}"
        )

        company = EpfrAiDistilledCompany(
            company_name=company_title,
            unp=unp,
            holder_id=int(company_data.get("holder_id", 0)),
            files=[],
        )

        file_index = 0
        for entry_any in files:
            if not isinstance(entry_any, dict):
                logger.debug(f"Skipping non-dict file entry for UNP {unp}")
                continue
            filename = str(entry_any.get("filename", ""))
            if not filename.lower().endswith(".md"):
                logger.debug(f"Skipping non-markdown file: {filename}")
                continue

            file_index += 1
            total_files += 1
            md_path = output_root / unp / filename
            distilled_file = EpfrAiDistilledFile(
                id=int(entry_any.get("id", 0)),
                file_path=str(md_path),
                filename=filename,
                original_name=str(entry_any.get("original_name", "")),
                upload_date=str(entry_any.get("upload_date", "")),
                extracted_from=(str(entry_any.get("extracted_from")) if entry_any.get("extracted_from") else None),
                converted_from=(str(entry_any.get("converted_from")) if entry_any.get("converted_from") else None),
            )

            logger.info(
                f"  [{company_index}.{file_index}] Processing file: {filename} (original: {distilled_file.original_name!r}, upload_date: {distilled_file.upload_date})"
            )

            try:
                if not md_path.exists():
                    raise FileNotFoundError(f"Markdown file not found: {md_path}")
                md_content = md_path.read_text(encoding="utf-8")
                if not md_content.strip():
                    raise ValueError("Markdown file is empty")

                logger.debug(
                    f"  [{company_index}.{file_index}] Read {len(md_content)} chars from {filename}, sending to AI"
                )

                raw_extraction = await distiller.extract_with_retry(md_content, input.max_retries, md_path)
                dividends: list[dict[str, Any]] = []
                warnings: list[str] = []
                for raw_div in raw_extraction.dividends:
                    try:
                        normalized, entry_warnings = normalize_and_fill_dividend(raw_div, distilled_file.upload_date)
                    except ValueError as exc:
                        warnings.append(f"invalid_dividend_entry: {exc}")
                        continue
                    dividends.append(normalized.model_dump(mode="json"))
                    warnings.extend(entry_warnings)

                dividends, deduplication_warnings = _deduplicate_dividends(dividends)
                warnings.extend(deduplication_warnings)
                extraction = EpfrDividendExtraction(
                    has_dividends=any(Decimal(dividend["amount_per_share"]) > 0 for dividend in dividends),
                    ai_comment=raw_extraction.ai_comment,
                    dividends=[EpfrDividendEntry.model_validate(dividend) for dividend in dividends],
                )
                if extraction.has_dividends != raw_extraction.has_dividends:
                    warnings.append("has_dividends_reconciled_from_amounts")

                distilled_file.has_dividends = extraction.has_dividends
                distilled_file.ai_comment = extraction.ai_comment
                distilled_file.dividends = extraction.dividends
                distilled_file.autofilled_fields = []
                distilled_file.warnings = sorted(set(warnings))
                successful += 1
                logger.info(
                    f"  [{company_index}.{file_index}] File processed successfully: {filename} (has_dividends={extraction.has_dividends})"
                )
            except Exception as exc:
                failed += 1
                distilled_file.error = f"{type(exc).__name__}: {exc}"
                failed_files.append(str(md_path))
                logger.error(
                    f"  [{company_index}.{file_index}] FAILED processing {filename}: {type(exc).__name__}: {exc}"
                )

            company.files.append(distilled_file)
            if input.file_delay_seconds > 0:
                logger.debug(f"  [{company_index}.{file_index}] Sleeping {input.file_delay_seconds}s before next file")
            await asyncio.sleep(input.file_delay_seconds)

        if company.files:
            result[unp] = company.model_dump(mode="json")
            logger.info(
                f"[{company_index}] Company {unp} ({company_title!r}): {len(company.files)} files processed, added to results"
            )
        else:
            logger.info(f"[{company_index}] Company {unp} ({company_title!r}): no markdown files found, skipping")

    logger.info(f"Writing output: {output_path} ({len(result)} companies, {total_files} files)")

    fd, tmp_path = tempfile.mkstemp(dir=str(output_path.parent), prefix=".ai_distilled_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, str(output_path))
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise

    stats = {
        "output_path": str(output_path.resolve()),
        "total_companies": len(result),
        "total_files": total_files,
        "successful": successful,
        "failed": failed,
        "failed_files": failed_files,
    }
    logger.info(
        f"AI distillation complete: {successful}/{total_files} files successful, {failed} failed, {len(result)} companies written to {output_path.name}"
    )
    if failed_files:
        logger.warning(f"Failed files: {failed_files}")
    return stats

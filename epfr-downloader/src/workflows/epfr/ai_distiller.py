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
import json
import logging
import os
import random
import tempfile
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

from mistralai.workflows.plugins.mistralai import ChatCompletionRequest, ResponseFormat, mistralai_chat_parse
from mistralai.extra import response_format_from_pydantic_model
from pydantic import BaseModel, Field, ValidationError

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

    def __init__(self, model_name: str, temperature: float, reference_date: str) -> None:
        """Initialize AI distiller with model config and reference date.

        Args:
            model_name: Mistral model identifier (e.g., "ministral-8b-latest", "mistral-large-latest").
            temperature: Model temperature for controlling output randomness. Use 0.0 for
                deterministic extractions (recommended for structured data extraction).
            reference_date: ISO 8601 date string (YYYY-MM-DD) used as the reference
                point in the system prompt. Typically the current date when the distiller
                is created.

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
        self.max_tokens = 4000
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
                is_retryable = any(s in error_str for s in ("503", "502", "429", "timeout", "overload", "cancelled"))
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
    """Parse ISO 8601 date string to date object.

    Converts a date string in YYYY-MM-DD format to a Python date object.
    Handles None input gracefully by returning None.

    Args:
        value: Date string in ISO 8601 format (YYYY-MM-DD), or None.

    Returns:
        date: Parsed date object, or None if input was None or empty.

    Raises:
        ValueError: If the string cannot be parsed as a valid ISO 8601 date.

    Business logic:
        - AI returns dates as strings in YYYY-MM-DD format
        - Empty strings and None are both treated as missing dates
        - This is the entry point for all date parsing from raw AI extraction
    """
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d").date()


def _shift_months(value: date, months: int) -> date:
    """Shift a date by specified number of months, handling month boundaries.

    Adds or subtracts months from a date while properly handling month length
    differences. For example, shifting Jan 31 by 1 month returns Feb 28/29
    (not March 31).

    Args:
        value: The base date to shift.
        months: Number of months to add (can be negative to subtract months).

    Returns:
        date: New date with months added, clamped to valid day for the target month.

    Business logic:
        - Handles leap years: Feb 29 in a leap year becomes Feb 28 in non-leap years
        - Respects month lengths: Jan 31 + 1 month = Feb 28/29, not March 3
        - Used for auto-filling missing dates: record_date defaults to decision_date - 1 month
        - payment_date defaults to decision_date + 2 months (for non-zero amounts)
    """
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    if month == 2:
        leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
        day_max = 29 if leap else 28
    elif month in {4, 6, 9, 11}:
        day_max = 30
    else:
        day_max = 31
    return date(year, month, min(value.day, day_max))


def _safe_replace_year(value: date, new_year: int) -> date:
    """Replace year in date safely, handling leap-day rollover.

    Changes the year of a date while preserving month and day. If the original
    date is Feb 29 and the new year is not a leap year, falls back to Feb 28.

    Args:
        value: The original date whose year will be replaced.
        new_year: The new year to set.

    Returns:
        date: New date with the year replaced. If the original was Feb 29 and
            new_year is not a leap year, returns Feb 28 of new_year.

    Business logic:
        - Leap day handling: Feb 29 in a leap year becomes Feb 28 in non-leap years
        - Used during annual period year correction when the period_year doesn't
          match the decision_date year (e.g., 2025 dividends decided in late 2024)
    """
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
    """Apply post-extraction date sanity checks and corrections.

    Validates and corrects dividend dates to ensure they follow business rules.
    Mutates the autofilled list to track which corrections were applied.

    Args:
        period_type: The dividend period type ("annual", "halfyear", "quarterly").
        period_year: The dividend period year.
        decision_date: Date when dividend was decided.
        record_date: Date of record for dividend eligibility.
        payment_date: Date when dividend is paid.
        autofilled: List of strings tracking which fields were auto-filled or corrected.
            This list is mutated in-place to add correction flags.

    Returns:
        tuple: Corrected values as (period_year, decision_date, record_date, payment_date).

    Side effects:
        - Appends correction flags to the autofilled list (e.g., "record_date_corrected")

    Business logic:
        - Date gap check: If decision_date is >6 months before record_date, shifts
          record_date back by 1 month (common data entry error pattern)
        - Payment date gap: If payment_date is >1 year after decision_date, shifts
          payment_date forward by 2 months
        - Annual period year correction: If period_type is "annual" and period_year
          equals decision_date year, checks if dates belong to next year. If so,
          either decrements period_year or shifts all dates to next year.
        - Final ordering guard: Ensures decision_date >= record_date and
          payment_date > decision_date, correcting if necessary.
    """
    if (decision_date.toordinal() - record_date.toordinal()) > 183:
        record_date = _shift_months(decision_date, -1)
        autofilled.append("record_date_corrected")

    if (payment_date.toordinal() - decision_date.toordinal()) > 365:
        payment_date = _shift_months(decision_date, 2)
        autofilled.append("payment_date_corrected")

    if period_type == "annual" and period_year == decision_date.year:
        current_year = datetime.now(UTC).year
        corrected_dates_year = decision_date.year + 1

        if corrected_dates_year > current_year:
            period_year -= 1
            autofilled.append("period_year_corrected")
        else:
            decision_date = _safe_replace_year(decision_date, corrected_dates_year)
            record_date = _safe_replace_year(record_date, corrected_dates_year)
            payment_date = _safe_replace_year(payment_date, corrected_dates_year)
            autofilled.append("dates_year_corrected")

    # Final ordering guard: ensure decision_date >= record_date and payment_date > decision_date.
    if decision_date < record_date:
        record_date = decision_date
        autofilled.append("record_date_corrected")
    if payment_date <= decision_date:
        payment_date = date.fromordinal(decision_date.toordinal() + 1)
        autofilled.append("payment_date_corrected")

    return period_year, decision_date, record_date, payment_date


def normalize_and_fill_dividend(raw: _RawDividendEntry, upload_date: str) -> tuple[EpfrDividendEntry, list[str]]:
    """Normalize and validate raw AI dividend entry, auto-filling missing fields.

    Converts a raw AI extraction (_RawDividendEntry) into a validated
    EpfrDividendEntry with all required fields populated. Tracks which fields
    were auto-filled for downstream quality analysis.

    Args:
        raw: Raw dividend entry from AI extraction with potentially missing or
            invalid fields.
        upload_date: The upload date of the source file in ISO 8601 format (YYYY-MM-DD).
            Used as the baseline for auto-filling missing dates.

    Returns:
        tuple: (normalized EpfrDividendEntry, list of auto-filled field names).
            The EpfrDividendEntry is guaranteed to pass all business constraint validations.

    Business logic:
        - Amount conversion: String/int/float amounts converted to Decimal for
          8-decimal precision. None becomes Decimal("0").
        - Share type normalization: Any non-standard value defaults to "common".
          Tracks this in autofilled if the original wasn't "common" or "preferred".
        - Date auto-filling hierarchy:
          - decision_date: defaults to upload_date - 1 month (or today - 1 month if
            upload_date unavailable)
          - record_date: defaults to decision_date - 1 month
          - payment_date: defaults to decision_date + 1 day (if amount=0) or
            decision_date + 2 months (if amount>0)
        - Period normalization:
          - period_type: defaults to "annual" if missing or invalid
          - period_number: defaults to 1 if missing
          - period_year: defaults to decision_date.year if missing
        - Validation: Calls _validate_and_correct_dates to enforce date ordering.
          If validation fails, applies fallback corrections (payment_date and
          record_date adjustments) and retries validation.
        - All auto-filled fields are tracked in the returned list for audit purposes.
    """
    autofilled: list[str] = []

    decision_date = _parse_iso_date(raw.decision_date)
    record_date = _parse_iso_date(raw.record_date)
    payment_date = _parse_iso_date(raw.payment_date)
    amount = Decimal(str(raw.amount_per_share)) if raw.amount_per_share is not None else Decimal("0")

    share_type = raw.share_type if raw.share_type in {"common", "preferred"} else "common"
    if raw.share_type not in {"common", "preferred"}:
        autofilled.append("share_type")

    if decision_date is None:
        base = _parse_iso_date(upload_date) or datetime.now(UTC).date()
        decision_date = _shift_months(base, -1)
        autofilled.append("decision_date")
    if record_date is None:
        record_date = _shift_months(decision_date, -1)
        autofilled.append("record_date")
    if payment_date is None:
        if amount == 0:
            payment_date = decision_date.fromordinal(decision_date.toordinal() + 1)
        else:
            payment_date = _shift_months(decision_date, 2)
        autofilled.append("payment_date")

    period_type = raw.period_type if raw.period_type in {"annual", "halfyear", "quarterly"} else "annual"
    period_number = raw.period_number if raw.period_number is not None else 1
    period_year = raw.period_year if raw.period_year is not None else decision_date.year

    if raw.period_type is None:
        autofilled.append("period_type")
    if raw.period_number is None:
        autofilled.append("period_number")
    if raw.period_year is None:
        autofilled.append("period_year")
    if raw.amount_per_share is None:
        autofilled.append("amount_per_share")

    period_year, decision_date, record_date, payment_date = _validate_and_correct_dates(
        period_type=period_type,
        period_year=period_year,
        decision_date=decision_date,
        record_date=record_date,
        payment_date=payment_date,
        autofilled=autofilled,
    )

    try:
        normalized = EpfrDividendEntry(
            share_type=cast(ShareType, share_type),
            period_year=period_year,
            period_type=cast(PeriodType, period_type),
            period_number=period_number,
            amount_per_share=amount,
            decision_date=decision_date,
            record_date=record_date,
            payment_date=payment_date,
        )
    except ValidationError:
        if payment_date <= decision_date:
            payment_date = date.fromordinal(decision_date.toordinal() + 1)
            autofilled.append("payment_date_corrected")
        if decision_date < record_date:
            record_date = decision_date
            autofilled.append("record_date_corrected")
        normalized = EpfrDividendEntry(
            share_type=cast(ShareType, share_type),
            period_year=period_year,
            period_type=cast(PeriodType, period_type),
            period_number=period_number,
            amount_per_share=amount,
            decision_date=decision_date,
            record_date=record_date,
            payment_date=payment_date,
        )
    return normalized, autofilled


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
                extraction = EpfrDividendExtraction(
                    has_dividends=raw_extraction.has_dividends,
                    ai_comment=raw_extraction.ai_comment,
                    dividends=[],
                )

                autofilled_fields: list[str] = []
                if raw_extraction.dividends:
                    logger.info(
                        f"  [{company_index}.{file_index}] AI returned dividend entries: {len(raw_extraction.dividends)}, comment={raw_extraction.ai_comment!r}"
                    )
                    for div_idx, raw_div in enumerate(raw_extraction.dividends):
                        normalized, filled = normalize_and_fill_dividend(raw_div, distilled_file.upload_date)
                        extraction.dividends.append(normalized)
                        autofilled_fields.extend(filled)
                        if filled:
                            logger.info(
                                f"  [{company_index}.{file_index}] Dividend #{div_idx + 1} autofilled fields: {filled}"
                            )
                        logger.debug(
                            f"  [{company_index}.{file_index}] Dividend #{div_idx + 1}: year={normalized.period_year}, type={normalized.period_type}, amount={normalized.amount_per_share}"
                        )
                else:
                    logger.info(
                        f"  [{company_index}.{file_index}] AI found no dividends: comment={raw_extraction.ai_comment!r}"
                    )

                distilled_file.has_dividends = extraction.has_dividends
                distilled_file.ai_comment = extraction.ai_comment
                distilled_file.dividends = extraction.dividends
                distilled_file.autofilled_fields = sorted(set(autofilled_fields))
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

"""AI Data Distillation for CentralDepo workflow.

Uses Mistral Large to extract structured dividend data from MD files,
validates with Pydantic models via asynchronous chat.parse, and returns typed data structures.

This module handles:
- Loading and formatting prompt templates
- Processing MD files through Mistral Large (sequentially, 1 call at a time)
- Validating output with Pydantic models
- Error handling with retry logic
"""

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

from .config import (
    AI_COMPANY_DELAY,
    AI_FILE_DELAY,
    AI_MAX_RETRIES,
    AI_MODEL,
    AI_RETRY_BACKOFF_BASE,
    AI_TEMPERATURE,
)
from .models import DividendData

logger = logging.getLogger(__name__)

# Prompt template - loaded lazily for performance
_PROMPT_TEMPLATE: str | None = None


class AIDistiller:
    """AI distillation runtime with shared Mistral client and prompt context.

    The instance is intended to be reused across many document calls within one
    workflow activity execution so SDK client construction and prompt rendering
    happen once.

    Args:
        reference_date: Current execution date in YYYY-MM-DD format.
        model_name: Mistral model identifier.
        temperature: Model temperature.

    Raises:
        ValueError: If `MISTRAL_API_KEY` is not configured.
    """

    def __init__(
        self,
        reference_date: str,
        model_name: str = AI_MODEL,
        temperature: float = AI_TEMPERATURE,
    ) -> None:
        """Initialize the distiller with prompt context and API client.

        Args:
            reference_date: Current execution date in YYYY-MM-DD format.
            model_name: Mistral model identifier.
            temperature: Model temperature.

        Raises:
            ValueError: If `MISTRAL_API_KEY` is not configured.
        """
        from mistralai.client import Mistral as MistralClient

        api_key = os.environ.get("MISTRAL_API_KEY")
        if not api_key:
            raise ValueError("MISTRAL_API_KEY environment variable required for AI distillation")

        prompt_template = _load_prompt_template()
        self.system_instruction = prompt_template.replace("{{REFERENCE_DATE}}", reference_date)
        self.model_name = model_name
        self.temperature = temperature
        self.client = MistralClient(api_key=api_key)

    async def _extract_dividend_data(self, ocr_text: str) -> DividendData:
        """Extract and validate dividend data from one OCR markdown payload.

        Args:
            ocr_text: OCR markdown content for one converted document.

        Returns:
            Validated dividend extraction model.

        Raises:
            ValueError: If the model returns no choices or no parsed payload.
        """
        logger.info("Starting dividend extraction from OCR text")

        completion = await self.client.chat.parse_async(
            model=self.model_name,
            messages=[
                {"role": "system", "content": self.system_instruction},
                {"role": "user", "content": ocr_text},
            ],
            response_format=DividendData,
            temperature=self.temperature,
            max_tokens=4000,
        )

        if not completion.choices:
            raise ValueError("Mistral chat.parse returned no choices")

        message = completion.choices[0].message
        if message is None:
            raise ValueError("Mistral chat.parse returned empty message payload")

        result = message.parsed
        if result is None:
            raise ValueError("Mistral chat.parse returned empty parsed payload")

        if not isinstance(result, DividendData):
            result = DividendData.model_validate(result)

        logger.info("Structured response parsed successfully")
        return result

    async def extract_dividend_data_with_retry(self, ocr_text: str, md_path: Path) -> DividendData:
        """Extract dividend data with retry/backoff for transient API failures.

        Args:
            ocr_text: OCR markdown content for one converted document.
            md_path: Source markdown file path for logging context.

        Returns:
            Validated dividend extraction model.

        Raises:
            Exception: Propagates terminal SDK/validation errors after retries.
        """
        for attempt in range(AI_MAX_RETRIES):
            try:
                return await self._extract_dividend_data(ocr_text)
            except Exception as e:
                error_str = str(e).lower()
                is_retryable = any(s in error_str for s in ("503", "502", "429", "reset reason", "timeout", "overload"))

                if is_retryable and attempt < AI_MAX_RETRIES - 1:
                    wait = AI_RETRY_BACKOFF_BASE ** (attempt + 1)
                    logger.warning(
                        "Retryable AI error on attempt %d/%d for %s with model=%s, waiting %ds: %s",
                        attempt + 1,
                        AI_MAX_RETRIES,
                        md_path.name,
                        self.model_name,
                        wait,
                        e,
                    )
                    await asyncio.sleep(wait)
                    continue

                raise

        raise RuntimeError(f"Unexpected retry loop completion for {md_path}")


def _load_prompt_template() -> str:
    """Load the dividends parsing prompt template.

    Returns:
        The prompt template as a string
    """
    global _PROMPT_TEMPLATE
    if _PROMPT_TEMPLATE is None:
        prompt_path = Path(__file__).parent / "prompts" / "dividends_parsing.md"
        if not prompt_path.exists():
            raise FileNotFoundError(
                f"Prompt template not found at {prompt_path}. "
                "Ensure src/workflows/centraldepo/prompts/dividends_parsing.md exists."
            )
        _PROMPT_TEMPLATE = prompt_path.read_text(encoding="utf-8")
    return _PROMPT_TEMPLATE


async def process_single_file(
    md_path: Path,
    reference_date: str,
    model_name: str = AI_MODEL,
    temperature: float = AI_TEMPERATURE,
    distiller: AIDistiller | None = None,
) -> tuple[bool, dict[str, Any] | None, str | None]:
    """Process a single MD file through AI distillation with Mistral Large.

    Uses Mistral SDK async chat.parse with DividendData Pydantic model
    for structured output validation.

    Args:
        md_path: Path to the .md file to process
        reference_date: Current date in YYYY-MM-DD format for prompt context
        model_name: Model identifier (default: mistral-large-latest)
        temperature: Model temperature (default: 0.0 for deterministic)
        distiller: Optional shared distiller instance for client reuse.

    Returns:
        Tuple of (success, result_dict_or_none, error_message_or_none)
        - For empty files: (True, None, None) - will be null entry in output
        - For processing errors: (False, None, error_message)
        - For success: (True, validated_result_dict, None)
    """
    # Load file content
    try:
        content = md_path.read_text(encoding="utf-8")
    except Exception as e:
        error_msg = f"Failed to read file {md_path}: {type(e).__name__}: {e}"
        logger.error(error_msg)
        return (False, None, error_msg)

    # Handle empty files per user decision - include as null
    if not content.strip():
        logger.warning("Empty MD file, will be null in output: %s", md_path)
        return (True, None, None)

    active_distiller = distiller or AIDistiller(
        reference_date=reference_date,
        model_name=model_name,
        temperature=temperature,
    )

    try:
        parsed = await active_distiller.extract_dividend_data_with_retry(content, md_path)
        result_dict = parsed.model_dump(by_alias=True)

        logger.info(
            "Successfully distilled %s: has_dividends=%s, payouts=%d",
            md_path.name,
            result_dict.get("has_dividends"),
            len(result_dict.get("share_payouts", [])),
        )

        return (True, result_dict, None)
    except Exception as e:
        error_msg = f"AI processing failed for {md_path}: {type(e).__name__}: {e}"
        logger.error(error_msg)
        return (False, None, error_msg)


async def process_company_files(
    company_name: str,
    md_files: list[Path],
    reference_date: str,
    model_name: str = AI_MODEL,
    temperature: float = AI_TEMPERATURE,
    distiller: AIDistiller | None = None,
) -> tuple[int, int, dict[str, dict[str, Any]], list[str]]:
    """Process all MD files for a single company sequentially.

    Args:
        company_name: Company name for logging context
        md_files: List of Path objects to .md files
        reference_date: Current date in YYYY-MM-DD format
        model_name: Model identifier to use
        temperature: Model temperature
        distiller: Optional shared distiller instance for client reuse.

    Returns:
        Tuple of (success_count, failure_count, results_dict, failed_files)
        where results_dict maps filename -> result_dict
    """
    if not md_files:
        return (0, 0, {}, [])

    results = []
    for md_path in md_files:
        result = await process_single_file(
            md_path,
            reference_date,
            model_name=model_name,
            temperature=temperature,
            distiller=distiller,
        )
        results.append(result)
        # Rate limiting: delay between individual file processing within a company
        await asyncio.sleep(AI_FILE_DELAY)

    success_count = sum(1 for suc, _, _ in results if suc)
    failure_count = len(results) - success_count
    failed_files = [str(md_path) for md_path, (suc, _, err) in zip(md_files, results, strict=False) if not suc]

    # Build results dict - map filename to result
    # Note: empty files return (True, None, None) and should be included
    results_dict = {}
    for md_path, (suc, data, _) in zip(md_files, results, strict=False):
        if suc:
            results_dict[md_path.name] = data
        else:
            # Failed files are not included in results_dict
            # They will be represented as None in the final dividends list
            pass

    return (success_count, failure_count, results_dict, failed_files)


async def run_ai_distillation(
    results: list[tuple[str, list[str]]],
    output_root: Path,
    reference_date: str,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Run AI distillation for all companies sequentially.

    Processes MD files through Mistral Large and returns structured data
    ready for saving.

    Args:
        results: List of (company_name, urls) tuples from workflow
        output_root: Root output directory (e.g., Path("output"))
        reference_date: Current date in YYYY-MM-DD format

    Returns:
        Tuple of (distillation_data, stats)
        - distillation_data: Dict mapping company_hash to output structure
        - stats: Dictionary with processing statistics
    """
    from .downloader import get_company_folder_name

    all_distillation_data: dict[str, dict[str, Any]] = {}
    total_files = 0
    total_success = 0
    total_failed = 0
    all_failed_files: list[str] = []
    distiller: AIDistiller | None = None
    distiller_lock = asyncio.Lock()

    async def _get_distiller() -> AIDistiller:
        nonlocal distiller
        if distiller is not None:
            return distiller
        async with distiller_lock:
            if distiller is None:
                distiller = AIDistiller(reference_date=reference_date)
            return distiller

    async def _process_company(company_name: str, _urls: list[str]):
        """Process a single company's MD files."""
        folder_name = get_company_folder_name(company_name)
        folder_path = output_root / folder_name

        if not folder_path.exists():
            logger.warning("Company folder not found: %s", folder_path)
            return (folder_name, company_name, 0, 0, {}, [])

        # Find all MD files
        md_files = [f for f in folder_path.iterdir() if f.is_file() and f.suffix.lower() == ".md"]

        if not md_files:
            logger.debug("No MD files found for %s", company_name)
            return (folder_name, company_name, 0, 0, {}, [])

        company_distiller = await _get_distiller()

        return (
            folder_name,
            company_name,
            *await process_company_files(
                company_name,
                md_files,
                reference_date,
                distiller=company_distiller,
            ),
        )

    # Process companies sequentially
    company_results = []
    for cn, urls in results:
        result = await _process_company(cn, urls)
        company_results.append(result)
        # Rate limiting: delay between companies
        await asyncio.sleep(AI_COMPANY_DELAY)

    # Aggregate results
    for folder_name, company_name, success, failure, results_dict, failed in company_results:
        folder_path = output_root / folder_name

        # Count MD files in this company's folder
        md_files_in_folder = [f for f in folder_path.iterdir() if f.is_file() and f.suffix.lower() == ".md"]
        total_files += len(md_files_in_folder)
        total_success += success
        total_failed += failure
        all_failed_files.extend(failed)

        # Build dividends list - include null for empty files or failed processing
        dividends_list: list[dict[str, Any] | None] = []
        file_paths: list[str] = []

        for md_path in sorted(md_files_in_folder):
            file_paths.append(str(md_path.relative_to(output_root)))
            if md_path.name in results_dict:
                dividends_list.append(results_dict[md_path.name])
            else:
                # Empty file or failed processing - include null per user decision
                dividends_list.append(None)

        company_lower = company_name.lower()

        # Only include companies with at least one MD file
        if md_files_in_folder:
            all_distillation_data[folder_name] = {
                "company_name": company_lower,
                "files_paths": sorted(file_paths),
                "dividends": dividends_list,
            }

    stats = {
        "total_companies": len(results),
        "total_files": total_files,
        "successful": total_success,
        "failed": total_failed,
        "failed_files": all_failed_files,
        "output_path": str(output_root / "ai_distilled.json"),
    }

    logger.info(
        "AI Distillation complete: %d/%d files processed (%d failed), %d companies in output",
        total_success,
        total_files,
        total_failed,
        len(all_distillation_data),
    )

    return (all_distillation_data, stats)

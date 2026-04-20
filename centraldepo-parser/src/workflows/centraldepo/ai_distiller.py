"""AI Data Distillation for CentralDepo workflow.

Uses Mistral Large to extract structured dividend data from MD files,
validates with outlines using Pydantic models, and returns typed data structures.

This module handles:
- Loading and formatting prompt templates
- Processing MD files through Mistral Large
- Validating output with outlines + Pydantic models
- Managing concurrency and error handling
"""

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

from mistralai.client import Mistral as MistralClient

from .config import AI_MODEL, AI_TEMPERATURE, MAX_CONCURRENT_AI_REQUESTS
from .models import DividendData

logger = logging.getLogger(__name__)

# Prompt template - loaded lazily for performance
_PROMPT_TEMPLATE: str | None = None


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
) -> tuple[bool, dict[str, Any] | None, str | None]:
    """Process a single MD file through AI distillation with Mistral Large.

    Uses Mistral SDK chat.parse with DividendData Pydantic model
    for structured output validation.

    Args:
        md_path: Path to the .md file to process
        reference_date: Current date in YYYY-MM-DD format for prompt context
        model_name: Model identifier (default: mistral-large-latest)
        temperature: Model temperature (default: 0.0 for deterministic)

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

    # Load and format prompt
    try:
        prompt_template = _load_prompt_template()
        formatted_prompt = prompt_template.replace("{{DOCUMENT_TEXT}}", content).replace(
            "{{REFERENCE_DATE}}", reference_date
        )
    except Exception as e:
        error_msg = f"Failed to load/format prompt for {md_path}: {type(e).__name__}: {e}"
        logger.error(error_msg)
        return (False, None, error_msg)

    # Process with Mistral Large using structured output (chat.parse)
    try:
        client = MistralClient(api_key=os.environ.get("MISTRAL_API_KEY"))
        response = client.chat.parse(
            model=model_name,
            messages=[{"role": "user", "content": formatted_prompt}],
            temperature=temperature,
            response_format=DividendData,
        )

        parsed = response.choices[0].message.parsed
        if parsed is None:
            raise ValueError("Mistral returned no parsed content")

        result_dict = json.loads(parsed.model_dump_json())

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
    max_concurrent: int = MAX_CONCURRENT_AI_REQUESTS,
) -> tuple[int, int, dict[str, dict[str, Any]], list[str]]:
    """Process all MD files for a single company with controlled concurrency.

    Args:
        company_name: Company name for logging context
        md_files: List of Path objects to .md files
        reference_date: Current date in YYYY-MM-DD format
        model_name: Model identifier to use
        temperature: Model temperature
        max_concurrent: Maximum concurrent AI requests per company

    Returns:
        Tuple of (success_count, failure_count, results_dict, failed_files)
        where results_dict maps filename -> result_dict
    """
    if not md_files:
        return (0, 0, {}, [])

    semaphore = asyncio.Semaphore(max_concurrent)

    async def _process_with_semaphore(md_path: Path) -> tuple[bool, dict[str, Any] | None, str | None]:
        async with semaphore:
            return await process_single_file(
                md_path,
                reference_date,
                model_name=model_name,
                temperature=temperature,
            )

    tasks = [asyncio.create_task(_process_with_semaphore(md_path)) for md_path in md_files]

    results = await asyncio.gather(*tasks)

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
    """Run AI distillation for all companies with company-level parallelism.

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

    # Per user decision: parallel company processing
    company_semaphore = asyncio.Semaphore(MAX_CONCURRENT_AI_REQUESTS)

    async def _process_company(company_name: str, _urls: list[str]):
        """Process a single company's MD files."""
        async with company_semaphore:
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

            return (
                folder_name,
                company_name,
                *await process_company_files(
                    company_name,
                    md_files,
                    reference_date,
                ),
            )

    # Create tasks for all companies
    tasks = [_process_company(cn, urls) for cn, urls in results]
    company_results = await asyncio.gather(*tasks)

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

"""AI dividend distillation for EPFR markdown files."""

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

from mistralai.client import Mistral as MistralClient
from pydantic import BaseModel, Field, ValidationError

from .config import load_epfr_config, require_mistral_api_key
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
    share_type: str | None = None
    period_year: int | None = None
    period_type: str | None = None
    period_number: int | None = None
    amount_per_share: float | int | str | None = None
    decision_date: str | None = None
    record_date: str | None = None
    payment_date: str | None = None


class _RawExtraction(BaseModel):
    has_dividends: bool = False
    ai_comment: str = ""
    dividends: list[_RawDividendEntry] = Field(default_factory=list)


class AIDistiller:
    """Reusable Mistral chat.parse client for EPFR dividend extraction."""

    def __init__(self, model_name: str, temperature: float, reference_date: str) -> None:
        logger.info(
            f"Initializing AIDistiller: model={model_name}, temperature={temperature}, reference_date={reference_date}"
        )

        cfg = load_epfr_config()
        api_key = require_mistral_api_key(cfg)

        prompt_template = _load_prompt_template()
        self.system_instruction = prompt_template.replace("{{REFERENCE_DATE}}", reference_date)
        self.model_name = model_name
        self.temperature = temperature
        self.client = MistralClient(api_key=api_key, timeout_ms=cfg.ai_timeout * 1000)
        logger.info(f"AIDistiller ready: timeout_ms={cfg.ai_timeout * 1000}, prompt_template_loaded=True")

    async def extract(self, markdown_text: str) -> _RawExtraction:
        logger.debug(f"Sending extraction request: text_length={len(markdown_text)} chars")
        completion = await self.client.chat.parse_async(
            model=self.model_name,
            messages=[
                {"role": "system", "content": self.system_instruction},
                {"role": "user", "content": markdown_text},
            ],
            response_format=_RawExtraction,
            temperature=self.temperature,
            max_tokens=4000,
        )

        if not completion.choices:
            raise ValueError("Mistral chat.parse returned no choices")

        message = completion.choices[0].message
        if message is None or message.parsed is None:
            raise ValueError("Mistral chat.parse returned empty parsed payload")

        parsed = message.parsed
        if isinstance(parsed, _RawExtraction):
            result = parsed
        else:
            result = _RawExtraction.model_validate(parsed)

        logger.debug(
            f"Extraction result: has_dividends={result.has_dividends}, dividend_count={len(result.dividends)}, ai_comment={result.ai_comment!r}"
        )
        return result

    async def extract_with_retry(self, markdown_text: str, max_retries: int, file_path: Path) -> _RawExtraction:
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
    """Compute capped exponential backoff with jitter for AI retries."""
    cfg = load_epfr_config()
    capped_max = cfg.ai_retry_backoff_max_429 if is_rate_limited else cfg.ai_retry_backoff_max
    base_wait = min(float(cfg.ai_retry_backoff_base ** (attempt + 1)), float(capped_max))
    jitter_span = base_wait * cfg.ai_retry_jitter_ratio
    jitter = random.uniform(-jitter_span, jitter_span)
    wait = base_wait + jitter
    return max(0.1, round(wait, 2))


def _load_prompt_template() -> str:
    global _PROMPT_TEMPLATE
    if _PROMPT_TEMPLATE is None:
        prompt_path = Path(__file__).parent / "prompts" / "dividends_parsing.md"
        if not prompt_path.exists():
            raise FileNotFoundError(f"Prompt template not found at {prompt_path}")
        _PROMPT_TEMPLATE = prompt_path.read_text(encoding="utf-8")
    return _PROMPT_TEMPLATE


def _parse_iso_date(value: str | None) -> date | None:
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d").date()


def _shift_months(value: date, months: int) -> date:
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
    """Replace year safely, handling leap-day rollover."""
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
    """Apply post-extraction date sanity checks and corrections."""
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
    """Normalize AI raw dividend payload and auto-fill missing required dates."""
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
    """Run sequential AI distillation over mapped EPFR markdown files."""
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

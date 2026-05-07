"""Pydantic data models for EPFR workflow.

Models define the minimal fields consumed by the workflow from the EPFR API at
/portal/reporting/securities-market and define workflow I/O shapes.
Unknown or unused API fields are silently ignored.
"""

from datetime import date
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from .config import AI_DISTILLED_FILENAME, AI_FILE_DELAY, AI_MAX_RETRIES, AI_MODEL, AI_TEMPERATURE, MAPPING_FILENAME


class Holder(BaseModel):
    """Represent the dividend-report holder (issuer company) from an EPFR record."""

    id: int
    title: str
    unp: str = ""


class EpfrRecord(BaseModel):
    """Represent one dividend disclosure record returned by the EPFR API.

    Only fields actually used by the pipeline are declared; all other API
    fields (user, organization, subCategoryType, etc.) are ignored by Pydantic.
    """

    id: int
    name: str
    real_upload_date: str = Field(default="", alias="realUploadDate")
    holder: Holder | None = None

    model_config = {"populate_by_name": True}


class EpfrApiResponse(BaseModel):
    """Represent one paginated EPFR securities-market API response.

    Only the fields used by the fetching loop are declared; pagination metadata
    and sort details are ignored.
    """

    content: list[EpfrRecord] = Field(default_factory=list)
    last: bool = False
    total_pages: int = Field(default=0, alias="totalPages")

    model_config = {"populate_by_name": True}


class EpfrFileRecord(BaseModel):
    """Represent one file entry in the final UNP mapping output.

    The lineage fields show whether a business document came from an archive,
    an office-document conversion, or PDF OCR.
    """

    id: int
    filename: str
    original_name: str
    upload_date: str = ""
    extracted_from: str | None = None  # Archive filename if extracted
    converted_from: str | None = None  # Source filename if converted to .md


class CompanyFiles(BaseModel):
    """Represent all mapped disclosure files for one company UNP."""

    title: str
    holder_id: int
    files: list[EpfrFileRecord] = Field(default_factory=list)


class EpfrWorkflowInput(BaseModel):
    """Configure the EPFR downloader workflow execution."""

    max_pages: int = Field(
        default=10,
        ge=1,
        le=100,
        description="Maximum number of API pages to iterate (default: 10)",
    )
    date_from: str = Field(
        default="2026-03-01",
        description="Start date filter in YYYY-MM-DD format (searchDateFrom parameter)",
    )
    timeout: int = Field(
        default=60,
        ge=10,
        le=300,
        description="Per-request timeout in seconds (default: 60)",
    )
    output_dir: str = Field(
        default="output",
        description="Root directory for downloaded files and mapping JSON",
    )


class EpfrWorkflowOutput(BaseModel):
    """Report EPFR downloader workflow totals and mapping location."""

    total_records: int = 0
    total_files_downloaded: int = 0
    total_companies: int = 0
    mapping_path: str = ""
    stats: dict[str, Any] = Field(default_factory=dict)


class EpfrPdfOcrInput(BaseModel):
    """Configure the separate EPFR PDF OCR workflow execution."""

    output_dir: str = Field(
        default="output",
        description="Root directory containing UNP folders and mapping JSON",
    )
    mapping_filename: str = Field(
        default="unp_file_mapping.json",
        description="Mapping filename inside output_dir",
    )
    overwrite: bool = Field(
        default=True,
        description="If True, overwrite existing markdown files",
    )
    cleanup_source: bool = Field(
        default=True,
        description="If True, remove source PDF after successful OCR conversion",
    )
    unps: list[str] | None = Field(
        default=None,
        description="Optional list of UNPs to process; all UNPs if omitted",
    )


class EpfrPdfOcrOutput(BaseModel):
    """Report OCR workflow totals, failures, cleanup, and raw stats."""

    mapping_path: str = ""
    total_pdf_entries: int = 0
    total_successful: int = 0
    total_failed: int = 0
    total_skipped: int = 0
    cleaned_up_files: list[str] = Field(default_factory=list)
    failed_files: list[str] = Field(default_factory=list)
    stats: dict[str, Any] = Field(default_factory=dict)


PeriodType = Literal["annual", "halfyear", "quarterly"]


class EpfrDividendEntry(BaseModel):
    """Represent one normalized dividend payout extracted from one file."""

    period_year: int = Field(..., ge=1990)
    period_type: PeriodType
    period_number: int = Field(..., ge=1)
    amount_per_share: Decimal = Field(..., ge=Decimal("0"), max_digits=20, decimal_places=8)
    decision_date: date
    record_date: date
    payment_date: date

    @model_validator(mode="after")
    def validate_period_and_dates(self) -> "EpfrDividendEntry":
        """Validate period numbering and date ordering business constraints."""
        if self.period_type == "annual" and self.period_number != 1:
            raise ValueError("period_number must be 1 for annual period_type")
        if self.period_type == "halfyear" and self.period_number not in {1, 2}:
            raise ValueError("period_number must be 1 or 2 for halfyear period_type")
        if self.period_type == "quarterly" and self.period_number not in {1, 2, 3, 4}:
            raise ValueError("period_number must be between 1 and 4 for quarterly period_type")
        if self.decision_date < self.record_date:
            raise ValueError("decision_date must be greater than or equal to record_date")
        if self.payment_date <= self.decision_date:
            raise ValueError("payment_date must be greater than decision_date")
        return self


class EpfrDividendExtraction(BaseModel):
    """Represent AI extraction output for one document with payout details."""

    has_dividends: bool
    ai_comment: str = ""
    dividends: list[EpfrDividendEntry] = Field(default_factory=list)


class EpfrAiDistilledFile(BaseModel):
    """Represent one mapped file enriched with AI-extracted dividend details."""

    id: int
    file_path: str
    filename: str
    original_name: str
    upload_date: str = ""
    extracted_from: str | None = None
    converted_from: str | None = None
    has_dividends: bool = False
    ai_comment: str = ""
    dividends: list[EpfrDividendEntry] = Field(default_factory=list)
    autofilled_fields: list[str] = Field(default_factory=list)
    error: str | None = None


class EpfrAiDistilledCompany(BaseModel):
    """Represent all distilled files for one company UNP bucket."""

    company_name: str
    unp: str
    holder_id: int
    files: list[EpfrAiDistilledFile] = Field(default_factory=list)


class EpfrAiDistillerInput(BaseModel):
    """Configure EPFR AI dividend distillation workflow execution."""

    output_dir: str = Field(
        default="output",
        description="Root directory containing UNP folders and mapping JSON",
    )
    mapping_filename: str = Field(
        default=MAPPING_FILENAME,
        description="Input mapping filename inside output_dir",
    )
    output_filename: str = Field(
        default=AI_DISTILLED_FILENAME,
        description="Output JSON filename inside output_dir",
    )
    model_name: str = Field(
        default=AI_MODEL,
        description="Mistral model identifier for chat.parse extraction",
    )
    temperature: float = Field(
        default=AI_TEMPERATURE,
        ge=0,
        le=2,
        description="Mistral model temperature",
    )
    max_retries: int = Field(
        default=AI_MAX_RETRIES,
        ge=1,
        le=10,
        description="Maximum retry attempts for transient AI call failures",
    )
    file_delay_seconds: float = Field(
        default=AI_FILE_DELAY,
        ge=0,
        le=30,
        description="Delay between sequential file processing operations",
    )
    unps: list[str] | None = Field(
        default=None,
        description="Optional subset of UNP company folders to process",
    )


class EpfrAiDistillerOutput(BaseModel):
    """Report AI distillation totals, failures, and output location."""

    output_path: str = ""
    total_companies: int = 0
    total_files: int = 0
    successful: int = 0
    failed: int = 0
    stats: dict[str, Any] = Field(default_factory=dict)

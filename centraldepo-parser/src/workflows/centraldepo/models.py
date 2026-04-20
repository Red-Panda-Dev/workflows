"""Pydantic data models for CentralDepo workflow."""

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


class PeriodType(StrEnum):
    """Type of dividend period for DB mapping."""

    annual = "annual"
    halfyear = "halfyear"
    quarterly = "quarterly"


class DividendRecord(BaseModel):
    """Single dividend disclosure entry from centraldepo.by."""

    company_name: str = Field(..., description="Name of the company")
    archive_url: str = Field(..., description="Absolute URL to the dividend archive file")


class ScrapeResult(BaseModel):
    """Result of scraping a single page."""

    page: int = Field(..., description="Page number that was scraped")
    items: list[DividendRecord] = Field(default_factory=list, description="List of dividend records found on page")
    success: bool = Field(..., description="Whether the scrape was successful")
    error: str | None = Field(default=None, description="Error message if scrape failed")


class CompanyResult(BaseModel):
    """Aggregated result for a single company with all its URLs."""

    company_name: str = Field(..., description="Name of the company")
    urls: list[str] = Field(default_factory=list, description="List of archive URLs for this company")


class WorkflowInput(BaseModel):
    """Input for the centraldepo-parser workflow."""

    max_pages: int = Field(
        default=10,
        ge=1,
        le=100,
        description="Maximum number of pages to scrape (default: 10)",
    )
    delay: float = Field(
        default=1.0,
        ge=0.0,
        le=10.0,
        description="Delay between page requests in seconds (default: 1.0)",
    )
    timeout: int = Field(
        default=180,
        ge=10,
        le=600,
        description="Per-request timeout in seconds (default: 180)",
    )
    output_path: str = Field(
        default="output/centraldepo_dividends.json",
        description="Path to save the JSON output file (default: output/centraldepo_dividends.json)",
    )


class WorkflowOutput(BaseModel):
    """Output structure: array of company entries with their URLs and metadata."""

    results: list[CompanyResult] = Field(
        default_factory=list,
        description="List of company results with grouped URLs",
    )
    stats: dict[str, Any] = Field(
        default_factory=dict,
        description="Metadata about the scrape operation",
    )
    download_stats: dict[str, Any] | None = Field(
        default=None,
        description="Statistics from file download phase (null if downloads disabled)",
    )
    extraction_stats: dict[str, Any] | None = Field(
        default=None,
        description="Statistics from archive extraction phase (null if extraction disabled)",
    )
    conversion_stats: dict[str, Any] | None = Field(
        default=None,
        description="Statistics from file conversion to MD phase (null if conversion disabled)",
    )
    distillation_stats: dict[str, Any] | None = Field(
        default=None,
        description="Statistics from AI distillation phase (null if distillation disabled)",
    )


# =============================================================================
# AI Data Distillation Models
# =============================================================================


class PaymentDeadline(BaseModel):
    """Payment deadline for dividend payout."""

    value: str | None = Field(
        default=None,
        description="The deadline value (date string, raw text, or null)",
    )
    precision: Literal["day", "month", "year", "text"] | None = Field(
        default=None,
        description="Precision level of the deadline",
    )
    source: Literal["explicit", "inferred"] | None = Field(
        default=None,
        description="Whether deadline was explicitly stated or inferred",
    )


class SharePayout(BaseModel):
    """Dividend payout information for a specific share type."""

    share_type: Literal["common", "preferred", "unspecified"] = Field(
        ...,
        description="Type of shares (common, preferred, or unspecified)",
    )
    period_year: int | None = Field(
        default=None,
        description="Year of the dividend period (e.g., 2024). Null if not explicitly stated or safely mappable.",
    )
    period_type: PeriodType | None = Field(
        default=None,
        description="Type of dividend period. Null if not explicitly stated or safely mappable.",
    )
    period_number: int | None = Field(
        default=None,
        description="Number within the period type (1-4 for quarterly, 1-2 for halfyear, 1 for annual). Null if not applicable.",
    )
    amount_per_share: float | None = Field(
        default=None,
        description="Gross dividend amount per one share. Numeric or null if not clearly stated.",
    )
    currency: Literal["BYN"] = Field(
        ...,
        description="Currency code - must be BYN for valid dividends",
    )
    amount_unit: Literal["per_share"] = Field(
        "per_share",
        description="Unit of the amount (always per_share)",
    )
    payment_deadline: PaymentDeadline | None = Field(
        default=None,
        description="Deadline for this payout, if specified",
    )
    extra_conditions: str | None = Field(
        default=None,
        description="Additional conditions for payout (e.g., bank transfer requirement)",
    )


class DecisionDate(BaseModel):
    """Date when dividend payment was decided."""

    value: str | None = Field(
        default=None,
        description="The decision date value (YYYY-MM-DD, YYYY-MM, YYYY, or null)",
    )
    precision: Literal["day", "month", "year"] | None = Field(
        default=None,
        description="Precision level of the date",
    )
    source: Literal["explicit", "inferred"] | None = Field(
        default=None,
        description="Whether date was explicitly stated or inferred from document",
    )


class PaymentPeriod(BaseModel):
    """Period for which dividends are paid."""

    from_date: str | None = Field(
        default=None,
        alias="from",
        description="Start of payment period (YYYY-MM-DD, YYYY-MM, or YYYY)",
    )
    to: str | None = Field(
        default=None,
        description="End of payment period (YYYY-MM-DD, YYYY-MM, or YYYY)",
    )
    precision: Literal["day", "month", "year"] | None = Field(
        default=None,
        description="Precision level of the period",
    )
    source: Literal["explicit", "inferred_default_previous_year"] | None = Field(
        default=None,
        description="Whether period was explicitly stated or defaulted to previous year",
    )


class DividendData(BaseModel):
    """Structured dividend data extracted from a single document by AI.

    This model represents the output of processing one MD file through
    the Mistral Large model with the dividends_parsing prompt.
    """

    has_dividends: bool = Field(
        ...,
        description="Whether the document confirms dividend payment in BYN",
    )
    decision_date: DecisionDate | None = Field(
        default=None,
        description="Date when dividend payment was decided (null if has_dividends=false)",
    )
    payment_period: PaymentPeriod | None = Field(
        default=None,
        description="Period for which dividends are paid (null if has_dividends=false)",
    )
    share_payouts: list[SharePayout] = Field(
        default_factory=list,
        description="List of payouts by share type (empty if has_dividends=false)",
    )
    notes: list[str] = Field(
        default_factory=list,
        description="List of notes about the extraction (e.g., defaults applied)",
    )


class CompanyDividendResult(BaseModel):
    """AI-distilled dividend data for a single company.

    Structure matches the required output format:
    {<company_name_hash>: {company_name: ..., files_paths: [...], dividends: [...]}}
    """

    company_name: str = Field(
        ...,
        description="Company name in lowercase",
    )
    files_paths: list[str] = Field(
        default_factory=list,
        description="List of relative paths to MD files for this company",
    )
    dividends: list[DividendData | None] = Field(
        default_factory=list,
        description="List of extracted dividend data per MD file (null for empty/failed files)",
    )

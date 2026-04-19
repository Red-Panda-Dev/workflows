"""Pydantic data models for CentralDepo workflow."""

from typing import Any

from pydantic import BaseModel, Field


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

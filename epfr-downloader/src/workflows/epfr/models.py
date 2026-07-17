"""Pydantic data models for EPFR workflow.

Models define the minimal fields consumed by the workflow from the EPFR API at
/portal/reporting/securities-market and define workflow I/O shapes.
Unknown or unused API fields are silently ignored.
"""

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field, field_serializer, field_validator, model_validator


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

    max_pages: int | None = Field(
        default=None, ge=1, description="Maximum number of API pages to iterate (default: 10)"
    )
    date_from: str | None = Field(
        default=None, description="Start date filter in YYYY-MM-DD format (searchDateFrom parameter)"
    )
    date_to: str = Field(
        default_factory=lambda: date.today().isoformat(),
        description="End date filter in YYYY-MM-DD format (searchDateTo parameter)",
    )
    timeout: int | None = Field(default=None, ge=1, description="Per-request timeout in seconds (default: 60)")
    output_dir: str | None = Field(default=None, description="Root directory for downloaded files and mapping JSON")


class EpfrWorkflowOutput(BaseModel):
    """Report EPFR downloader workflow totals and mapping location."""

    total_records: int = 0
    total_files_downloaded: int = 0
    total_companies: int = 0
    mapping_path: str = ""
    stats: dict[str, Any] = Field(default_factory=dict)


class EpfrOcrInput(BaseModel):
    """Configure the EPFR OCR workflow execution (supports PDF, PNG, JPG, JPEG)."""

    output_dir: str | None = Field(default=None, description="Root directory containing UNP folders and mapping JSON")
    mapping_filename: str | None = Field(default=None, description="Mapping filename inside output_dir")
    overwrite: bool = Field(default=True, description="If True, overwrite existing markdown files")
    cleanup_source: bool | None = Field(
        default=None, description="If True, remove source files after successful OCR conversion"
    )
    unps: list[str] | None = Field(default=None, description="Optional list of UNPs to process; all UNPs if omitted")


class EpfrOcrOutput(BaseModel):
    """Report OCR workflow totals, failures, cleanup, and raw stats."""

    mapping_path: str = ""
    total_ocr_entries: int = 0
    total_successful: int = 0
    total_failed: int = 0
    total_skipped: int = 0
    cleaned_up_files: list[str] = Field(default_factory=list)
    failed_files: list[str] = Field(default_factory=list)
    stats: dict[str, Any] = Field(default_factory=dict)


# Backward compatibility aliases
EpfrPdfOcrInput = EpfrOcrInput
EpfrPdfOcrOutput = EpfrOcrOutput


# OCR Workflow Internal Models
# These models support the 3-activity split for UI progress tracking
# Supports PDF, PNG, JPG, JPEG files


class OcrWorkItem(BaseModel):
    """Represents a single file to be OCR'd (PDF, PNG, JPG, JPEG).

    Used by the scan_ocr_entries activity to pass work items to the
    process_ocr_files activity.

    Attributes:
        unp: Company tax identifier (folder name).
        file_index: Index of the file entry in the mapping[unp]["files"] list.
        filename: Original filename.
        file_path: Full filesystem path to the file.
        entry: Original mapping entry dictionary for this file.
    """

    unp: str
    file_index: int
    filename: str
    file_path: str
    entry: dict[str, Any]


class OcrScanResult(BaseModel):
    """Result of scanning the mapping file for OCR-able entries.

    Output of the scan_ocr_entries activity and input to process_ocr_files.

    Attributes:
        mapping_path: Absolute path to the mapping JSON file.
        mapping_raw: Full mapping dictionary for downstream processing.
        total_unps_scanned: Number of UNP entries scanned.
        total_ocr_entries: Total count of OCR-able files found.
        work_items: List of files that need OCR processing.
        by_unp: Per-UNP statistics from the scan phase.
        output_dir: Resolved output directory path.
        mapping_filename: Resolved mapping filename.
        cleanup_source: Resolved cleanup_source flag.
    """

    mapping_path: str
    mapping_raw: dict[str, Any]
    total_unps_scanned: int
    total_ocr_entries: int
    work_items: list[OcrWorkItem]
    by_unp: dict[str, dict[str, Any]]
    output_dir: str
    mapping_filename: str
    cleanup_source: bool | None = None


class OcrFileResult(BaseModel):
    """Result of OCR'ing a single file (PDF, PNG, JPG, JPEG).

    Captures the outcome of processing one file entry.

    Attributes:
        unp: Company tax identifier.
        file_index: Index in the mapping[unp]["files"] list.
        status: Processing result - SUCCESS, FAILED, or SKIPPED.
        original_filename: Original filename.
        new_filename: Markdown filename if successful, None otherwise.
        source_path: Full path to the source file.
        error: Error message if status is FAILED or SKIPPED.
        converted_from: Value for the converted_from field in updated mapping.
    """

    unp: str
    file_index: int
    status: Literal["SUCCESS", "FAILED", "SKIPPED"]
    original_filename: str
    new_filename: str | None = None
    source_path: str | None = None
    error: str | None = None
    converted_from: str | None = None


class OcrProcessResult(BaseModel):
    """Result of the OCR processing phase.

    Output of the process_ocr_files activity and input to finalize_ocr_mapping.

    Attributes:
        updated_mapping: Mapping dict with OCR entries updated to .md.
        results: Per-file results from OCR processing.
        total_successful: Count of successfully OCR'd files.
        total_failed: Count of failed OCR attempts.
        total_skipped: Count of skipped files (already exists, etc.).
        failed_files: List of paths to failed files.
        skipped_files: List of paths to skipped files.
        cleaned_up_files: List of paths to files deleted after successful OCR.
    """

    updated_mapping: dict[str, Any]
    results: list[OcrFileResult]
    total_successful: int
    total_failed: int
    total_skipped: int
    failed_files: list[str]
    skipped_files: list[str]
    cleaned_up_files: list[str]


# Backward compatibility aliases for PDF OCR
PdfOcrWorkItem = OcrWorkItem
PdfOcrScanResult = OcrScanResult
PdfOcrFileResult = OcrFileResult
PdfOcrProcessResult = OcrProcessResult


PeriodType = Literal["annual", "halfyear", "quarterly"]
ShareType = Literal["common", "preferred"]

AMOUNT_PER_SHARE_QUANTUM = Decimal("0.00000001")


def _normalize_amount_per_share(value: Any) -> Any:
    """Round dividend amounts to the 8-decimal schema precision."""
    if isinstance(value, Decimal):
        decimal_value = value
    else:
        try:
            decimal_value = Decimal(str(value))
        except Exception:  # noqa: BLE001
            return value

    quantized = decimal_value.quantize(AMOUNT_PER_SHARE_QUANTUM, rounding=ROUND_HALF_UP)
    fixed_point = format(quantized, "f")
    if "." in fixed_point:
        fixed_point = fixed_point.rstrip("0").rstrip(".")
    return Decimal(fixed_point or "0")


class EpfrDividendEntry(BaseModel):
    """Represent one normalized dividend payout extracted from one file."""

    share_type: ShareType
    period_year: int = Field(..., ge=1990)
    period_type: PeriodType
    period_number: int = Field(..., ge=1)
    amount_per_share: Decimal = Field(..., ge=Decimal("0"), max_digits=20, decimal_places=8)
    decision_date: date | None = None
    record_date: date | None = None
    payment_date: date | None = None

    @field_validator("amount_per_share", mode="before")
    @classmethod
    def normalize_amount_per_share(cls, value: Any) -> Any:
        """Normalize AI-emitted precision before decimal-place validation runs."""
        return _normalize_amount_per_share(value)

    @model_validator(mode="after")
    def validate_period_and_dates(self) -> EpfrDividendEntry:
        """Validate period numbering and date ordering business constraints."""
        if self.period_type == "annual" and self.period_number != 1:
            raise ValueError("period_number must be 1 for annual period_type")
        if self.period_type == "halfyear" and self.period_number not in {1, 2}:
            raise ValueError("period_number must be 1 or 2 for halfyear period_type")
        if self.period_type == "quarterly" and self.period_number not in {1, 2, 3, 4}:
            raise ValueError("period_number must be between 1 and 4 for quarterly period_type")
        if self.decision_date is not None and self.record_date is not None and self.decision_date < self.record_date:
            raise ValueError("decision_date must be greater than or equal to record_date")
        if self.payment_date is not None and self.decision_date is not None and self.payment_date <= self.decision_date:
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
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None


class EpfrAiDistilledCompany(BaseModel):
    """Represent all distilled files for one company UNP bucket."""

    company_name: str
    unp: str
    holder_id: int
    files: list[EpfrAiDistilledFile] = Field(default_factory=list)


class EpfrAiDistillerInput(BaseModel):
    """Configure EPFR AI dividend distillation workflow execution."""

    output_dir: str | None = Field(default=None, description="Root directory containing UNP folders and mapping JSON")
    mapping_filename: str | None = Field(default=None, description="Input mapping filename inside output_dir")
    output_filename: str | None = Field(default=None, description="Output JSON filename inside output_dir")
    model_name: str | None = Field(default=None, description="Mistral model identifier for chat.parse extraction")
    temperature: float | None = Field(default=None, ge=0.0, description="Mistral model temperature")
    max_retries: int | None = Field(
        default=None, ge=0, description="Maximum retry attempts for transient AI call failures"
    )
    max_tokens: int | None = Field(default=None, ge=256, description="Maximum AI response tokens per document")
    file_delay_seconds: float | None = Field(
        default=None, ge=0.0, description="Delay between sequential file processing operations"
    )
    unps: list[str] | None = Field(default=None, description="Optional subset of UNP company folders to process")


class EpfrAiDistillerOutput(BaseModel):
    """Report AI distillation totals, failures, and output location."""

    output_path: str = ""
    total_companies: int = 0
    total_files: int = 0
    successful: int = 0
    failed: int = 0
    stats: dict[str, Any] = Field(default_factory=dict)


# =============================================================================
# AI Distiller Workflow Internal Models
# These models support the 3-activity split for UI progress tracking
# =============================================================================


class AiDistillerWorkItem(BaseModel):
    """Represents a single markdown file to be processed by AI distiller.

    Used by the scan_ai_distiller_files activity to pass work items to the
    process_ai_distillation activity.

    Attributes:
        unp: Company tax identifier (folder name).
        company_title: Company name from the mapping.
        holder_id: Holder ID from the mapping.
        file_path: Full filesystem path to the markdown file.
        filename: Original markdown filename.
        original_name: Original name of the source file.
        upload_date: Upload date from the mapping.
        file_id: File ID from the mapping.
        extracted_from: Archive filename if extracted, None otherwise.
        converted_from: Source filename if converted, None otherwise.
    """

    unp: str
    company_title: str
    holder_id: int
    file_path: str
    filename: str
    original_name: str
    upload_date: str
    file_id: int
    extracted_from: str | None = None
    converted_from: str | None = None


class AiDistillerScanResult(BaseModel):
    """Result of scanning the mapping for markdown files to distill.

    Output of the scan_ai_distiller_files activity and input to process_ai_distillation.

    Attributes:
        mapping_path: Absolute path to the mapping JSON file.
        total_companies: Number of companies in the mapping.
        total_files: Total count of markdown files found.
        work_items: List of markdown files that need AI distillation.
        output_dir: Resolved output directory path.
        output_filename: Resolved output filename.
        model_name: Resolved Mistral model name.
        temperature: Resolved model temperature.
        max_retries: Resolved maximum retry attempts.
        file_delay_seconds: Resolved delay between file processing.
    """

    mapping_path: str
    total_companies: int
    total_files: int
    work_items: list[AiDistillerWorkItem]
    output_dir: str
    output_filename: str
    model_name: str
    temperature: float
    max_retries: int
    max_tokens: int = 4000
    file_delay_seconds: float


class AiDistillerFileResult(BaseModel):
    """Result of AI distilling a single markdown file.

    Captures the outcome of processing one markdown file through AI extraction.

    Attributes:
        unp: Company tax identifier.
        filename: Original markdown filename.
        status: Processing result - SUCCESS or FAILED.
        has_dividends: Whether the AI found dividends in the file.
        ai_comment: AI-generated commentary about the extraction.
        dividends: List of extracted dividend entries (serialized dicts).
        autofilled_fields: List of fields that were auto-filled during normalization.
        error: Error message if status is FAILED.
        file_id: File ID from the original mapping.
    """

    unp: str
    filename: str
    status: Literal["SUCCESS", "FAILED"]
    has_dividends: bool
    ai_comment: str
    dividends: list[dict[str, Any]]
    autofilled_fields: list[str]
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None
    file_id: int


class AiDistillerProcessResult(BaseModel):
    """Result of the AI distillation processing phase.

    Output of the process_ai_distillation activity and input to finalize_ai_distillation.

    Attributes:
        results: Dict of file results keyed by UNP.
        total_files: Total number of files processed.
        successful: Count of successfully processed files.
        failed: Count of failed files.
        failed_files: List of paths to failed files.
        total_companies: Number of companies processed.
    """

    results: dict[str, AiDistillerFileResult]  # keyed by unp
    total_files: int
    successful: int
    failed: int
    failed_files: list[str]
    total_companies: int


# =============================================================================
# Share Payout Exporter Workflow Internal Models
# These models support the 3-activity split for UI progress tracking
# =============================================================================


class SharePayoutScanResult(BaseModel):
    """Result of scanning CSV and distilled JSON for payout export.

    Output of the scan_share_payout_export activity and input to process_share_payout_matching.

    Attributes:
        csv_path: Absolute path to the shares CSV file.
        csv_index: Mapping of (unp, share_kind) to instrument_uuid.
        csv_stats: Statistics from CSV loading (ambiguous keys, known UNPs).
        distilled_path: Absolute path to the distilled JSON file.
        distilled_data: Full distilled data dictionary.
        output_dir: Resolved output directory path.
        output_filename: Resolved output filename.
    """

    csv_path: str
    csv_index: dict[str, str]  # unp|share_kind -> instrument_uuid (serializable)
    csv_stats: dict[str, Any]  # ambiguous_share_kind, known_unps, ambiguous_keys (as set of strings)
    distilled_path: str
    distilled_data: dict[str, Any]
    output_dir: str
    output_filename: str


class SharePayoutProcessResult(BaseModel):
    """Result of matching dividends against share reference.

    Output of the process_share_payout_matching activity and input to finalize_share_payout_export.

    Attributes:
        export_data: Dict of payout rows keyed by UNP.
        matched_count: Number of successfully matched payouts.
        skipped_file_errors: Number of files skipped due to errors.
        autofilled_share_type: Number of dividends with autofilled share_type.
        missing_csv_unp: Number of UNPs not in CSV.
        missing_share_kind: Number of share_kinds not in CSV.
        ambiguous_share_kind: Number of ambiguous (unp, share_kind) combinations.
        samples: Sample data for debugging unmatched cases.
    """

    export_data: dict[str, list[dict]]  # unp -> list of payout rows
    matched_count: int
    skipped_file_errors: int
    autofilled_share_type: int
    missing_csv_unp: int
    missing_share_kind: int
    ambiguous_share_kind: int
    samples: dict[str, list[dict]]


class EpfrSharePayoutExportRow(BaseModel):
    """A payout row model for the DB-ready export.

    Each row represents a single dividend payout matched to a share instrument.
    The ``unp`` is NOT a field — it is used as the top-level dict key instead.
    Serialized keys: share_uuid, period_year, period_type, period_number,
    amount_per_share, decision_date, record_date, payment_date.
    """

    share_uuid: str
    period_year: int = Field(..., ge=1990)
    period_type: PeriodType
    period_number: int = Field(..., ge=1)
    amount_per_share: Decimal = Field(..., ge=Decimal("0"), max_digits=20, decimal_places=8)
    decision_date: date | None = None
    record_date: date | None = None
    payment_date: date | None = None

    @field_validator("amount_per_share", mode="before")
    @classmethod
    def normalize_amount_per_share(cls, value: Any) -> Any:
        """Normalize export amounts to match the shared dividend precision contract."""
        return _normalize_amount_per_share(value)

    @field_serializer("amount_per_share", mode="plain")
    @classmethod
    def _serialize_amount(cls, v: Decimal) -> str:
        return str(v)

    @model_validator(mode="after")
    def validate_period_and_dates(self) -> EpfrSharePayoutExportRow:
        """Validate period numbering and date ordering business constraints."""
        if self.period_type == "annual" and self.period_number != 1:
            raise ValueError("period_number must be 1 for annual period_type")
        if self.period_type == "halfyear" and self.period_number not in {1, 2}:
            raise ValueError("period_number must be 1 or 2 for halfyear period_type")
        if self.period_type == "quarterly" and self.period_number not in {1, 2, 3, 4}:
            raise ValueError("period_number must be between 1 and 4 for quarterly period_type")
        if self.decision_date is not None and self.record_date is not None and self.decision_date < self.record_date:
            raise ValueError("decision_date must be greater than or equal to record_date")
        if self.payment_date is not None and self.decision_date is not None and self.payment_date <= self.decision_date:
            raise ValueError("payment_date must be greater than decision_date")
        return self


class EpfrSharePayoutExportInput(BaseModel):
    """Configure the share payout export workflow execution."""

    output_dir: str | None = Field(
        default=None, description="Root directory containing distilled JSON and export output"
    )
    input_filename: str | None = Field(default=None, description="Input distilled JSON filename inside output_dir")
    output_filename: str | None = Field(default=None, description="Output export JSON filename inside output_dir")
    shares_csv_path: str | None = Field(
        default=None, description="Optional override path for shares CSV; uses config default when None"
    )


class EpfrSharePayoutExportOutput(BaseModel):
    """Report share payout export totals and output location."""

    output_path: str = ""
    sql_path: str = ""
    total_companies: int = 0
    total_payouts: int = 0
    matched_payouts: int = 0
    unmatched_payouts: int = 0
    sql_records: int = 0
    stats: dict[str, Any] = Field(default_factory=dict)

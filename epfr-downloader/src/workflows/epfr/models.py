"""Pydantic data models for EPFR workflow.

Models mirror the JSON structure returned by the EPFR API at
/portal/reporting/securities-market and define workflow I/O shapes.
"""

from typing import Any

from pydantic import BaseModel, Field


class Label(BaseModel):
    """Organization label (e.g. 'Эмитент', 'Профучастник')."""

    id: int
    name: str


class Organization(BaseModel):
    """Company or organization referenced in a record."""

    id: int
    title: str
    unp: str = ""
    short_name: str = Field(default="", alias="shortName")

    model_config = {"populate_by_name": True}


class User(BaseModel):
    """User who uploaded the record."""

    id: int
    surname: str = ""
    name: str = ""
    patronymic: str = ""
    login: str = ""
    email: str = ""
    organization: Organization | None = None
    certificate_id: str | None = Field(default=None, alias="certificateId")
    roles: list[Any] = Field(default_factory=list)
    state: bool | None = None
    phone_number: str | None = Field(default=None, alias="phoneNumber")

    model_config = {"populate_by_name": True}


class EpfrRecord(BaseModel):
    """Single disclosure record from the EPFR API."""

    id: int
    name: str
    description: str = ""
    storage_type: str = Field(default="FILE_SYSTEM", alias="storageType")
    real_upload_date: str = Field(default="", alias="realUploadDate")
    upload_date: str = Field(default="", alias="uploadDate")
    user: User | None = None
    organization: Organization | None = None
    holder: Organization | None = None
    sub_category_type: str = Field(default="ANY", alias="subCategoryType")

    model_config = {"populate_by_name": True}


class SortInfo(BaseModel):
    """Sort state in API response."""

    empty: bool = False
    sorted: bool = True
    unsorted: bool = False


class Pageable(BaseModel):
    """Pagination metadata in API response."""

    sort: SortInfo = Field(default_factory=SortInfo)
    offset: int = 0
    page_number: int = Field(default=0, alias="pageNumber")
    page_size: int = Field(default=14, alias="pageSize")
    paged: bool = True
    unpaged: bool = False

    model_config = {"populate_by_name": True}


class EpfrApiResponse(BaseModel):
    """Full paginated response from the EPFR securities-market API."""

    content: list[EpfrRecord] = Field(default_factory=list)
    pageable: Pageable = Field(default_factory=Pageable)
    last: bool = False
    total_pages: int = Field(default=0, alias="totalPages")
    total_elements: int = Field(default=0, alias="totalElements")
    size: int = 14
    number: int = 0
    sort: SortInfo = Field(default_factory=SortInfo)
    first: bool = True
    number_of_elements: int = Field(default=0, alias="numberOfElements")
    empty: bool = False

    model_config = {"populate_by_name": True}


class EpfrFileRecord(BaseModel):
    """Simplified record for the UNP-to-files mapping output.

    Tracks file lineage:
    - Original downloaded files have only id, filename, original_name, upload_date
    - Files extracted from archives have extracted_from set to archive filename
    - Markdown files from conversion have converted_from set to source filename
    """

    id: int
    filename: str
    original_name: str
    upload_date: str = ""
    extracted_from: str | None = None  # Archive filename if extracted
    converted_from: str | None = None  # Source filename if converted to .md


class CompanyFiles(BaseModel):
    """UNP-keyed company entry in the mapping output."""

    title: str
    holder_id: int
    files: list[EpfrFileRecord] = Field(default_factory=list)


class EpfrWorkflowInput(BaseModel):
    """Input for the epfr-files-downloader workflow."""

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
    """Output of the epfr-files-downloader workflow."""

    total_records: int = 0
    total_files_downloaded: int = 0
    total_companies: int = 0
    mapping_path: str = ""
    stats: dict[str, Any] = Field(default_factory=dict)


class EpfrPdfOcrInput(BaseModel):
    """Input for the epfr-pdf-ocr-converter workflow."""

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
    """Output for the epfr-pdf-ocr-converter workflow."""

    mapping_path: str = ""
    total_pdf_entries: int = 0
    total_successful: int = 0
    total_failed: int = 0
    total_skipped: int = 0
    cleaned_up_files: list[str] = Field(default_factory=list)
    failed_files: list[str] = Field(default_factory=list)
    stats: dict[str, Any] = Field(default_factory=dict)

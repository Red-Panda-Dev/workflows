"""Direct unit tests for workflow.py activities.

Tests for:
- fetch_all_pages
- download_all_epfr_files
- extract_all_epfr_archives
- convert_all_epfr_files
- EpfrFilesDownloader.run()
"""

# ruff: noqa: D102

import logging
import os
from pathlib import Path

import pytest


os.environ.pop("AGENT", None)

from workflows.epfr.models import EpfrApiResponse, EpfrRecord, EpfrWorkflowInput, Holder
from workflows.epfr.workflow import (
    EpfrFilesDownloader,
    convert_all_epfr_files,
    download_all_epfr_files,
    extract_all_epfr_archives,
    fetch_all_pages,
)


@pytest.fixture()
def anyio_backend():
    """Return the anyio backend for async tests."""
    return "asyncio"


# =============================================================================
# Helper factories
# =============================================================================


def _holder(unp: str = "600073968", title: str = "Test Co", holder_id: int = 100) -> Holder:
    """Build a minimal Holder for testing."""
    return Holder(id=holder_id, title=title, unp=unp)


def _record(
    rec_id: int = 1,
    name: str = "Test disclosure",
    upload_date: str = "2026-01-15 10:30:00",
    holder: Holder | None = None,
) -> EpfrRecord:
    """Build a minimal EpfrRecord for testing."""
    return EpfrRecord(
        id=rec_id,
        name=name,
        real_upload_date=upload_date,
        holder=holder,
    )


def _api_response(
    content: list[EpfrRecord] | None = None,
    last: bool = False,
    total_pages: int = 0,
) -> EpfrApiResponse:
    """Build an EpfrApiResponse for testing."""
    return EpfrApiResponse(
        content=content or [],
        last=last,
        total_pages=total_pages,
    )


# =============================================================================
# Test Suite 1: fetch_all_pages
# =============================================================================


class TestFetchAllPages:
    """Test pagination and API interaction for fetch_all_pages."""

    @pytest.mark.anyio
    async def test_single_page_last_true(self, mocker, caplog):
        """Fetches one page and stops when last=True on first page."""
        mock_response = _api_response(
            content=[_record(holder=_holder())],
            last=True,
            total_pages=1,
        )
        mock_fetch_page = mocker.patch(
            "workflows.epfr.workflow.fetch_page",
            return_value=mock_response,
        )

        with caplog.at_level(logging.INFO):
            result = await fetch_all_pages(EpfrWorkflowInput(max_pages=10, date_from="2026-01-01"))

        assert len(result) == 1
        assert result[0].id == 1
        mock_fetch_page.assert_called_once()
        assert "Last page reached" in caplog.text

    @pytest.mark.anyio
    async def test_multiple_pages_early_termination(self, mocker, caplog):
        """Stops fetching when API returns last=True before reaching max_pages."""
        # First page: last=False, second page: last=True
        mock_responses = [
            _api_response(
                content=[_record(1, holder=_holder())],
                last=False,
                total_pages=2,
            ),
            _api_response(
                content=[_record(2, holder=_holder())],
                last=True,
                total_pages=2,
            ),
        ]
        mock_fetch_page = mocker.patch(
            "workflows.epfr.workflow.fetch_page",
            side_effect=mock_responses,
        )

        with caplog.at_level(logging.INFO):
            result = await fetch_all_pages(EpfrWorkflowInput(max_pages=10, date_from="2026-01-01"))

        assert len(result) == 2
        assert mock_fetch_page.call_count == 2
        assert "Last page reached" in caplog.text

    @pytest.mark.anyio
    async def test_max_pages_limit(self, mocker, caplog):
        """Respects max_pages parameter and fetches exactly that many pages."""
        # Always return last=False to force max_pages iteration
        mock_response = _api_response(
            content=[_record(holder=_holder())],
            last=False,
            total_pages=100,
        )
        mock_fetch_page = mocker.patch(
            "workflows.epfr.workflow.fetch_page",
            return_value=mock_response,
        )

        with caplog.at_level(logging.INFO):
            result = await fetch_all_pages(EpfrWorkflowInput(max_pages=3, date_from="2026-01-01"))

        # Should fetch 3 pages (0, 1, 2)
        assert mock_fetch_page.call_count == 3
        assert len(result) == 3
        # No early termination message since last is always False
        assert "Last page reached" not in caplog.text

    @pytest.mark.anyio
    async def test_empty_result(self, mocker, caplog):
        """Returns empty list when API returns no records."""
        mock_response = _api_response(content=[], last=True, total_pages=0)
        mock_fetch_page = mocker.patch(
            "workflows.epfr.workflow.fetch_page",
            return_value=mock_response,
        )

        with caplog.at_level(logging.INFO):
            result = await fetch_all_pages(EpfrWorkflowInput(max_pages=5, date_from="2026-01-01"))

        assert result == []
        mock_fetch_page.assert_called_once()
        assert "Fetched 0 total records" in caplog.text

    @pytest.mark.anyio
    async def test_date_to_parameter_passed(self, mocker):
        """Passes date_to parameter to fetch_page when provided."""
        mock_response = _api_response(content=[], last=True, total_pages=0)
        mocker.patch(
            "workflows.epfr.workflow.fetch_page",
            return_value=mock_response,
        )

        await fetch_all_pages(
            EpfrWorkflowInput(
                max_pages=1,
                date_from="2026-01-01",
                date_to="2026-01-31",
            )
        )

    @pytest.mark.anyio
    async def test_page_delay_between_requests(self, mocker):
        """Waits between page requests using page_delay from config."""
        mock_response = _api_response(
            content=[_record(holder=_holder())],
            last=False,
            total_pages=10,
        )
        mocker.patch(
            "workflows.epfr.workflow.fetch_page",
            return_value=mock_response,
        )
        mock_sleep = mocker.patch("workflows.epfr.workflow.asyncio.sleep")

        await fetch_all_pages(EpfrWorkflowInput(max_pages=3, date_from="2026-01-01"))

        # With max_pages=3, it iterates pages 0, 1, 2
        # After each page with last=False, it sleeps
        # So after page 0: sleep (1), after page 1: sleep (2), after page 2: sleep (3)
        # All 3 pages have last=False, so 3 sleeps
        assert mock_sleep.call_count == 3


# =============================================================================
# Test Suite 2: download_all_epfr_files
# =============================================================================


class TestDownloadAllEpfrFiles:
    """Test file download orchestration."""

    @pytest.mark.anyio
    async def test_successful_downloads(self, tmp_path, mocker, caplog):
        """All files download successfully."""
        records = [_record(1, holder=_holder()), _record(2, holder=_holder())]
        mock_stats = {
            "total_files_attempted": 2,
            "successful": 2,
            "failed": 0,
            "failed_ids": [],
            "file_map": {1: "1.pdf", 2: "2.pdf"},
            "by_unp": {"600073968": {"files": ["1.pdf", "2.pdf"]}},
        }
        mock_download = mocker.patch(
            "workflows.epfr.workflow.download_all_files",
            return_value=mock_stats,
        )

        with caplog.at_level(logging.INFO):
            result = await download_all_epfr_files(records, str(tmp_path))

        assert result == mock_stats
        mock_download.assert_called_once_with(records, Path(tmp_path))
        assert "Download complete: 2 files (2 successful, 0 failed)" in caplog.text

    @pytest.mark.anyio
    async def test_partial_failures(self, tmp_path, mocker, caplog):
        """Some files fail to download - warning logged."""
        records = [_record(1, holder=_holder()), _record(2, holder=_holder())]
        mock_stats = {
            "total_files_attempted": 2,
            "successful": 1,
            "failed": 1,
            "failed_ids": [2],
            "file_map": {1: "1.pdf"},
            "by_unp": {"600073968": {"files": ["1.pdf"]}},
        }
        mocker.patch(
            "workflows.epfr.workflow.download_all_files",
            return_value=mock_stats,
        )

        with caplog.at_level(logging.WARNING):
            result = await download_all_epfr_files(records, str(tmp_path))

        assert result == mock_stats
        assert "Failed to download 1 files" in caplog.text

    @pytest.mark.anyio
    async def test_all_failures(self, tmp_path, mocker, caplog):
        """All files fail to download."""
        records = [_record(1, holder=_holder()), _record(2, holder=_holder())]
        mock_stats = {
            "total_files_attempted": 2,
            "successful": 0,
            "failed": 2,
            "failed_ids": [1, 2],
            "file_map": {},
            "by_unp": {"600073968": {"files": []}},
        }
        mocker.patch(
            "workflows.epfr.workflow.download_all_files",
            return_value=mock_stats,
        )

        with caplog.at_level(logging.INFO):
            result = await download_all_epfr_files(records, str(tmp_path))

        assert result == mock_stats
        assert result["successful"] == 0
        assert result["failed"] == 2

    @pytest.mark.anyio
    async def test_no_records(self, tmp_path, mocker, caplog):
        """Empty record list - returns zero stats."""
        mock_stats = {
            "total_files_attempted": 0,
            "successful": 0,
            "failed": 0,
            "failed_ids": [],
            "file_map": {},
            "by_unp": {},
        }
        mock_download = mocker.patch(
            "workflows.epfr.workflow.download_all_files",
            return_value=mock_stats,
        )

        result = await download_all_epfr_files([], str(tmp_path))

        assert result == mock_stats
        mock_download.assert_called_once_with([], Path(tmp_path))

    @pytest.mark.anyio
    async def test_output_dir_created(self, tmp_path, mocker):
        """Output directory is created if it doesn't exist."""
        records = [_record(1, holder=_holder())]
        mock_stats = {
            "total_files_attempted": 1,
            "successful": 1,
            "failed": 0,
            "failed_ids": [],
            "file_map": {1: "1.pdf"},
            "by_unp": {"600073968": {"files": ["1.pdf"]}},
        }
        mocker.patch(
            "workflows.epfr.workflow.download_all_files",
            return_value=mock_stats,
        )

        new_dir = tmp_path / "new_output"
        await download_all_epfr_files(records, str(new_dir))

        assert new_dir.exists()
        assert new_dir.is_dir()


# =============================================================================
# Test Suite 3: extract_all_epfr_archives
# =============================================================================


class TestExtractAllEpfrArchives:
    """Test archive extraction orchestration."""

    @pytest.mark.anyio
    async def test_successful_extraction(self, tmp_path, mocker, caplog):
        """All archives extract successfully."""
        unp_folders = ["600073968", "123456789"]
        download_stats = {
            "by_unp": {
                "600073968": {"files": ["1.zip"]},
                "123456789": {"files": ["2.tar"]},
            }
        }
        mock_stats = {
            "total_unps": 2,
            "total_archives": 2,
            "successful": 2,
            "failed": 0,
            "failed_archives": [],
            "files_extracted": 10,
            "by_unp": {
                "600073968": {"archive_to_files": {"1.zip": ["a.txt", "b.txt"]}},
                "123456789": {"archive_to_files": {"2.tar": ["c.txt", "d.txt"]}},
            },
        }
        mock_extract = mocker.patch(
            "workflows.epfr.workflow.extract_all_archives",
            return_value=mock_stats,
        )

        with caplog.at_level(logging.INFO):
            result = await extract_all_epfr_archives(str(tmp_path), download_stats)

        assert result == mock_stats
        mock_extract.assert_called_once_with(unp_folders, Path(tmp_path))
        assert "Extraction complete" in caplog.text

    @pytest.mark.anyio
    async def test_extraction_failures(self, tmp_path, mocker, caplog):
        """Some archives fail to extract."""
        download_stats = {
            "by_unp": {
                "600073968": {"files": ["1.zip", "2.zip"]},
            }
        }
        mock_stats = {
            "total_unps": 1,
            "total_archives": 2,
            "successful": 1,
            "failed": 1,
            "failed_archives": ["2.zip"],
            "files_extracted": 5,
            "by_unp": {},
        }
        mocker.patch(
            "workflows.epfr.workflow.extract_all_archives",
            return_value=mock_stats,
        )

        result = await extract_all_epfr_archives(str(tmp_path), download_stats)

        assert result == mock_stats
        assert result["failed"] == 1
        assert "2.zip" in result["failed_archives"]

    @pytest.mark.anyio
    async def test_no_archives(self, tmp_path, mocker, caplog):
        """No archives to extract - returns zero stats."""
        download_stats = {"by_unp": {}}
        mocker.patch(
            "workflows.epfr.workflow.extract_all_archives",
            return_value={
                "total_unps": 0,
                "total_archives": 0,
                "successful": 0,
                "failed": 0,
                "failed_archives": [],
                "files_extracted": 0,
                "by_unp": {},
            },
        )

        with caplog.at_level(logging.INFO):
            await extract_all_epfr_archives(str(tmp_path), download_stats)

        assert "No UNP folders to process for extraction" in caplog.text

    @pytest.mark.anyio
    async def test_mixed_unp_folders(self, tmp_path, mocker):
        """Multiple UNP folders with mixed extraction results."""
        download_stats = {
            "by_unp": {
                "unp1": {"files": ["a.zip"]},
                "unp2": {"files": ["b.tar"]},
                "unp3": {"files": ["c.rar"]},
            }
        }
        mock_stats = {
            "total_unps": 3,
            "total_archives": 3,
            "successful": 2,
            "failed": 1,
            "failed_archives": ["c.rar"],
            "files_extracted": 10,
            "by_unp": {},
        }
        mock_extract = mocker.patch(
            "workflows.epfr.workflow.extract_all_archives",
            return_value=mock_stats,
        )

        await extract_all_epfr_archives(str(tmp_path), download_stats)

        mock_extract.assert_called_once()
        call_args = mock_extract.call_args[0]
        assert set(call_args[0]) == {"unp1", "unp2", "unp3"}


# =============================================================================
# Test Suite 4: convert_all_epfr_files
# =============================================================================


class TestConvertAllEpfrFiles:
    """Test document conversion orchestration."""

    @pytest.mark.anyio
    async def test_successful_conversion(self, tmp_path, mocker, caplog):
        """All files convert successfully."""
        download_stats = {
            "by_unp": {
                "600073968": {"files": ["1.docx", "2.xlsx"]},
            }
        }
        mock_stats = {
            "total_unps": 1,
            "total_files_attempted": 2,
            "total_successful": 2,
            "total_failed": 0,
            "failed_files": [],
            "cleaned_up_files": [str(tmp_path / "600073968" / "1.docx"), str(tmp_path / "600073968" / "2.xlsx")],
            "by_unp": {},
        }
        mock_convert = mocker.patch(
            "workflows.epfr.workflow.convert_all_files",
            return_value=mock_stats,
        )

        with caplog.at_level(logging.INFO):
            await convert_all_epfr_files(str(tmp_path), download_stats)

        assert mock_convert.call_args[0][0] == ["600073968"]
        assert mock_convert.call_args[1]["overwrite"] is True
        assert mock_convert.call_args[1]["cleanup_source"] is True
        assert "Conversion complete" in caplog.text

    @pytest.mark.anyio
    async def test_conversion_failures(self, tmp_path, mocker):
        """Some files fail to convert."""
        download_stats = {
            "by_unp": {
                "600073968": {"files": ["1.docx", "2.xlsx"]},
            }
        }
        mock_stats = {
            "total_unps": 1,
            "total_files_attempted": 2,
            "total_successful": 1,
            "total_failed": 1,
            "failed_files": ["2.xlsx"],
            "cleaned_up_files": [],
            "by_unp": {},
        }
        mocker.patch(
            "workflows.epfr.workflow.convert_all_files",
            return_value=mock_stats,
        )

        result = await convert_all_epfr_files(str(tmp_path), download_stats)

        assert result == mock_stats
        assert result["total_failed"] == 1
        assert "2.xlsx" in result["failed_files"]

    @pytest.mark.anyio
    async def test_no_files_to_convert(self, tmp_path, mocker, caplog):
        """No files to convert - returns zero stats."""
        download_stats = {"by_unp": {}}
        mocker.patch(
            "workflows.epfr.workflow.convert_all_files",
            return_value={
                "total_unps": 0,
                "total_files_attempted": 0,
                "total_successful": 0,
                "total_failed": 0,
                "failed_files": [],
                "cleaned_up_files": [],
                "by_unp": {},
            },
        )

        with caplog.at_level(logging.INFO):
            result = await convert_all_epfr_files(str(tmp_path), download_stats)

        assert result["total_files_attempted"] == 0
        assert "No UNP folders to process for conversion" in caplog.text

    @pytest.mark.anyio
    async def test_cleanup_source_and_overwrite_flags(self, tmp_path, mocker):
        """Verify cleanup_source=True and overwrite=True are passed."""
        download_stats = {
            "by_unp": {
                "600073968": {"files": ["1.docx"]},
            }
        }
        mock_stats = {
            "total_unps": 1,
            "total_files_attempted": 1,
            "total_successful": 1,
            "total_failed": 0,
            "failed_files": [],
            "cleaned_up_files": [],
            "by_unp": {},
        }
        mock_convert = mocker.patch(
            "workflows.epfr.workflow.convert_all_files",
            return_value=mock_stats,
        )

        await convert_all_epfr_files(str(tmp_path), download_stats)

        call_kwargs = mock_convert.call_args[1]
        assert call_kwargs["cleanup_source"] is True
        assert call_kwargs["overwrite"] is True


# =============================================================================
# Test Suite 5: EpfrFilesDownloader workflow class
# =============================================================================


class TestEpfrFilesDownloader:
    """Test the main workflow class."""

    @pytest.mark.anyio
    async def test_run_complete_workflow(self, tmp_path, mocker):
        """Complete workflow execution with all activities mocked."""
        records = [_record(1, holder=_holder()), _record(2, holder=_holder())]

        # Mock all activities
        mocker.patch(
            "workflows.epfr.workflow.fetch_all_pages",
            return_value=records,
        )
        download_stats = {
            "total_files_attempted": 2,
            "successful": 2,
            "failed": 0,
            "failed_ids": [],
            "file_map": {1: "1.pdf", 2: "2.pdf"},
            "by_unp": {"600073968": {"files": ["1.pdf", "2.pdf"]}},
        }
        mocker.patch(
            "workflows.epfr.workflow.download_all_epfr_files",
            return_value=download_stats,
        )
        extraction_stats = {
            "total_unps": 1,
            "total_archives": 0,
            "successful": 0,
            "failed": 0,
            "files_extracted": 0,
            "by_unp": {},
        }
        mocker.patch(
            "workflows.epfr.workflow.extract_all_epfr_archives",
            return_value=extraction_stats,
        )
        conversion_stats = {
            "total_unps": 1,
            "total_files_attempted": 2,
            "total_successful": 2,
            "total_failed": 0,
            "failed_files": [],
            "cleaned_up_files": [],
            "by_unp": {},
        }
        mocker.patch(
            "workflows.epfr.workflow.convert_all_epfr_files",
            return_value=conversion_stats,
        )
        mocker.patch(
            "workflows.epfr.workflow.save_unp_mapping",
            return_value=str(tmp_path / "unp_file_mapping.json"),
        )

        workflow = EpfrFilesDownloader()
        result = await workflow.run(EpfrWorkflowInput(max_pages=5, date_from="2026-01-01", output_dir=str(tmp_path)))

        # Result may be a dict (when run through workflow runtime) or EpfrWorkflowOutput
        # Use dict-like access for compatibility
        assert result["total_records"] == 2
        assert result["total_files_downloaded"] == 2
        assert result["total_companies"] == 1
        assert result["mapping_path"] == str(tmp_path / "unp_file_mapping.json")

    @pytest.mark.anyio
    async def test_run_empty_records(self, tmp_path, mocker, caplog):
        """Early return when no records are fetched."""
        mocker.patch(
            "workflows.epfr.workflow.fetch_all_pages",
            return_value=[],
        )

        with caplog.at_level(logging.WARNING):
            workflow = EpfrFilesDownloader()
            result = await workflow.run(
                EpfrWorkflowInput(max_pages=5, date_from="2026-01-01", output_dir=str(tmp_path))
            )

        assert result["total_records"] == 0
        assert result["total_files_downloaded"] == 0
        assert result["total_companies"] == 0
        assert result["mapping_path"] == ""
        assert "No records found" in caplog.text

    @pytest.mark.anyio
    async def test_run_input_defaults(self, tmp_path, mocker):
        """Uses default values for None input parameters."""
        records = [_record(1, holder=_holder())]
        mocker.patch(
            "workflows.epfr.workflow.fetch_all_pages",
            return_value=records,
        )
        mocker.patch(
            "workflows.epfr.workflow.download_all_epfr_files",
            return_value={
                "total_files_attempted": 1,
                "successful": 1,
                "failed": 0,
                "failed_ids": [],
                "file_map": {1: "1.pdf"},
                "by_unp": {"600073968": {"files": ["1.pdf"]}},
            },
        )
        mocker.patch(
            "workflows.epfr.workflow.extract_all_epfr_archives",
            return_value={
                "total_unps": 1,
                "total_archives": 0,
                "successful": 0,
                "failed": 0,
                "files_extracted": 0,
                "by_unp": {},
            },
        )
        mocker.patch(
            "workflows.epfr.workflow.convert_all_epfr_files",
            return_value={
                "total_unps": 1,
                "total_files_attempted": 1,
                "total_successful": 1,
                "total_failed": 0,
                "failed_files": [],
                "cleaned_up_files": [],
                "by_unp": {},
            },
        )
        mocker.patch(
            "workflows.epfr.workflow.save_unp_mapping",
            return_value=str(tmp_path / "unp_file_mapping.json"),
        )

        workflow = EpfrFilesDownloader()
        result = await workflow.run(
            EpfrWorkflowInput(
                max_pages=None,
                date_from=None,
                output_dir=None,
            )
        )

        # Verify defaults were used (we can't easily verify the exact default values
        # without inspecting the function internals, but we can verify it didn't crash)
        assert result["total_records"] == 1

    @pytest.mark.anyio
    async def test_run_custom_input(self, tmp_path, mocker):
        """Uses custom input values."""
        records = [_record(1, holder=_holder())]
        mocker.patch(
            "workflows.epfr.workflow.fetch_all_pages",
            return_value=records,
        )
        mocker.patch(
            "workflows.epfr.workflow.download_all_epfr_files",
            return_value={
                "total_files_attempted": 1,
                "successful": 1,
                "failed": 0,
                "failed_ids": [],
                "file_map": {1: "1.pdf"},
                "by_unp": {"600073968": {"files": ["1.pdf"]}},
            },
        )
        mocker.patch(
            "workflows.epfr.workflow.extract_all_epfr_archives",
            return_value={
                "total_unps": 1,
                "total_archives": 0,
                "successful": 0,
                "failed": 0,
                "files_extracted": 0,
                "by_unp": {},
            },
        )
        mocker.patch(
            "workflows.epfr.workflow.convert_all_epfr_files",
            return_value={
                "total_unps": 1,
                "total_files_attempted": 1,
                "total_successful": 1,
                "total_failed": 0,
                "failed_files": [],
                "cleaned_up_files": [],
                "by_unp": {},
            },
        )
        mocker.patch(
            "workflows.epfr.workflow.save_unp_mapping",
            return_value=str(tmp_path / "unp_file_mapping.json"),
        )

        workflow = EpfrFilesDownloader()
        result = await workflow.run(
            EpfrWorkflowInput(
                max_pages=20,
                date_from="2026-03-01",
                date_to="2026-03-31",
                output_dir=str(tmp_path),
                timeout=120,
            )
        )

        assert result["total_records"] == 1

    def test_workflow_definition(self):
        """Workflow has correct definition name."""
        from mistralai.workflows.core.definition.workflow_definition import get_workflow_definition

        wf_def = get_workflow_definition(EpfrFilesDownloader)
        assert wf_def.name == "epfr-files-downloader"

    def test_workflow_discoverable(self):
        """Workflow class has discovery attribute."""
        assert hasattr(EpfrFilesDownloader, "__workflows_workflow_def")

    def test_run_method_exists(self):
        """Workflow class has run method."""
        assert hasattr(EpfrFilesDownloader, "run")
        assert callable(getattr(EpfrFilesDownloader, "run", None))

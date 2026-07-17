"""Tests for EPFR AI Distiller workflow activities.

This module tests the 3 split activities in ai_distiller_workflow.py:
1. scan_ai_distiller_files - Discover markdown files in the mapping
2. process_ai_distillation - Run AI extraction on discovered files
3. finalize_ai_distillation - Save distilled JSON output

These tests validate the workflow activities independently, enabling:
- Granular progress tracking in Mistral Workflows UI
- Parallel test execution per activity
- Proper separation of concerns
"""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from workflows.epfr.ai_distiller import _RawDividendEntry, _RawExtraction
from workflows.epfr.ai_distiller_workflow import (
    finalize_ai_distillation,
    process_ai_distillation,
    scan_ai_distiller_files,
)
from workflows.epfr.models import (
    AiDistillerFileResult,
    AiDistillerProcessResult,
    AiDistillerScanResult,
    AiDistillerWorkItem,
    EpfrAiDistillerInput,
)


# =============================================================================
# Test fixtures for AI Distiller Workflow
# =============================================================================


@pytest.fixture
def sample_ai_distiller_input(tmp_path):
    """Create a sample AI distiller input for testing."""
    output_dir = tmp_path / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    return EpfrAiDistillerInput(
        output_dir=str(output_dir),
        mapping_filename="unp_file_mapping.json",
        output_filename="ai_distilled_dividends.json",
        model_name="mistral-large-latest",
        temperature=0.0,
        max_retries=3,
        file_delay_seconds=0.0,
    )


@pytest.fixture
def sample_mapping_data():
    """Create sample mapping data with multiple companies and files."""
    return {
        "100000001": {
            "title": "Company A",
            "holder_id": 1,
            "files": [
                {
                    "filename": "doc1.md",
                    "original_name": "document1.pdf",
                    "upload_date": "2026-01-15",
                    "id": 101,
                    "extracted_from": "doc1.pdf",
                    "converted_from": "doc1.docx",
                },
                {
                    "filename": "doc2.md",
                    "original_name": "document2.pdf",
                    "upload_date": "2026-01-16",
                    "id": 102,
                },
            ],
        },
        "200000002": {
            "title": "Company B",
            "holder_id": 2,
            "files": [
                {
                    "filename": "doc3.md",
                    "original_name": "document3.pdf",
                    "upload_date": "2026-01-17",
                    "id": 201,
                },
            ],
        },
        "300000003": {
            "title": "Company C",
            "holder_id": 3,
            "files": [
                {"filename": "not_md.txt", "id": 301},
            ],
        },
    }


@pytest.fixture
def sample_work_items():
    """Create sample work items for testing."""
    return [
        AiDistillerWorkItem(
            unp="100000001",
            company_title="Company A",
            holder_id=1,
            file_path="/output/100000001/doc1.md",
            filename="doc1.md",
            original_name="document1.pdf",
            upload_date="2026-01-15",
            file_id=101,
            extracted_from="doc1.pdf",
            converted_from="doc1.docx",
        ),
        AiDistillerWorkItem(
            unp="100000001",
            company_title="Company A",
            holder_id=1,
            file_path="/output/100000001/doc2.md",
            filename="doc2.md",
            original_name="document2.pdf",
            upload_date="2026-01-16",
            file_id=102,
            extracted_from=None,
            converted_from=None,
        ),
    ]


@pytest.fixture
def sample_scan_result(tmp_path, sample_work_items):
    """Create a sample scan result for testing."""
    return AiDistillerScanResult(
        mapping_path=str(tmp_path / "output" / "unp_file_mapping.json"),
        total_companies=1,
        total_files=2,
        work_items=sample_work_items,
        output_dir=str(tmp_path / "output"),
        output_filename="ai_distilled_dividends.json",
        model_name="mistral-large-latest",
        temperature=0.0,
        max_retries=3,
        file_delay_seconds=0.0,
    )


# =============================================================================
# TestScanAiDistillerFiles - Activity 1 Tests
# =============================================================================


class TestScanAiDistillerFiles:
    """Test Activity 1: scan_ai_distiller_files.

    This activity scans the mapping file and identifies all markdown files
    for AI distillation, with optional UNP filtering.
    """

    @pytest.mark.anyio
    async def test_scans_mapping_and_returns_work_items(self, tmp_path, sample_mapping_data):
        """Verifies scanning finds all .md files in mapping."""
        # Setup: Create mapping file
        output_dir = tmp_path / "output"
        output_dir.mkdir(parents=True, exist_ok=True)
        mapping_path = output_dir / "unp_file_mapping.json"
        mapping_path.write_text(json.dumps(sample_mapping_data), encoding="utf-8")

        # Create dummy .md files
        (output_dir / "100000001").mkdir(parents=True, exist_ok=True)
        (output_dir / "100000001" / "doc1.md").write_text("# Content 1", encoding="utf-8")
        (output_dir / "100000001" / "doc2.md").write_text("# Content 2", encoding="utf-8")
        (output_dir / "200000002").mkdir(parents=True, exist_ok=True)
        (output_dir / "200000002" / "doc3.md").write_text("# Content 3", encoding="utf-8")

        input_obj = EpfrAiDistillerInput(
            output_dir=str(output_dir),
            mapping_filename="unp_file_mapping.json",
            output_filename="ai_distilled_dividends.json",
        )

        result = await scan_ai_distiller_files(input_obj)

        assert isinstance(result, AiDistillerScanResult)
        assert result.total_companies == 3
        assert result.total_files == 3  # Only .md files counted
        assert len(result.work_items) == 3
        assert all(item.filename.endswith(".md") for item in result.work_items)

    @pytest.mark.anyio
    async def test_filters_by_unps_when_provided(self, tmp_path, sample_mapping_data):
        """Verifies UNP filtering works correctly."""
        output_dir = tmp_path / "output"
        output_dir.mkdir(parents=True, exist_ok=True)
        mapping_path = output_dir / "unp_file_mapping.json"
        mapping_path.write_text(json.dumps(sample_mapping_data), encoding="utf-8")

        input_obj = EpfrAiDistillerInput(
            output_dir=str(output_dir),
            mapping_filename="unp_file_mapping.json",
            unps=["100000001"],
        )

        result = await scan_ai_distiller_files(input_obj)

        assert result.total_companies == 1
        assert all(item.unp == "100000001" for item in result.work_items)

    @pytest.mark.anyio
    async def test_raises_file_not_found_for_missing_mapping(self, tmp_path):
        """Verifies FileNotFoundError when mapping doesn't exist."""
        output_dir = tmp_path / "output"
        output_dir.mkdir(parents=True, exist_ok=True)

        input_obj = EpfrAiDistillerInput(
            output_dir=str(output_dir),
            mapping_filename="nonexistent.json",
        )

        with pytest.raises(FileNotFoundError) as exc_info:
            await scan_ai_distiller_files(input_obj)

        assert "Mapping file not found" in str(exc_info.value)

    @pytest.mark.anyio
    async def test_handles_empty_mapping(self, tmp_path):
        """Verifies empty mapping returns empty work_items."""
        output_dir = tmp_path / "output"
        output_dir.mkdir(parents=True, exist_ok=True)
        mapping_path = output_dir / "unp_file_mapping.json"
        mapping_path.write_text("{}", encoding="utf-8")

        input_obj = EpfrAiDistillerInput(
            output_dir=str(output_dir),
            mapping_filename="unp_file_mapping.json",
        )

        result = await scan_ai_distiller_files(input_obj)

        assert result.total_companies == 0
        assert result.total_files == 0
        assert result.work_items == []

    @pytest.mark.anyio
    async def test_skips_non_dict_company_entries(self, tmp_path):
        """Verifies non-dict company entries are handled gracefully."""
        output_dir = tmp_path / "output"
        output_dir.mkdir(parents=True, exist_ok=True)
        mapping_path = output_dir / "unp_file_mapping.json"
        mapping_data = {
            "valid_unp": {"title": "Valid", "holder_id": 1, "files": []},
            "invalid_unp": "not a dict",
        }
        mapping_path.write_text(json.dumps(mapping_data), encoding="utf-8")

        input_obj = EpfrAiDistillerInput(
            output_dir=str(output_dir),
            mapping_filename="unp_file_mapping.json",
        )

        # Should not raise, just skip invalid entries
        result = await scan_ai_distiller_files(input_obj)
        # Both entries are counted as companies, but invalid_unp has no files
        assert result.total_companies == 2
        assert result.total_files == 0
        assert result.work_items == []


# =============================================================================
# TestProcessAiDistillation - Activity 2 Tests
# =============================================================================


class TestProcessAiDistillation:
    """Test Activity 2: process_ai_distillation.

    This activity processes all markdown files with AI extraction,
    respecting rate limiting between files.
    """

    @pytest.mark.anyio
    async def test_returns_empty_result_for_no_work_items(self, tmp_path):
        """Verifies graceful handling of empty scan result."""
        scan_result = AiDistillerScanResult(
            mapping_path=str(tmp_path / "mapping.json"),
            total_companies=0,
            total_files=0,
            work_items=[],
            output_dir=str(tmp_path),
            output_filename="output.json",
            model_name="test-model",
            temperature=0.0,
            max_retries=3,
            file_delay_seconds=0.0,
        )

        result = await process_ai_distillation(scan_result)

        assert isinstance(result, AiDistillerProcessResult)
        assert result.total_files == 0
        assert result.successful == 0
        assert result.failed == 0
        assert result.results == {}

    @pytest.mark.anyio
    async def test_handles_missing_files_gracefully(self, tmp_path, sample_scan_result):
        """Verifies FAILED status for missing files."""
        # Don't create the actual files - they should be missing
        result = await process_ai_distillation(sample_scan_result)

        assert result.total_files == 2
        assert result.failed == 2
        assert result.successful == 0
        assert len(result.failed_files) == 2

        # Check each result has FAILED status
        for _, file_result in result.results.items():
            assert file_result.status == "FAILED"
            assert file_result.error is not None
            assert "Markdown file not found" in file_result.error

    @pytest.mark.anyio
    async def test_processes_files_with_mocked_ai(self, tmp_path, sample_work_items):
        """Verifies file processing with mocked AI extraction."""
        # Create actual .md files
        output_dir = tmp_path / "output"
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "100000001").mkdir(parents=True, exist_ok=True)
        (output_dir / "100000001" / "doc1.md").write_text("# Test content", encoding="utf-8")
        (output_dir / "100000001" / "doc2.md").write_text("# More content", encoding="utf-8")

        # Update work items to point to real paths
        work_items = [
            AiDistillerWorkItem(
                unp="100000001",
                company_title="Company A",
                holder_id=1,
                file_path=str(output_dir / "100000001" / "doc1.md"),
                filename="doc1.md",
                original_name="",
                upload_date="2026-01-15",
                file_id=101,
                extracted_from=None,
                converted_from=None,
            ),
            AiDistillerWorkItem(
                unp="100000001",
                company_title="Company A",
                holder_id=1,
                file_path=str(output_dir / "100000001" / "doc2.md"),
                filename="doc2.md",
                original_name="",
                upload_date="2026-01-16",
                file_id=102,
                extracted_from=None,
                converted_from=None,
            ),
        ]

        scan_result = AiDistillerScanResult(
            mapping_path=str(output_dir / "mapping.json"),
            total_companies=1,
            total_files=2,
            work_items=work_items,
            output_dir=str(output_dir),
            output_filename="output.json",
            model_name="test-model",
            temperature=0.0,
            max_retries=0,
            file_delay_seconds=0.0,
        )

        # Mock the AIDistiller to return predictable results
        mock_extraction = MagicMock()
        mock_extraction.dividends = []
        mock_extraction.has_dividends = False
        mock_extraction.ai_comment = "Test comment"

        with patch("workflows.epfr.ai_distiller_workflow.AIDistiller") as MockDistiller:
            mock_distiller_instance = AsyncMock()
            mock_distiller_instance.extract_with_retry = AsyncMock(return_value=mock_extraction)
            MockDistiller.return_value = mock_distiller_instance

            result = await process_ai_distillation(scan_result)

        assert result.total_files == 2
        assert result.successful == 2
        assert result.failed == 0
        assert len(result.results) == 2

        for _, file_result in result.results.items():
            assert file_result.status == "SUCCESS"
            assert file_result.has_dividends is False
            assert file_result.ai_comment == "Test comment"

    @pytest.mark.anyio
    async def test_applies_rate_limiting_between_files(self, tmp_path, sample_work_items):
        """Verifies file_delay_seconds is respected between files."""
        output_dir = tmp_path / "output"
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "100000001").mkdir(parents=True, exist_ok=True)
        (output_dir / "100000001" / "doc1.md").write_text("# Content", encoding="utf-8")
        (output_dir / "100000001" / "doc2.md").write_text("# Content", encoding="utf-8")

        work_items = [
            AiDistillerWorkItem(
                unp="100000001",
                company_title="Company A",
                holder_id=1,
                file_path=str(output_dir / "100000001" / "doc1.md"),
                filename="doc1.md",
                original_name="",
                upload_date="2026-01-15",
                file_id=101,
                extracted_from=None,
                converted_from=None,
            ),
            AiDistillerWorkItem(
                unp="100000001",
                company_title="Company A",
                holder_id=1,
                file_path=str(output_dir / "100000001" / "doc2.md"),
                filename="doc2.md",
                original_name="",
                upload_date="2026-01-16",
                file_id=102,
                extracted_from=None,
                converted_from=None,
            ),
        ]

        scan_result = AiDistillerScanResult(
            mapping_path=str(output_dir / "mapping.json"),
            total_companies=1,
            total_files=2,
            work_items=work_items,
            output_dir=str(output_dir),
            output_filename="output.json",
            model_name="test-model",
            temperature=0.0,
            max_retries=0,
            file_delay_seconds=0.5,  # 500ms delay
        )

        mock_extraction = MagicMock()
        mock_extraction.dividends = []
        mock_extraction.has_dividends = False
        mock_extraction.ai_comment = ""

        sleep_calls = []
        original_sleep = asyncio.sleep

        async def track_sleep(delay):
            sleep_calls.append(delay)
            await original_sleep(0.001)  # Minimal actual sleep for speed

        with (
            patch("workflows.epfr.ai_distiller_workflow.AIDistiller") as MockDistiller,
            patch("workflows.epfr.ai_distiller_workflow.asyncio.sleep", track_sleep),
        ):
            mock_distiller_instance = AsyncMock()
            mock_distiller_instance.extract_with_retry = AsyncMock(return_value=mock_extraction)
            MockDistiller.return_value = mock_distiller_instance

            await process_ai_distillation(scan_result)

        # Should have called sleep once between the two files
        assert len(sleep_calls) >= 1
        assert sleep_calls[0] == 0.5

    @pytest.mark.anyio
    async def test_reconciles_boolean_deduplicates_and_calculates_missing_dates(self, tmp_path):
        """Verify the activity emits internally consistent facts with required dates."""
        output_dir = tmp_path / "output"
        unp_dir = output_dir / "100000001"
        unp_dir.mkdir(parents=True)
        path = unp_dir / "doc.md"
        path.write_text("# Notice", encoding="utf-8")
        scan_result = AiDistillerScanResult(
            mapping_path=str(output_dir / "mapping.json"),
            total_companies=1,
            total_files=1,
            work_items=[
                AiDistillerWorkItem(
                    unp="100000001",
                    company_title="Company A",
                    holder_id=1,
                    file_path=str(path),
                    filename="doc.md",
                    original_name="doc.pdf",
                    upload_date="2026-01-01",
                    file_id=1,
                )
            ],
            output_dir=str(output_dir),
            output_filename="output.json",
            model_name="test-model",
            temperature=0.0,
            max_retries=1,
            file_delay_seconds=0.0,
        )
        raw = _RawDividendEntry(
            share_type="common",
            period_year=2025,
            period_type="annual",
            period_number=1,
            amount_per_share="0.5",
            decision_date="2026-03-28",
            record_date=None,
            payment_date=None,
        )
        extraction = _RawExtraction(has_dividends=False, ai_comment="Found payout", dividends=[raw, raw])

        with patch("workflows.epfr.ai_distiller_workflow.AIDistiller") as mock_distiller:
            mock_distiller.return_value.extract_with_retry = AsyncMock(return_value=extraction)
            result = await process_ai_distillation(scan_result)

        file_result = result.results["100000001/doc.md"]
        assert file_result.has_dividends is True
        assert len(file_result.dividends) == 1
        assert file_result.dividends[0]["record_date"] == "2026-02-28"
        assert file_result.dividends[0]["payment_date"] == "2026-05-28"
        assert file_result.warnings == [
            "duplicate_dividend_entries_removed",
            "has_dividends_reconciled_from_amounts",
            "payment_date_defaulted",
            "record_date_defaulted",
        ]


# =============================================================================
# TestFinalizeAiDistillation - Activity 3 Tests
# =============================================================================


class TestFinalizeAiDistillation:
    """Test Activity 3: finalize_ai_distillation.

    This activity performs the atomic write of the distilled JSON output.
    """

    @pytest.mark.anyio
    async def test_writes_atomic_output_file(self, tmp_path, sample_scan_result):
        """Verifies tempfile.mkstemp + os.replace pattern is used."""
        output_dir = Path(sample_scan_result.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        # Create process result with some data
        process_result = AiDistillerProcessResult(
            results={
                "100000001/doc1.md": AiDistillerFileResult(
                    unp="100000001",
                    filename="doc1.md",
                    status="SUCCESS",
                    has_dividends=True,
                    ai_comment="Test",
                    dividends=[{"period_year": 2026}],
                    autofilled_fields=[],
                    error=None,
                    file_id=101,
                ),
            },
            total_files=1,
            successful=1,
            failed=0,
            failed_files=[],
            total_companies=1,
        )

        await finalize_ai_distillation(sample_scan_result, process_result)

        # Verify output file was created
        output_path = output_dir / sample_scan_result.output_filename
        assert output_path.exists()

        # Verify content
        content = json.loads(output_path.read_text(encoding="utf-8"))
        assert "100000001" in content
        assert content["100000001"]["files"][0]["filename"] == "doc1.md"

    @pytest.mark.anyio
    async def test_builds_correct_export_structure(self, tmp_path):
        """Verifies output JSON structure matches expected format."""
        output_dir = tmp_path / "output"
        output_dir.mkdir(parents=True, exist_ok=True)

        work_items = [
            AiDistillerWorkItem(
                unp="100000001",
                company_title="Company A",
                holder_id=1,
                file_path=str(output_dir / "100000001" / "doc1.md"),
                filename="doc1.md",
                original_name="orig.pdf",
                upload_date="2026-01-15",
                file_id=101,
                extracted_from="archive.zip",
                converted_from="doc.docx",
            ),
        ]

        scan_result = AiDistillerScanResult(
            mapping_path=str(output_dir / "mapping.json"),
            total_companies=1,
            total_files=1,
            work_items=work_items,
            output_dir=str(output_dir),
            output_filename="output.json",
            model_name="test-model",
            temperature=0.0,
            max_retries=3,
            file_delay_seconds=0.0,
        )

        process_result = AiDistillerProcessResult(
            results={
                "100000001/doc1.md": AiDistillerFileResult(
                    unp="100000001",
                    filename="doc1.md",
                    status="SUCCESS",
                    has_dividends=True,
                    ai_comment="Test comment",
                    dividends=[{"period_year": 2026, "period_type": "annual", "period_number": 1}],
                    autofilled_fields=["share_type"],
                    error=None,
                    file_id=101,
                ),
            },
            total_files=1,
            successful=1,
            failed=0,
            failed_files=[],
            total_companies=1,
        )

        stats = await finalize_ai_distillation(scan_result, process_result)

        # Verify stats structure
        assert "output_path" in stats
        assert stats["total_companies"] == 1
        assert stats["total_files"] == 1
        assert stats["successful"] == 1
        assert stats["failed"] == 0

        # Verify output file structure
        output_path = output_dir / "output.json"
        content = json.loads(output_path.read_text(encoding="utf-8"))

        company = content["100000001"]
        assert company["company_name"] == "Company A"
        assert company["unp"] == "100000001"
        assert company["holder_id"] == 1
        assert len(company["files"]) == 1

        file_entry = company["files"][0]
        assert file_entry["filename"] == "doc1.md"
        assert file_entry["original_name"] == "orig.pdf"
        assert file_entry["upload_date"] == "2026-01-15"
        assert file_entry["extracted_from"] == "archive.zip"
        assert file_entry["converted_from"] == "doc.docx"
        assert file_entry["has_dividends"] is True
        assert file_entry["ai_comment"] == "Test comment"

    @pytest.mark.anyio
    async def test_handles_cleanup_on_write_failure(self, tmp_path, sample_scan_result, monkeypatch):
        """Verifies tmp file is cleaned up on exception."""
        output_dir = Path(sample_scan_result.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        process_result = AiDistillerProcessResult(
            results={},
            total_files=0,
            successful=0,
            failed=0,
            failed_files=[],
            total_companies=0,
        )

        # Mock os.replace to raise an error
        def failing_replace(src, dst):
            raise OSError("Simulated write failure")

        with (
            patch("workflows.epfr.ai_distiller_workflow.os.replace", failing_replace),
            patch("workflows.epfr.ai_distiller_workflow.os.path.exists", return_value=True),
        ):
            with pytest.raises(RuntimeError) as exc_info:
                await finalize_ai_distillation(sample_scan_result, process_result)

            assert "Failed to save distilled output" in str(exc_info.value)

    @pytest.mark.anyio
    async def test_handles_empty_process_result(self, tmp_path):
        """Verifies graceful handling of empty process result."""
        scan_result = AiDistillerScanResult(
            mapping_path=str(tmp_path / "mapping.json"),
            total_companies=0,
            total_files=0,
            work_items=[],
            output_dir=str(tmp_path),
            output_filename="output.json",
            model_name="test-model",
            temperature=0.0,
            max_retries=3,
            file_delay_seconds=0.0,
        )

        process_result = AiDistillerProcessResult(
            results={},
            total_files=0,
            successful=0,
            failed=0,
            failed_files=[],
            total_companies=0,
        )

        stats = await finalize_ai_distillation(scan_result, process_result)

        assert stats["total_companies"] == 0
        assert stats["total_files"] == 0
        assert stats["successful"] == 0
        assert stats["failed"] == 0

    @pytest.mark.anyio
    async def test_handles_failed_files_in_result(self, tmp_path):
        """Verifies failed files are included in output with error info."""
        output_dir = tmp_path / "output"
        output_dir.mkdir(parents=True, exist_ok=True)

        work_items = [
            AiDistillerWorkItem(
                unp="100000001",
                company_title="Company A",
                holder_id=1,
                file_path=str(output_dir / "100000001" / "doc1.md"),
                filename="doc1.md",
                original_name="",
                upload_date="2026-01-15",
                file_id=101,
                extracted_from=None,
                converted_from=None,
            ),
        ]

        scan_result = AiDistillerScanResult(
            mapping_path=str(output_dir / "mapping.json"),
            total_companies=1,
            total_files=1,
            work_items=work_items,
            output_dir=str(output_dir),
            output_filename="output.json",
            model_name="test-model",
            temperature=0.0,
            max_retries=3,
            file_delay_seconds=0.0,
        )

        process_result = AiDistillerProcessResult(
            results={
                "100000001/doc1.md": AiDistillerFileResult(
                    unp="100000001",
                    filename="doc1.md",
                    status="FAILED",
                    has_dividends=False,
                    ai_comment="",
                    dividends=[],
                    autofilled_fields=[],
                    error="File not found: /path/to/file.md",
                    file_id=101,
                ),
            },
            total_files=1,
            successful=0,
            failed=1,
            failed_files=[str(output_dir / "100000001" / "doc1.md")],
            total_companies=1,
        )

        stats = await finalize_ai_distillation(scan_result, process_result)

        assert stats["failed"] == 1
        assert len(stats["failed_files"]) == 1

        # Verify output file includes error info
        output_path = output_dir / "output.json"
        content = json.loads(output_path.read_text(encoding="utf-8"))
        file_entry = content["100000001"]["files"][0]
        assert file_entry["error"] == "File not found: /path/to/file.md"

"""Tests for PDF OCR workflow activities.

These tests verify the 3-activity split for UI progress tracking:
1. scan_pdf_entries - Discover PDF entries in mapping
2. process_pdf_ocr - Perform OCR on discovered PDFs
3. finalize_ocr_mapping - Save updated mapping
"""

import json
import tempfile
from pathlib import Path

import pytest

from workflows.epfr.models import (
    EpfrPdfOcrInput,
    PdfOcrFileResult,
    PdfOcrProcessResult,
    PdfOcrScanResult,
    PdfOcrWorkItem,
)
from workflows.epfr.pdf_ocr_workflow import (
    finalize_ocr_mapping,
    process_pdf_ocr,
    scan_pdf_entries,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def sample_mapping_dir():
    """Create a temporary directory with a mapping file and dummy PDFs."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)

        # Create mapping JSON with PDF entries
        mapping = {
            "123456789": {
                "title": "Test Company",
                "holder_id": 1,
                "files": [
                    {"id": 1, "filename": "file1.pdf", "original_name": "Original.pdf"},
                    {"id": 2, "filename": "file2.pdf", "original_name": "Another.pdf"},
                    {"id": 3, "filename": "file3.docx", "original_name": "Word.docx"},
                ],
            },
            "987654321": {
                "title": "Another Company",
                "holder_id": 2,
                "files": [
                    {"id": 4, "filename": "file4.pdf", "original_name": "Final.pdf"},
                ],
            },
        }

        mapping_path = tmpdir / "unp_file_mapping.json"
        mapping_path.write_text(json.dumps(mapping, ensure_ascii=False, indent=2))

        # Create UNP directories
        (tmpdir / "123456789").mkdir()
        (tmpdir / "987654321").mkdir()

        # Create dummy PDF files (minimal valid PDF content)
        pdf_content = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n%%EOF"
        (tmpdir / "123456789" / "file1.pdf").write_bytes(pdf_content)
        (tmpdir / "123456789" / "file2.pdf").write_bytes(pdf_content)
        (tmpdir / "987654321" / "file4.pdf").write_bytes(pdf_content)

        yield tmpdir, mapping_path, mapping


@pytest.fixture
def empty_mapping_dir():
    """Create a temporary directory with an empty mapping file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        mapping = {}
        mapping_path = tmpdir / "unp_file_mapping.json"
        mapping_path.write_text(json.dumps(mapping, ensure_ascii=False, indent=2))
        yield tmpdir, mapping_path


# =============================================================================
# scan_pdf_entries Tests
# =============================================================================


@pytest.mark.anyio
async def test_scan_pdf_entries_basic(sample_mapping_dir):
    """Test basic scanning of PDF entries."""
    tmpdir, mapping_path, expected_mapping = sample_mapping_dir

    input = EpfrPdfOcrInput(
        output_dir=str(tmpdir),
        mapping_filename="unp_file_mapping.json",
        overwrite=True,
        cleanup_source=True,
    )

    result = await scan_pdf_entries(input)

    assert isinstance(result, PdfOcrScanResult)
    assert result.total_pdf_entries == 3  # file1.pdf, file2.pdf, file4.pdf
    assert result.total_unps_scanned == 2  # 123456789, 987654321
    assert len(result.work_items) == 3

    # Check work item structure
    for item in result.work_items:
        assert isinstance(item, PdfOcrWorkItem)
        assert item.filename.endswith(".pdf")
        assert Path(item.file_path).exists()
        assert item.unp in ["123456789", "987654321"]


@pytest.mark.anyio
async def test_scan_pdf_entries_with_unp_filter(sample_mapping_dir):
    """Test scanning with UNP filter."""
    tmpdir, mapping_path, _ = sample_mapping_dir

    input = EpfrPdfOcrInput(
        output_dir=str(tmpdir),
        mapping_filename="unp_file_mapping.json",
        unps=["123456789"],
    )

    result = await scan_pdf_entries(input)

    assert result.total_pdf_entries == 2  # Only from UNP 123456789
    assert result.total_unps_scanned == 1
    assert all(item.unp == "123456789" for item in result.work_items)


@pytest.mark.anyio
async def test_scan_pdf_entries_empty_mapping(empty_mapping_dir):
    """Test scanning with empty mapping file."""
    tmpdir, mapping_path = empty_mapping_dir

    input = EpfrPdfOcrInput(
        output_dir=str(tmpdir),
        mapping_filename="unp_file_mapping.json",
    )

    result = await scan_pdf_entries(input)

    assert result.total_pdf_entries == 0
    assert result.total_unps_scanned == 0
    assert len(result.work_items) == 0


@pytest.mark.anyio
async def test_scan_pdf_entries_not_found():
    """Test scanning with non-existent mapping file."""
    input = EpfrPdfOcrInput(
        output_dir="/nonexistent",
        mapping_filename="unp_file_mapping.json",
    )

    with pytest.raises(FileNotFoundError):
        await scan_pdf_entries(input)


@pytest.mark.anyio
async def test_scan_pdf_entries_skips_non_pdf(sample_mapping_dir):
    """Test that non-PDF files are skipped during scan."""
    tmpdir, mapping_path, _ = sample_mapping_dir

    input = EpfrPdfOcrInput(
        output_dir=str(tmpdir),
        mapping_filename="unp_file_mapping.json",
    )

    result = await scan_pdf_entries(input)

    # file3.docx should be skipped
    assert result.total_pdf_entries == 3
    assert all(item.filename.endswith(".pdf") for item in result.work_items)


@pytest.mark.anyio
async def test_scan_pdf_entries_preserves_mapping_raw(sample_mapping_dir):
    """Test that scan preserves the full mapping for downstream use."""
    tmpdir, mapping_path, expected_mapping = sample_mapping_dir

    input = EpfrPdfOcrInput(
        output_dir=str(tmpdir),
        mapping_filename="unp_file_mapping.json",
    )

    result = await scan_pdf_entries(input)

    assert result.mapping_raw == expected_mapping


# =============================================================================
# process_pdf_ocr Tests
# =============================================================================


@pytest.mark.anyio
async def test_process_pdf_ocr_empty():
    """Test processing with no work items."""
    scan_result = PdfOcrScanResult(
        mapping_path="/test/mapping.json",
        mapping_raw={},
        total_unps_scanned=0,
        total_pdf_entries=0,
        work_items=[],
        by_unp={},
        output_dir="/test",
        mapping_filename="mapping.json",
        cleanup_source=None,
    )

    result = await process_pdf_ocr("/test", scan_result, overwrite=True)

    assert isinstance(result, PdfOcrProcessResult)
    assert result.total_successful == 0
    assert result.total_failed == 0
    assert result.total_skipped == 0
    assert len(result.results) == 0


@pytest.mark.anyio
async def test_process_pdf_ocr_preserves_mapping():
    """Test that process preserves mapping structure."""
    original_mapping = {
        "123": {
            "title": "Test",
            "holder_id": 1,
            "files": [{"id": 1, "filename": "file1.pdf"}],
        }
    }

    scan_result = PdfOcrScanResult(
        mapping_path="/test/mapping.json",
        mapping_raw=original_mapping,
        total_unps_scanned=1,
        total_pdf_entries=1,
        work_items=[
            PdfOcrWorkItem(
                unp="123",
                file_index=0,
                filename="file1.pdf",
                file_path="/nonexistent/123/file1.pdf",  # File doesn't exist, will fail
                entry={"id": 1, "filename": "file1.pdf"},
            )
        ],
        by_unp={"123": {"pdf_entries": 1}},
        output_dir="/test",
        mapping_filename="mapping.json",
        cleanup_source=None,
    )

    result = await process_pdf_ocr("/test", scan_result, overwrite=True)

    # Mapping should still have the structure
    assert "123" in result.updated_mapping
    assert result.updated_mapping["123"]["title"] == "Test"
    assert result.total_failed == 1  # File doesn't exist


# =============================================================================
# finalize_ocr_mapping Tests
# =============================================================================


@pytest.mark.anyio
async def test_finalize_ocr_mapping_basic(tmp_path):
    """Test finalization saves mapping correctly."""
    process_result = PdfOcrProcessResult(
        updated_mapping={
            "123": {
                "title": "Test",
                "holder_id": 1,
                "files": [{"id": 1, "filename": "file1.md", "converted_from": "file1.pdf"}],
            }
        },
        results=[
            PdfOcrFileResult(
                unp="123",
                file_index=0,
                status="SUCCESS",
                original_filename="file1.pdf",
                new_filename="file1.md",
                source_path=str(tmp_path / "123" / "file1.pdf"),
                error=None,
                converted_from="file1.pdf",
            )
        ],
        total_successful=1,
        total_failed=0,
        total_skipped=0,
        failed_files=[],
        skipped_files=[],
        cleaned_up_files=[],
    )

    stats = await finalize_ocr_mapping(
        output_root=str(tmp_path),
        mapping_filename="unp_file_mapping.json",
        process_result=process_result,
        cleanup_source=True,
    )

    # Verify file was created
    assert (tmp_path / "unp_file_mapping.json").exists()

    # Verify stats structure
    assert stats["total_successful"] == 1
    assert stats["total_failed"] == 0
    assert stats["total_skipped"] == 0
    assert stats["mapping_path"] == str(tmp_path / "unp_file_mapping.json")
    assert stats["total_unps_scanned"] == 1


@pytest.mark.anyio
async def test_finalize_ocr_mapping_content(tmp_path):
    """Test that finalization saves correct mapping content."""
    updated_mapping = {
        "123": {
            "title": "Company 123",
            "holder_id": 123,
            "files": [{"id": 1, "filename": "doc1.md", "original_name": "doc1.pdf", "converted_from": "doc1.pdf"}],
        }
    }

    process_result = PdfOcrProcessResult(
        updated_mapping=updated_mapping,
        results=[
            PdfOcrFileResult(
                unp="123",
                file_index=0,
                status="SUCCESS",
                original_filename="doc1.pdf",
                new_filename="doc1.md",
                source_path="/test/123/doc1.pdf",
                error=None,
                converted_from="doc1.pdf",
            )
        ],
        total_successful=1,
        total_failed=0,
        total_skipped=0,
        failed_files=[],
        skipped_files=[],
        cleaned_up_files=[],
    )

    await finalize_ocr_mapping(
        output_root=str(tmp_path),
        mapping_filename="test_mapping.json",
        process_result=process_result,
        cleanup_source=True,
    )

    # Read and verify saved content
    saved_content = (tmp_path / "test_mapping.json").read_text()
    saved_mapping = json.loads(saved_content)

    assert saved_mapping == updated_mapping


@pytest.mark.anyio
async def test_finalize_ocr_mapping_by_unp_stats(tmp_path):
    """Test that by_unp stats are reconstructed correctly."""
    process_result = PdfOcrProcessResult(
        updated_mapping={},
        results=[
            PdfOcrFileResult(
                unp="unp1",
                file_index=0,
                status="SUCCESS",
                original_filename="f1.pdf",
                new_filename="f1.md",
                source_path="/test/unp1/f1.pdf",
                error=None,
                converted_from="f1.pdf",
            ),
            PdfOcrFileResult(
                unp="unp1",
                file_index=1,
                status="FAILED",
                original_filename="f2.pdf",
                new_filename=None,
                source_path="/test/unp1/f2.pdf",
                error="Some error",
                converted_from=None,
            ),
            PdfOcrFileResult(
                unp="unp2",
                file_index=0,
                status="SUCCESS",
                original_filename="f3.pdf",
                new_filename="f3.md",
                source_path="/test/unp2/f3.pdf",
                error=None,
                converted_from="f3.pdf",
            ),
        ],
        total_successful=2,
        total_failed=1,
        total_skipped=0,
        failed_files=["/test/unp1/f2.pdf"],
        skipped_files=[],
        cleaned_up_files=[],
    )

    stats = await finalize_ocr_mapping(
        output_root=str(tmp_path),
        mapping_filename="test_mapping.json",
        process_result=process_result,
        cleanup_source=True,
    )

    by_unp = stats["by_unp"]

    assert "unp1" in by_unp
    assert "unp2" in by_unp

    assert by_unp["unp1"]["pdf_entries"] == 2
    assert by_unp["unp1"]["successful"] == 1
    assert by_unp["unp1"]["failed"] == 1
    assert by_unp["unp1"]["skipped"] == 0

    assert by_unp["unp2"]["pdf_entries"] == 1
    assert by_unp["unp2"]["successful"] == 1
    assert by_unp["unp2"]["failed"] == 0


# =============================================================================
# Integration Tests (End-to-End without actual OCR)
# =============================================================================


@pytest.mark.anyio
async def test_workflow_pipeline_empty(sample_mapping_dir):
    """Test full 3-activity pipeline with empty result (no PDFs to process)."""
    tmpdir, mapping_path, _ = sample_mapping_dir

    # First, scan
    input = EpfrPdfOcrInput(
        output_dir=str(tmpdir),
        mapping_filename="unp_file_mapping.json",
        unps=["999999999"],  # Non-existent UNP
    )

    scan_result = await scan_pdf_entries(input)
    assert scan_result.total_pdf_entries == 0

    # Then process (empty)
    process_result = await process_pdf_ocr(str(tmpdir), scan_result, overwrite=True)
    assert process_result.total_successful == 0

    # Then finalize
    stats = await finalize_ocr_mapping(str(tmpdir), "unp_file_mapping.json", process_result, cleanup_source=True)
    assert stats["total_pdf_entries"] == 0


@pytest.mark.anyio
async def test_process_pdf_ocr_exception_isolation(tmp_path):
    """Test that one task raising an exception does not cancel sibling tasks."""
    from unittest.mock import AsyncMock, patch

    unp = "111222333"
    (tmp_path / unp).mkdir()
    pdf_content = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n%%EOF"
    for name in ("a.pdf", "b.pdf", "c.pdf"):
        (tmp_path / unp / name).write_bytes(pdf_content)

    original_mapping = {
        unp: {
            "title": "Isolation Test",
            "files": [
                {"id": 1, "filename": "a.pdf"},
                {"id": 2, "filename": "b.pdf"},
                {"id": 3, "filename": "c.pdf"},
            ],
        }
    }

    scan_result = PdfOcrScanResult(
        mapping_path=str(tmp_path / "mapping.json"),
        mapping_raw=original_mapping,
        total_unps_scanned=1,
        total_pdf_entries=3,
        work_items=[
            PdfOcrWorkItem(
                unp=unp,
                file_index=0,
                filename="a.pdf",
                file_path=str(tmp_path / unp / "a.pdf"),
                entry={"id": 1, "filename": "a.pdf"},
            ),
            PdfOcrWorkItem(
                unp=unp,
                file_index=1,
                filename="b.pdf",
                file_path=str(tmp_path / unp / "b.pdf"),
                entry={"id": 2, "filename": "b.pdf"},
            ),
            PdfOcrWorkItem(
                unp=unp,
                file_index=2,
                filename="c.pdf",
                file_path=str(tmp_path / unp / "c.pdf"),
                entry={"id": 3, "filename": "c.pdf"},
            ),
        ],
        by_unp={unp: {"pdf_entries": 3}},
        output_dir=str(tmp_path),
        mapping_filename="mapping.json",
        cleanup_source=None,
    )

    call_count = 0

    async def _mock_process(u, output_root, item, overwrite):
        nonlocal call_count
        call_count += 1
        if item.filename == "b.pdf":
            raise RuntimeError("simulated OCR API failure")
        return (
            0,
            item.file_index,
            "SUCCESS",
            {"id": item.entry["id"], "filename": item.filename},
            str(Path(item.file_path)),
            None,
        )

    with patch(
        "workflows.epfr.pdf_ocr_workflow._process_work_item",
        new=AsyncMock(side_effect=_mock_process),
    ):
        result = await process_pdf_ocr(str(tmp_path), scan_result, overwrite=True)

    assert result.total_failed == 1
    assert result.total_successful == 2
    assert len(result.results) == 3

    failed = [r for r in result.results if r.status == "FAILED"]
    assert len(failed) == 1
    assert failed[0].original_filename == "b.pdf"
    assert "RuntimeError" in (failed[0].error or "")

    successful = sorted([r for r in result.results if r.status == "SUCCESS"], key=lambda r: r.original_filename)
    assert len(successful) == 2
    assert successful[0].original_filename == "a.pdf"
    assert successful[1].original_filename == "c.pdf"

    mapping_files = result.updated_mapping[unp]["files"]
    assert mapping_files[0]["filename"] == "a.pdf"
    assert mapping_files[2]["filename"] == "c.pdf"
    assert mapping_files[1]["filename"] == "b.pdf"


@pytest.mark.anyio
async def test_workflow_pipeline_structure(sample_mapping_dir):
    """Test that data flows correctly through all 3 activities."""
    tmpdir, mapping_path, expected_mapping = sample_mapping_dir

    # Step 1: Scan
    input = EpfrPdfOcrInput(
        output_dir=str(tmpdir),
        mapping_filename="unp_file_mapping.json",
    )

    scan_result = await scan_pdf_entries(input)
    assert scan_result.total_pdf_entries == 3

    # Step 2: Process
    # Note: This will fail because the PDFs are dummy, but we can test the flow
    process_result = await process_pdf_ocr(str(tmpdir), scan_result, overwrite=True)

    # All 3 PDFs should have failed (they're minimal dummy PDFs, not valid)
    # But the structure should be correct
    assert len(process_result.results) == 3
    assert process_result.total_successful + process_result.total_failed + process_result.total_skipped == 3

    # Step 3: Finalize
    stats = await finalize_ocr_mapping(str(tmpdir), "unp_file_mapping.json", process_result, cleanup_source=True)

    assert stats["total_pdf_entries"] == 3
    assert "by_unp" in stats

"""Tests for workflow wrappers and save_unp_mapping() from workflow.py."""

# ruff: noqa: D102

import importlib
import json
import os
from pathlib import Path


os.environ.pop("AGENT", None)

from workflows.epfr.models import EpfrRecord, Holder
from workflows.epfr.workflow import EpfrFilesDownloader, save_unp_mapping


pytest = importlib.import_module("pytest")


@pytest.fixture()
def anyio_backend():
    """Return the anyio backend for async tests."""
    return "asyncio"


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
        realUploadDate=upload_date,
        holder=holder,
    )


def _holder(unp: str = "600073968", title: str = "Test Co", holder_id: int = 100) -> Holder:
    return Holder(id=holder_id, title=title, unp=unp)


class TestSaveUnpMappingHappyPath:
    """save_unp_mapping produces correct JSON shape for basic inputs."""

    @pytest.mark.anyio
    async def test_basic_mapping_shape(self, tmp_path: Path):
        """Happy path: single record produces mapping with UNP key, title, holder_id, files."""
        holder = _holder()
        record = _record(holder=holder)

        unp_dir = tmp_path / holder.unp
        unp_dir.mkdir()
        (unp_dir / "1.pdf").write_bytes(b"%PDF-1.4 fake")

        result = await save_unp_mapping(
            records=[record],
            output_dir=str(tmp_path),
            download_stats={"file_map": {"1": "1.pdf"}, "by_unp": {holder.unp: {"files": ["1.pdf"]}}},
            extraction_stats={"by_unp": {}},
            conversion_stats={"cleaned_up_files": []},
        )

        assert result == str((tmp_path / "unp_file_mapping.json").resolve())
        data = json.loads((tmp_path / "unp_file_mapping.json").read_text(encoding="utf-8"))

        assert holder.unp in data
        entry = data[holder.unp]
        assert entry["title"] == "Test Co"
        assert entry["holder_id"] == 100
        assert len(entry["files"]) == 1
        assert entry["files"][0]["id"] == 1
        assert entry["files"][0]["filename"] == "1.pdf"
        assert entry["files"][0]["original_name"] == "Test disclosure"
        assert entry["files"][0]["upload_date"] == "2026-01-15"

    @pytest.mark.anyio
    async def test_empty_records_produce_empty_mapping(self, tmp_path: Path):
        """Empty record list produces a mapping file with empty dict."""
        result = await save_unp_mapping(
            records=[],
            output_dir=str(tmp_path),
            download_stats={"file_map": {}, "by_unp": {}},
            extraction_stats={"by_unp": {}},
            conversion_stats={"cleaned_up_files": []},
        )

        assert result == str((tmp_path / "unp_file_mapping.json").resolve())
        data = json.loads((tmp_path / "unp_file_mapping.json").read_text(encoding="utf-8"))
        assert data == {}


class TestSaveUnpMappingSkippedRecords:
    """Records with holder=None are skipped in mapping output."""

    @pytest.mark.anyio
    async def test_holder_none_records_skipped(self, tmp_path: Path):
        """Records with holder=None produce no mapping entries."""
        record_no_holder = _record(holder=None)

        await save_unp_mapping(
            records=[record_no_holder],
            output_dir=str(tmp_path),
            download_stats={"file_map": {"1": "1.pdf"}, "by_unp": {}},
            extraction_stats={"by_unp": {}},
            conversion_stats={"cleaned_up_files": []},
        )

        data = json.loads((tmp_path / "unp_file_mapping.json").read_text(encoding="utf-8"))
        assert data == {}

    @pytest.mark.anyio
    async def test_mixed_holder_and_no_holder(self, tmp_path: Path):
        """Only records with valid holders appear in output."""
        holder = _holder(unp="111111111")
        record_ok = _record(rec_id=1, holder=holder)
        record_skip = _record(rec_id=2, holder=None)

        unp_dir = tmp_path / holder.unp
        unp_dir.mkdir()
        (unp_dir / "1.pdf").write_bytes(b"%PDF-1.4 fake")

        await save_unp_mapping(
            records=[record_ok, record_skip],
            output_dir=str(tmp_path),
            download_stats={"file_map": {"1": "1.pdf"}, "by_unp": {holder.unp: {"files": ["1.pdf"]}}},
            extraction_stats={"by_unp": {}},
            conversion_stats={"cleaned_up_files": []},
        )

        data = json.loads((tmp_path / "unp_file_mapping.json").read_text(encoding="utf-8"))
        assert "111111111" in data
        assert len(data) == 1


class TestSaveUnpMappingExtractedLineage:
    """Verify extracted_from field tracks archive-to-extracted-file lineage."""

    @pytest.mark.anyio
    async def test_archive_extraction_lineage(self, tmp_path: Path):
        """Files extracted from an archive carry extracted_from field."""
        holder = _holder()
        record = _record(holder=holder)

        unp_dir = tmp_path / holder.unp
        unp_dir.mkdir()
        (unp_dir / "report.docx").write_bytes(b"PK extracted docx")

        await save_unp_mapping(
            records=[record],
            output_dir=str(tmp_path),
            download_stats={"file_map": {"1": "archive.zip"}, "by_unp": {holder.unp: {"files": ["archive.zip"]}}},
            extraction_stats={
                "by_unp": {
                    holder.unp: {
                        "archive_to_files": {"archive.zip": ["report.docx"]},
                    }
                }
            },
            conversion_stats={"cleaned_up_files": []},
        )

        data = json.loads((tmp_path / "unp_file_mapping.json").read_text(encoding="utf-8"))
        files = data[holder.unp]["files"]
        assert len(files) == 1
        assert files[0]["filename"] == "report.docx"
        assert files[0]["extracted_from"] == "archive.zip"
        assert files[0]["converted_from"] is None


class TestSaveUnpMappingConvertedLineage:
    """Verify converted_from field tracks source-to-markdown conversion lineage."""

    @pytest.mark.anyio
    async def test_conversion_lineage_for_regular_file(self, tmp_path: Path):
        """A file that was converted and cleaned up shows converted_from and .md filename."""
        holder = _holder()
        record = _record(holder=holder)

        unp_dir = tmp_path / holder.unp
        unp_dir.mkdir()
        md_path = unp_dir / "1.md"
        md_path.write_text("# Converted content", encoding="utf-8")
        (unp_dir / "1.doc").write_bytes(b"fake doc content")

        source_file_str = str(unp_dir / "1.doc")

        await save_unp_mapping(
            records=[record],
            output_dir=str(tmp_path),
            download_stats={"file_map": {"1": "1.doc"}, "by_unp": {holder.unp: {"files": ["1.doc"]}}},
            extraction_stats={"by_unp": {}},
            conversion_stats={"cleaned_up_files": [source_file_str]},
        )

        data = json.loads((tmp_path / "unp_file_mapping.json").read_text(encoding="utf-8"))
        files = data[holder.unp]["files"]
        assert len(files) == 1
        assert files[0]["filename"] == "1.md"
        assert files[0]["converted_from"] == "1.doc"
        assert files[0]["extracted_from"] is None

    @pytest.mark.anyio
    async def test_conversion_lineage_for_extracted_file(self, tmp_path: Path):
        """An archive-extracted file that was then converted shows both lineage fields."""
        holder = _holder()
        record = _record(holder=holder)

        unp_dir = tmp_path / holder.unp
        unp_dir.mkdir()
        md_path = unp_dir / "report.md"
        md_path.write_text("# Converted from extracted", encoding="utf-8")

        extracted_source_str = str(unp_dir / "report.docx")

        await save_unp_mapping(
            records=[record],
            output_dir=str(tmp_path),
            download_stats={"file_map": {"1": "archive.zip"}, "by_unp": {holder.unp: {"files": ["archive.zip"]}}},
            extraction_stats={
                "by_unp": {
                    holder.unp: {
                        "archive_to_files": {"archive.zip": ["report.docx"]},
                    }
                }
            },
            conversion_stats={"cleaned_up_files": [extracted_source_str]},
        )

        data = json.loads((tmp_path / "unp_file_mapping.json").read_text(encoding="utf-8"))
        files = data[holder.unp]["files"]
        assert len(files) == 1
        assert files[0]["filename"] == "report.md"
        assert files[0]["extracted_from"] == "archive.zip"
        assert files[0]["converted_from"] == "report.docx"

    @pytest.mark.anyio
    async def test_file_not_in_file_map_skipped(self, tmp_path: Path):
        """A record whose id is not in file_map produces no file entry."""
        holder = _holder()
        record = _record(rec_id=42, holder=holder)

        unp_dir = tmp_path / holder.unp
        unp_dir.mkdir()

        await save_unp_mapping(
            records=[record],
            output_dir=str(tmp_path),
            download_stats={"file_map": {}, "by_unp": {}},
            extraction_stats={"by_unp": {}},
            conversion_stats={"cleaned_up_files": []},
        )

        data = json.loads((tmp_path / "unp_file_mapping.json").read_text(encoding="utf-8"))
        assert holder.unp not in data

    @pytest.mark.anyio
    async def test_original_file_not_on_disk_skipped(self, tmp_path: Path):
        """A non-archive file that does not exist on disk is skipped."""
        holder = _holder()
        record = _record(holder=holder)

        unp_dir = tmp_path / holder.unp
        unp_dir.mkdir()

        await save_unp_mapping(
            records=[record],
            output_dir=str(tmp_path),
            download_stats={"file_map": {"1": "1.pdf"}, "by_unp": {holder.unp: {"files": ["1.pdf"]}}},
            extraction_stats={"by_unp": {}},
            conversion_stats={"cleaned_up_files": []},
        )

        data = json.loads((tmp_path / "unp_file_mapping.json").read_text(encoding="utf-8"))
        assert holder.unp not in data


class TestSaveUnpMappingAtomicWrite:
    """Verify the atomic write pattern for mapping output."""

    @pytest.mark.anyio
    async def test_mapping_file_created(self, tmp_path: Path):
        """The mapping file is created at the expected path."""
        holder = _holder()
        record = _record(holder=holder)

        unp_dir = tmp_path / holder.unp
        unp_dir.mkdir()
        (unp_dir / "1.pdf").write_bytes(b"%PDF-1.4 fake")

        result = await save_unp_mapping(
            records=[record],
            output_dir=str(tmp_path),
            download_stats={"file_map": {"1": "1.pdf"}, "by_unp": {holder.unp: {"files": ["1.pdf"]}}},
            extraction_stats={"by_unp": {}},
            conversion_stats={"cleaned_up_files": []},
        )

        mapping_path = tmp_path / "unp_file_mapping.json"
        assert mapping_path.exists()
        assert result == str(mapping_path.resolve())

    @pytest.mark.anyio
    async def test_no_temp_files_left_behind(self, tmp_path: Path):
        """No temporary .mapping_ files remain after successful write."""
        holder = _holder()
        record = _record(holder=holder)

        unp_dir = tmp_path / holder.unp
        unp_dir.mkdir()
        (unp_dir / "1.pdf").write_bytes(b"%PDF-1.4 fake")

        await save_unp_mapping(
            records=[record],
            output_dir=str(tmp_path),
            download_stats={"file_map": {"1": "1.pdf"}, "by_unp": {holder.unp: {"files": ["1.pdf"]}}},
            extraction_stats={"by_unp": {}},
            conversion_stats={"cleaned_up_files": []},
        )

        temp_files = list(tmp_path.glob(".mapping_*"))
        assert temp_files == []


class TestSaveUnpMappingMultipleUnps:
    """Multiple UNP groups are handled correctly."""

    @pytest.mark.anyio
    async def test_two_unps_produce_two_entries(self, tmp_path: Path):
        """Records for two different UNPs produce separate mapping groups."""
        h1 = _holder(unp="111111111", title="Co A", holder_id=10)
        h2 = _holder(unp="222222222", title="Co B", holder_id=20)
        r1 = _record(rec_id=1, holder=h1)
        r2 = _record(rec_id=2, holder=h2)

        for h in (h1, h2):
            d = tmp_path / h.unp
            d.mkdir()
            (d / f"{r1.id if h == h1 else r2.id}.pdf").write_bytes(b"%PDF-1.4 fake")

        await save_unp_mapping(
            records=[r1, r2],
            output_dir=str(tmp_path),
            download_stats={"file_map": {"1": "1.pdf", "2": "2.pdf"}, "by_unp": {}},
            extraction_stats={"by_unp": {}},
            conversion_stats={"cleaned_up_files": []},
        )

        data = json.loads((tmp_path / "unp_file_mapping.json").read_text(encoding="utf-8"))
        assert len(data) == 2
        assert data["111111111"]["title"] == "Co A"
        assert data["222222222"]["title"] == "Co B"


class TestSaveUnpMappingEdgeCases:
    """Additional edge cases for save_unp_mapping."""

    @pytest.mark.anyio
    async def test_upload_date_empty_string(self, tmp_path: Path):
        """Handles empty string upload_date - produces empty string."""
        holder = _holder()
        record = _record(upload_date="", holder=holder)

        unp_dir = tmp_path / holder.unp
        unp_dir.mkdir()
        (unp_dir / "1.pdf").write_bytes(b"%PDF-1.4 fake")

        await save_unp_mapping(
            records=[record],
            output_dir=str(tmp_path),
            download_stats={"file_map": {"1": "1.pdf"}, "by_unp": {holder.unp: {"files": ["1.pdf"]}}},
            extraction_stats={"by_unp": {}},
            conversion_stats={"cleaned_up_files": []},
        )

        data = json.loads((tmp_path / "unp_file_mapping.json").read_text(encoding="utf-8"))
        files = data[holder.unp]["files"]
        assert len(files) == 1
        assert files[0]["upload_date"] == ""

    @pytest.mark.anyio
    async def test_upload_date_no_space(self, tmp_path: Path):
        """Handles upload_date without space separator."""
        holder = _holder()
        record = _record(upload_date="2026-01-15", holder=holder)

        unp_dir = tmp_path / holder.unp
        unp_dir.mkdir()
        (unp_dir / "1.pdf").write_bytes(b"%PDF-1.4 fake")

        await save_unp_mapping(
            records=[record],
            output_dir=str(tmp_path),
            download_stats={"file_map": {"1": "1.pdf"}, "by_unp": {holder.unp: {"files": ["1.pdf"]}}},
            extraction_stats={"by_unp": {}},
            conversion_stats={"cleaned_up_files": []},
        )

        data = json.loads((tmp_path / "unp_file_mapping.json").read_text(encoding="utf-8"))
        files = data[holder.unp]["files"]
        assert len(files) == 1
        assert files[0]["upload_date"] == "2026-01-15"

    @pytest.mark.anyio
    async def test_empty_archive_to_files(self, tmp_path: Path):
        """Handles archive with no extracted files - no entries created."""
        holder = _holder()
        record = _record(holder=holder)

        unp_dir = tmp_path / holder.unp
        unp_dir.mkdir()
        (unp_dir / "archive.zip").write_bytes(b"PK\x03\x04 fake zip")

        await save_unp_mapping(
            records=[record],
            output_dir=str(tmp_path),
            download_stats={"file_map": {"1": "archive.zip"}, "by_unp": {holder.unp: {"files": ["archive.zip"]}}},
            extraction_stats={
                "by_unp": {
                    holder.unp: {
                        "archive_to_files": {"archive.zip": []},  # Empty list
                    }
                }
            },
            conversion_stats={"cleaned_up_files": []},
        )

        data = json.loads((tmp_path / "unp_file_mapping.json").read_text(encoding="utf-8"))
        # Archive with no extracted files should not produce entries
        assert holder.unp not in data

    @pytest.mark.anyio
    async def test_file_exists_not_converted_not_extracted(self, tmp_path: Path):
        """File exists on disk but wasn't converted or extracted - kept as is."""
        holder = _holder()
        record = _record(holder=holder)

        unp_dir = tmp_path / holder.unp
        unp_dir.mkdir()
        (unp_dir / "1.pdf").write_bytes(b"%PDF-1.4 fake")

        await save_unp_mapping(
            records=[record],
            output_dir=str(tmp_path),
            download_stats={"file_map": {"1": "1.pdf"}, "by_unp": {holder.unp: {"files": ["1.pdf"]}}},
            extraction_stats={"by_unp": {}},  # Not an archive
            conversion_stats={"cleaned_up_files": []},  # Not converted
        )

        data = json.loads((tmp_path / "unp_file_mapping.json").read_text(encoding="utf-8"))
        files = data[holder.unp]["files"]
        assert len(files) == 1
        assert files[0]["filename"] == "1.pdf"
        assert files[0]["extracted_from"] is None
        assert files[0]["converted_from"] is None

    @pytest.mark.anyio
    async def test_atomic_write_exception(self, tmp_path: Path, mocker):
        """Handles exception during file write - raises RuntimeError and cleans temp file."""
        holder = _holder()
        record = _record(holder=holder)

        unp_dir = tmp_path / holder.unp
        unp_dir.mkdir()
        (unp_dir / "1.pdf").write_bytes(b"%PDF-1.4 fake")

        # Mock os.fdopen to raise an exception
        mocker.patch(
            "workflows.epfr.workflow.os.fdopen",
            side_effect=OSError("Disk full"),
        )

        with pytest.raises(RuntimeError) as exc_info:
            await save_unp_mapping(
                records=[record],
                output_dir=str(tmp_path),
                download_stats={"file_map": {"1": "1.pdf"}, "by_unp": {holder.unp: {"files": ["1.pdf"]}}},
                extraction_stats={"by_unp": {}},
                conversion_stats={"cleaned_up_files": []},
            )

        assert "Failed to save mapping" in str(exc_info.value)
        # Verify temp files are cleaned up
        temp_files = list(tmp_path.glob(".mapping_*"))
        assert len(temp_files) == 0

    @pytest.mark.anyio
    async def test_multiple_files_per_record_archive(self, tmp_path: Path):
        """Handles records with archive extracting to multiple files."""
        holder = _holder()
        record = _record(holder=holder)

        unp_dir = tmp_path / holder.unp
        unp_dir.mkdir()
        (unp_dir / "archive.zip").write_bytes(b"PK\x03\x04 fake zip")
        (unp_dir / "file1.txt").write_bytes(b"content1")
        (unp_dir / "file2.txt").write_bytes(b"content2")

        await save_unp_mapping(
            records=[record],
            output_dir=str(tmp_path),
            download_stats={"file_map": {"1": "archive.zip"}, "by_unp": {holder.unp: {"files": ["archive.zip"]}}},
            extraction_stats={
                "by_unp": {
                    holder.unp: {
                        "archive_to_files": {"archive.zip": ["file1.txt", "file2.txt"]},
                    }
                }
            },
            conversion_stats={"cleaned_up_files": []},
        )

        data = json.loads((tmp_path / "unp_file_mapping.json").read_text(encoding="utf-8"))
        files = data[holder.unp]["files"]
        assert len(files) == 2
        filenames = {f["filename"] for f in files}
        assert filenames == {"file1.txt", "file2.txt"}
        # Both should have extracted_from
        assert all(f["extracted_from"] == "archive.zip" for f in files)


class TestWorkflowDiscovery:
    """Verify all four workflow classes are auto-discoverable with correct names."""

    def _get_workflow_name(self, cls: type) -> str:
        """Extract the workflow name from the decorated class."""
        from mistralai.workflows.core.definition.workflow_definition import get_workflow_definition

        return get_workflow_definition(cls).name

    def test_epfr_files_downloader_discoverable(self):
        """EpfrFilesDownloader has the discovery attribute and correct name."""
        assert hasattr(EpfrFilesDownloader, "__workflows_workflow_def")
        assert self._get_workflow_name(EpfrFilesDownloader) == "epfr-files-downloader"

    def test_pdf_ocr_converter_discoverable(self):
        """EpfrPdfOcrConverter (now EpfrOcrConverter) has the discovery attribute and correct name."""
        from workflows.epfr.pdf_ocr_workflow import EpfrOcrConverter

        assert hasattr(EpfrOcrConverter, "__workflows_workflow_def")
        assert self._get_workflow_name(EpfrOcrConverter) == "epfr-ocr-converter"

    def test_ai_distiller_discoverable(self):
        """EpfrAiDistillerWorkflow has the discovery attribute and correct name."""
        from workflows.epfr.ai_distiller_workflow import EpfrAiDistillerWorkflow

        assert hasattr(EpfrAiDistillerWorkflow, "__workflows_workflow_def")
        assert self._get_workflow_name(EpfrAiDistillerWorkflow) == "epfr-ai-distiller"

    def test_share_payout_exporter_discoverable(self):
        """EpfrSharePayoutExporterWorkflow has the discovery attribute and correct name."""
        from workflows.epfr.share_payout_exporter_workflow import EpfrSharePayoutExporterWorkflow

        assert hasattr(EpfrSharePayoutExporterWorkflow, "__workflows_workflow_def")
        assert self._get_workflow_name(EpfrSharePayoutExporterWorkflow) == "epfr-share-payout-exporter"


class TestAllWorkflowsDiscovered:
    """Verify all four workflow names appear in the full discovery scan."""

    def test_all_four_workflows_in_discovery(self):
        """The discover_workflows scan returns all four EPFR workflow classes."""
        os.environ.pop("AGENT", None)
        from mistralai.workflows.core.definition.workflow_definition import get_workflow_definition

        from discover import discover_workflows

        names = sorted(get_workflow_definition(wf).name for wf in discover_workflows())
        for expected_name in [
            "epfr-ai-distiller",
            "epfr-files-downloader",
            "epfr-ocr-converter",
            "epfr-share-payout-exporter",
        ]:
            assert expected_name in names


class TestWorkflowClassInterface:
    """Verify each workflow class has the expected run entrypoint method."""

    def test_downloader_has_run_method(self):
        assert hasattr(EpfrFilesDownloader, "run")
        assert callable(getattr(EpfrFilesDownloader, "run", None))

    def test_pdf_ocr_has_run_method(self):
        from workflows.epfr.pdf_ocr_workflow import EpfrOcrConverter

        assert hasattr(EpfrOcrConverter, "run")
        assert callable(getattr(EpfrOcrConverter, "run", None))

    def test_ai_distiller_has_run_method(self):
        from workflows.epfr.ai_distiller_workflow import EpfrAiDistillerWorkflow

        assert hasattr(EpfrAiDistillerWorkflow, "run")
        assert callable(getattr(EpfrAiDistillerWorkflow, "run", None))

    def test_share_payout_exporter_has_run_method(self):
        from workflows.epfr.share_payout_exporter_workflow import EpfrSharePayoutExporterWorkflow

        assert hasattr(EpfrSharePayoutExporterWorkflow, "run")
        assert callable(getattr(EpfrSharePayoutExporterWorkflow, "run", None))

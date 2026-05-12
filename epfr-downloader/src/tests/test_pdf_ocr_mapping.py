"""Default-collected tests for OCR mapping updates without live OCR."""

# ruff: noqa: D102

from collections.abc import Iterator
from dataclasses import dataclass
import importlib
import json
from pathlib import Path
import sys
import types


@dataclass
class _DocumentURLChunk:
    document_url: str
    document_name: str


@dataclass
class _OCRRequest:
    model: str
    document: _DocumentURLChunk


async def _unconfigured_mistral_ocr(_request):
    raise AssertionError("Test must patch pdf_ocr.mistralai_ocr before use")


pytest = importlib.import_module("pytest")


@pytest.fixture()
def anyio_backend():
    """Return the anyio backend for async tests."""
    return "asyncio"


def _make_package_module(name: str) -> types.ModuleType:
    module = types.ModuleType(name)
    module.__path__ = []
    return module


@pytest.fixture()
def pdf_ocr_module(monkeypatch) -> Iterator[types.ModuleType]:
    """Create a mocked pdf_ocr module for testing."""
    target_module_name = "workflows.epfr.pdf_ocr"
    original_pdf_ocr = sys.modules.pop(target_module_name, None)

    mistralai_module = _make_package_module("mistralai")
    mistralai_client_module = _make_package_module("mistralai.client")
    mistralai_client_models_module = types.ModuleType("mistralai.client.models")
    mistralai_workflows_module = _make_package_module("mistralai.workflows")
    mistralai_workflows_plugins_module = _make_package_module("mistralai.workflows.plugins")
    mistralai_workflows_plugins_mistralai_module = types.ModuleType("mistralai.workflows.plugins.mistralai")

    mistralai_client_models_module.DocumentURLChunk = _DocumentURLChunk
    mistralai_workflows_plugins_mistralai_module.OCRRequest = _OCRRequest
    mistralai_workflows_plugins_mistralai_module.mistralai_ocr = _unconfigured_mistral_ocr

    monkeypatch.setitem(sys.modules, "mistralai", mistralai_module)
    monkeypatch.setitem(sys.modules, "mistralai.client", mistralai_client_module)
    monkeypatch.setitem(sys.modules, "mistralai.client.models", mistralai_client_models_module)
    monkeypatch.setitem(sys.modules, "mistralai.workflows", mistralai_workflows_module)
    monkeypatch.setitem(sys.modules, "mistralai.workflows.plugins", mistralai_workflows_plugins_module)
    monkeypatch.setitem(
        sys.modules, "mistralai.workflows.plugins.mistralai", mistralai_workflows_plugins_mistralai_module
    )

    module = importlib.import_module(target_module_name)
    yield module

    sys.modules.pop(target_module_name, None)
    if original_pdf_ocr is not None:
        sys.modules[target_module_name] = original_pdf_ocr


class _FakePage:
    def __init__(self, markdown: str):
        self.markdown = markdown


class _FakeOcrResult:
    def __init__(self, pages: list[_FakePage]):
        self.pages = pages


def _stage_fixture_pdf_mapping(copy_epfr_fixture, load_epfr_fixture_json, tmp_path: Path) -> tuple[Path, str, str]:
    mapping_path = copy_epfr_fixture("unp_file_mapping.json", tmp_path)
    original_mapping = load_epfr_fixture_json("unp_file_mapping.json")

    unp = "600073968"
    filename = "141278.pdf"
    entry = original_mapping[unp]["files"][0]

    staged_mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
    staged_entry = dict(entry)
    staged_entry["filename"] = filename
    staged_entry["converted_from"] = None
    staged_mapping[unp]["files"][0] = staged_entry
    mapping_path.write_text(json.dumps(staged_mapping, ensure_ascii=False, indent=2), encoding="utf-8")

    pdf_dir = tmp_path / unp
    pdf_dir.mkdir(parents=True, exist_ok=True)
    (pdf_dir / filename).write_bytes(b"%PDF-1.4 fixture")

    return mapping_path, unp, filename


@pytest.mark.anyio
async def test_ocr_mapping_fixture_pdf_updates_mapping_and_preserves_fixture(
    pdf_ocr_module,
    monkeypatch,
    tmp_path: Path,
    copy_epfr_fixture,
    load_epfr_fixture_json,
):
    mapping_path, unp, filename = _stage_fixture_pdf_mapping(copy_epfr_fixture, load_epfr_fixture_json, tmp_path)
    original_mapping = load_epfr_fixture_json("unp_file_mapping.json")

    async def _fake_ocr(_request):
        return _FakeOcrResult([_FakePage("Title\n\n![img-0.jpeg](img-0.jpeg)\n\nBody")])

    monkeypatch.setattr(pdf_ocr_module, "mistralai_ocr", _fake_ocr)

    stats = await pdf_ocr_module.ocr_mapping_pdfs(
        output_root=tmp_path,
        mapping_filename="unp_file_mapping.json",
        overwrite=True,
        cleanup_source=False,
        unps=[unp],
    )

    updated_mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
    updated_entry = updated_mapping[unp]["files"][0]
    pdf_path = tmp_path / unp / filename
    md_path = pdf_path.with_suffix(".md")

    assert stats["mapping_path"] == str(mapping_path.resolve())
    assert stats["total_unps_scanned"] == 1
    assert stats["total_pdf_entries"] == 1
    assert stats["total_successful"] == 1
    assert stats["total_failed"] == 0
    assert stats["total_skipped"] == 0
    assert stats["cleaned_up_files"] == []
    assert stats["by_unp"][unp]["converted_files"] == [{"source": filename, "markdown": "141278.md"}]
    assert updated_entry["filename"] == "141278.md"
    assert updated_entry["converted_from"] == filename
    assert updated_entry["extracted_from"] is None
    assert md_path.read_text(encoding="utf-8") == "Title\n\nBody"
    assert pdf_path.exists()
    assert original_mapping[unp]["files"][0]["filename"] == "141278.md"
    assert load_epfr_fixture_json("unp_file_mapping.json")[unp]["files"][0]["filename"] == "141278.md"


@pytest.mark.anyio
async def test_ocr_mapping_skips_when_markdown_exists(
    pdf_ocr_module, monkeypatch, tmp_path: Path, copy_epfr_fixture, load_epfr_fixture_json
):
    mapping_path, unp, filename = _stage_fixture_pdf_mapping(copy_epfr_fixture, load_epfr_fixture_json, tmp_path)
    pdf_path = tmp_path / unp / filename
    md_path = pdf_path.with_suffix(".md")
    md_path.write_text("existing markdown", encoding="utf-8")
    ocr_calls = 0

    async def _fake_ocr(_request):
        nonlocal ocr_calls
        ocr_calls += 1
        return _FakeOcrResult([_FakePage("should not run")])

    monkeypatch.setattr(pdf_ocr_module, "mistralai_ocr", _fake_ocr)

    stats = await pdf_ocr_module.ocr_mapping_pdfs(
        output_root=tmp_path,
        mapping_filename="unp_file_mapping.json",
        overwrite=False,
        cleanup_source=False,
        unps=[unp],
    )

    updated_mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
    updated_entry = updated_mapping[unp]["files"][0]

    assert ocr_calls == 0
    assert stats["total_pdf_entries"] == 1
    assert stats["total_successful"] == 0
    assert stats["total_failed"] == 0
    assert stats["total_skipped"] == 1
    assert stats["skipped_files"] == [str(pdf_path)]
    assert updated_entry["filename"] == filename
    assert updated_entry["converted_from"] is None
    assert pdf_path.exists()
    assert md_path.read_text(encoding="utf-8") == "existing markdown"


@pytest.mark.anyio
async def test_ocr_mapping_missing_mapping_file_raises(pdf_ocr_module, tmp_path: Path):
    with pytest.raises(FileNotFoundError, match="Mapping file not found"):
        await pdf_ocr_module.ocr_mapping_pdfs(output_root=tmp_path, mapping_filename="unp_file_mapping.json")


@pytest.mark.anyio
async def test_ocr_mapping_invalid_mapping_file_raises_json_decode_error(pdf_ocr_module, tmp_path: Path):
    mapping_path = tmp_path / "unp_file_mapping.json"
    mapping_path.write_text("{not valid json", encoding="utf-8")

    with pytest.raises(json.JSONDecodeError):
        await pdf_ocr_module.ocr_mapping_pdfs(output_root=tmp_path, mapping_filename=mapping_path.name)


@pytest.mark.anyio
async def test_ocr_mapping_preserves_original_mapping_when_atomic_replace_fails(
    pdf_ocr_module,
    monkeypatch,
    tmp_path: Path,
    copy_epfr_fixture,
    load_epfr_fixture_json,
):
    mapping_path, unp, _filename = _stage_fixture_pdf_mapping(copy_epfr_fixture, load_epfr_fixture_json, tmp_path)
    original_mapping_text = mapping_path.read_text(encoding="utf-8")

    async def _fake_ocr(_request):
        return _FakeOcrResult([_FakePage("stable markdown")])

    def _failing_replace(_src: str, _dst: str):
        raise OSError("replace failed")

    monkeypatch.setattr(pdf_ocr_module, "mistralai_ocr", _fake_ocr)
    monkeypatch.setattr(pdf_ocr_module.os, "replace", _failing_replace)

    with pytest.raises(OSError, match="replace failed"):
        await pdf_ocr_module.ocr_mapping_pdfs(
            output_root=tmp_path,
            mapping_filename="unp_file_mapping.json",
            overwrite=True,
            cleanup_source=False,
            unps=[unp],
        )

    assert mapping_path.read_text(encoding="utf-8") == original_mapping_text
    assert list(tmp_path.glob(".mapping_ocr_*.tmp")) == []

"""Inventory tests for the committed EPFR fixtures."""

from __future__ import annotations

import importlib
from collections.abc import Callable
from typing import Any
from pathlib import Path

pytest = importlib.import_module("pytest")


REQUIRED_REAL_WORLD_FIXTURES = [
    "140297.md",
    "140911.md",
    "ai_distilled_dividends.json",
    "epfr_api_response_null_subcategory.json",
    "ole2_excel_as_doc_dividends.doc",
    "ole2_excel_as_doc_sectors.doc",
    "share_payouts_by_unp.json",
    "unp_file_mapping.json",
]


def test_required_real_world_fixtures_exist(epfr_fixtures_dir: Path):
    for name in REQUIRED_REAL_WORLD_FIXTURES:
        assert (epfr_fixtures_dir / name).is_file(), name


@pytest.mark.parametrize("name", REQUIRED_REAL_WORLD_FIXTURES)
def test_required_real_world_fixtures_are_readable(
    name: str,
    load_epfr_fixture_json: Callable[[str], Any],
    load_epfr_fixture_text: Callable[[str], str],
    copy_epfr_fixture: Callable[[str, Path], Path],
    tmp_path: Path,
):
    if name.endswith(".json"):
        loaded = load_epfr_fixture_json(name)
        assert isinstance(loaded, (dict, list))
    elif name.endswith(".md"):
        loaded = load_epfr_fixture_text(name)
        assert isinstance(loaded, str)
        assert len(loaded) > 0
    else:
        copied_path = copy_epfr_fixture(name, tmp_path)
        assert copied_path.exists()
        assert copied_path.read_bytes() == (Path(__file__).parent / "fixtures" / name).read_bytes()
        return

    copied_path = copy_epfr_fixture(name, tmp_path)
    assert copied_path.exists()
    assert copied_path.name == name


def test_fixture_helpers_use_utf8_and_do_not_mutate_committed_files(
    epfr_fixtures_dir: Path,
    load_epfr_fixture_json: Callable[[str], Any],
    load_epfr_fixture_text: Callable[[str], str],
    copy_epfr_fixture: Callable[[str, Path], Path],
    tmp_path: Path,
):
    before = {name: (epfr_fixtures_dir / name).stat().st_mtime_ns for name in REQUIRED_REAL_WORLD_FIXTURES}

    json_data = load_epfr_fixture_json("unp_file_mapping.json")
    text_data = load_epfr_fixture_text("140297.md")
    copied_path = copy_epfr_fixture("ole2_excel_as_doc_dividends.doc", tmp_path)

    assert isinstance(json_data, (dict, list))
    assert isinstance(text_data, str)
    assert len(text_data) > 0
    assert copied_path.exists()

    after = {name: (epfr_fixtures_dir / name).stat().st_mtime_ns for name in REQUIRED_REAL_WORLD_FIXTURES}
    assert before == after


def test_missing_fixture_reports_name(load_epfr_fixture_json: Callable[[str], Any]):
    with pytest.raises(FileNotFoundError, match="missing.fixture"):
        load_epfr_fixture_json("missing.fixture")


FIXTURE_TO_TEST_MODULE: dict[str, list[str]] = {
    "epfr_api_response_null_subcategory.json": [
        "test_models.py",
        "test_client.py",
    ],
    "ole2_excel_as_doc_dividends.doc": [
        "test_converter.py",
    ],
    "ole2_excel_as_doc_sectors.doc": [
        "test_converter.py",
    ],
    "unp_file_mapping.json": [
        "test_models.py",
        "test_pdf_ocr_mapping.py",
        "test_ai_distiller.py",
    ],
    "140911.md": [
        "test_markdown_cleanup.py",
        "test_ai_distiller.py",
    ],
    "140297.md": [
        "test_markdown_cleanup.py",
        "test_ai_distiller.py",
    ],
    "ai_distilled_dividends.json": [
        "test_models.py",
        "test_share_payout_exporter.py",
    ],
    "share_payouts_by_unp.json": [
        "test_models.py",
        "test_share_payout_exporter.py",
    ],
}


def test_fixture_audit_all_fixtures_mapped_to_tests():
    """Every required fixture has at least one test module mapping."""
    for fixture_name in REQUIRED_REAL_WORLD_FIXTURES:
        assert fixture_name in FIXTURE_TO_TEST_MODULE, f"Fixture {fixture_name!r} has no test module mapping"


def test_fixture_audit_all_test_modules_exist():
    """All referenced test modules exist in the test directory."""
    test_dir = Path(__file__).parent
    for fixture_name, modules in FIXTURE_TO_TEST_MODULE.items():
        for module_name in modules:
            assert (test_dir / module_name).is_file(), (
                f"Test module {module_name!r} for fixture {fixture_name!r} does not exist"
            )

"""Tests for EPFR Share Payout Exporter workflow activities.

This module tests the 4 split activities in share_payout_exporter_workflow.py:
1. scan_share_payout_export - Load CSV index and distilled JSON
2. process_share_payout_matching - Match dividends against CSV index
3. finalize_share_payout_export - Save export JSON output
4. generate_share_payout_sql - Generate SQL INSERT statements from JSON output

These tests validate the workflow activities independently, enabling:
- Granular progress tracking in Mistral Workflows UI
- Parallel test execution per activity
- Proper separation of concerns
"""

import json
import os
from unittest.mock import patch


os.environ.pop("AGENT", None)


import pytest

from workflows.epfr.models import (
    EpfrSharePayoutExportInput,
    SharePayoutProcessResult,
    SharePayoutScanResult,
)
from workflows.epfr.share_payout_exporter_workflow import (
    finalize_share_payout_export,
    generate_share_payout_sql,
    process_share_payout_matching,
    scan_share_payout_export,
)


# =============================================================================
# Test fixtures for Share Payout Exporter Workflow
# =============================================================================


@pytest.fixture
def sample_csv_content():
    """Sample CSV content for testing."""
    return """name,unp,instrument_uuid,ticker,share_kind
Company A,100000001,uuid-common-1,T001,common
Company A,100000001,uuid-pref-1,T002,preferred
Company B,200000002,uuid-common-2,T003,common
Company C,300000003,uuid-common-3,T004,common
Company C,300000003,uuid-pref-3,T005,preferred
"""


@pytest.fixture
def sample_csv_with_ambiguous():
    """Sample CSV with ambiguous entries (duplicate unp+share_kind)."""
    return """name,unp,instrument_uuid,ticker,share_kind
Bank,100325912,uuid-pref-a,T001,preferred
Bank,100325912,uuid-pref-b,T002,preferred
"""


@pytest.fixture
def sample_distilled_data():
    """Sample distilled JSON data for testing."""
    return {
        "100000001": {
            "company_name": "Company A",
            "unp": "100000001",
            "holder_id": 1,
            "files": [
                {
                    "id": 101,
                    "filename": "doc1.md",
                    "has_dividends": True,
                    "ai_comment": "Test",
                    "dividends": [
                        {
                            "share_type": "common",
                            "period_year": 2026,
                            "period_type": "annual",
                            "period_number": 1,
                            "amount_per_share": "0.50",
                            "decision_date": "2026-01-15",
                            "record_date": "2026-01-10",
                            "payment_date": "2026-02-15",
                        },
                        {
                            "share_type": "preferred",
                            "period_year": 2026,
                            "period_type": "annual",
                            "period_number": 1,
                            "amount_per_share": "0.75",
                            "decision_date": "2026-01-15",
                            "record_date": "2026-01-10",
                            "payment_date": "2026-02-15",
                        },
                    ],
                    "autofilled_fields": [],
                },
            ],
        },
        "200000002": {
            "company_name": "Company B",
            "unp": "200000002",
            "holder_id": 2,
            "files": [
                {
                    "id": 201,
                    "filename": "doc2.md",
                    "has_dividends": True,
                    "ai_comment": "Test",
                    "dividends": [
                        {
                            "share_type": "common",
                            "period_year": 2026,
                            "period_type": "annual",
                            "period_number": 1,
                            "amount_per_share": "1.00",
                            "decision_date": "2026-01-20",
                            "record_date": "2026-01-15",
                            "payment_date": "2026-02-20",
                        },
                    ],
                    "autofilled_fields": [],
                },
            ],
        },
    }


@pytest.fixture
def sample_distilled_data_with_errors():
    """Sample distilled data with file errors."""
    return {
        "100000001": {
            "company_name": "Company A",
            "unp": "100000001",
            "holder_id": 1,
            "files": [
                {
                    "id": 101,
                    "filename": "doc1.md",
                    "has_dividends": True,
                    "ai_comment": "Test",
                    "dividends": [
                        {
                            "share_type": "common",
                            "period_year": 2026,
                            "period_type": "annual",
                            "period_number": 1,
                            "amount_per_share": "0.50",
                            "decision_date": "2026-01-15",
                            "record_date": "2026-01-10",
                            "payment_date": "2026-02-15",
                        },
                    ],
                    "autofilled_fields": [],
                    "error": None,
                },
                {
                    "id": 102,
                    "filename": "doc2.md",
                    "error": "File read error",
                    "dividends": [
                        {
                            "share_type": "common",
                            "period_year": 2026,
                        },
                    ],
                    "autofilled_fields": [],
                },
            ],
        },
    }


@pytest.fixture
def sample_scan_result(tmp_path):
    """Create a sample scan result for testing."""
    csv_index = {
        "100000001|common": "uuid-common-1",
        "100000001|preferred": "uuid-pref-1",
        "200000002|common": "uuid-common-2",
    }
    csv_stats = {
        "ambiguous_share_kind": 0,
        "known_unps": {"100000001", "200000002"},
        "ambiguous_keys": set(),
    }
    return SharePayoutScanResult(
        csv_path=str(tmp_path / "shares.csv"),
        csv_index=csv_index,
        csv_stats=csv_stats,
        distilled_path=str(tmp_path / "ai_distilled_dividends.json"),
        distilled_data={"100000001": {"files": []}},
        output_dir=str(tmp_path),
        output_filename="share_payouts_by_unp.json",
    )


# =============================================================================
# TestScanSharePayoutExport - Activity 1 Tests
# =============================================================================


class TestScanSharePayoutExport:
    """Test Activity 1: scan_share_payout_export.

    This activity loads the CSV index and distilled JSON for payout export.
    """

    @pytest.mark.anyio
    async def test_loads_csv_and_distilled_json(self, tmp_path, sample_csv_content, sample_distilled_data):
        """Verifies both files are loaded correctly."""
        # Setup: Create CSV and distilled JSON files
        csv_path = tmp_path / "shares.csv"
        csv_path.write_text(sample_csv_content, encoding="utf-8")

        distilled_path = tmp_path / "ai_distilled_dividends.json"
        distilled_path.write_text(json.dumps(sample_distilled_data), encoding="utf-8")

        input_obj = EpfrSharePayoutExportInput(
            output_dir=str(tmp_path),
            input_filename="ai_distilled_dividends.json",
            output_filename="share_payouts_by_unp.json",
            shares_csv_path=str(csv_path),
        )

        result = await scan_share_payout_export(input_obj)

        assert isinstance(result, SharePayoutScanResult)
        assert result.csv_path == str(csv_path.resolve())
        assert result.distilled_path == str(distilled_path.resolve())
        assert len(result.csv_index) == 5  # 5 data rows in CSV
        assert len(result.distilled_data) == 2
        assert "100000001|common" in result.csv_index
        assert "200000002|common" in result.csv_index

    @pytest.mark.anyio
    async def test_builds_index_with_csv_stats(self, tmp_path, sample_csv_content):
        """Verifies csv_index and csv_stats are populated correctly."""
        csv_path = tmp_path / "shares.csv"
        csv_path.write_text(sample_csv_content, encoding="utf-8")

        distilled_path = tmp_path / "ai_distilled_dividends.json"
        distilled_path.write_text("{}", encoding="utf-8")

        input_obj = EpfrSharePayoutExportInput(
            output_dir=str(tmp_path),
            input_filename="ai_distilled_dividends.json",
            output_filename="share_payouts_by_unp.json",
            shares_csv_path=str(csv_path),
        )

        result = await scan_share_payout_export(input_obj)

        # Verify index has correct mappings
        assert result.csv_index["100000001|common"] == "uuid-common-1"
        assert result.csv_index["100000001|preferred"] == "uuid-pref-1"
        assert result.csv_index["200000002|common"] == "uuid-common-2"
        assert result.csv_index["300000003|common"] == "uuid-common-3"
        assert result.csv_index["300000003|preferred"] == "uuid-pref-3"

        # Verify stats
        assert result.csv_stats["ambiguous_share_kind"] == 0
        assert "100000001" in result.csv_stats["known_unps"]
        assert "200000002" in result.csv_stats["known_unps"]
        assert "300000003" in result.csv_stats["known_unps"]

    @pytest.mark.anyio
    async def test_raises_file_not_found_for_missing_csv(self, tmp_path):
        """Verifies FileNotFoundError when CSV doesn't exist."""
        input_obj = EpfrSharePayoutExportInput(
            output_dir=str(tmp_path),
            input_filename="ai_distilled_dividends.json",
            output_filename="share_payouts_by_unp.json",
            shares_csv_path=str(tmp_path / "nonexistent.csv"),
        )

        with pytest.raises(FileNotFoundError) as exc_info:
            await scan_share_payout_export(input_obj)

        assert "CSV file not found" in str(exc_info.value)

    @pytest.mark.anyio
    async def test_raises_file_not_found_for_missing_distilled(self, tmp_path, sample_csv_content):
        """Verifies FileNotFoundError when distilled JSON doesn't exist."""
        csv_path = tmp_path / "shares.csv"
        csv_path.write_text(sample_csv_content, encoding="utf-8")

        input_obj = EpfrSharePayoutExportInput(
            output_dir=str(tmp_path),
            input_filename="nonexistent.json",
            output_filename="share_payouts_by_unp.json",
            shares_csv_path=str(csv_path),
        )

        with pytest.raises(FileNotFoundError) as exc_info:
            await scan_share_payout_export(input_obj)

        assert "Distilled JSON file not found" in str(exc_info.value)

    @pytest.mark.anyio
    async def test_handles_empty_csv(self, tmp_path):
        """Verifies empty CSV (header only) produces empty index."""
        csv_path = tmp_path / "shares.csv"
        csv_path.write_text("name,unp,instrument_uuid,ticker,share_kind\n", encoding="utf-8")

        distilled_path = tmp_path / "ai_distilled_dividends.json"
        distilled_path.write_text("{}", encoding="utf-8")

        input_obj = EpfrSharePayoutExportInput(
            output_dir=str(tmp_path),
            input_filename="ai_distilled_dividends.json",
            output_filename="share_payouts_by_unp.json",
            shares_csv_path=str(csv_path),
        )

        result = await scan_share_payout_export(input_obj)

        assert result.csv_index == {}
        assert result.csv_stats["known_unps"] == set()

    @pytest.mark.anyio
    async def test_handles_csv_with_ambiguous_entries(self, tmp_path, sample_csv_with_ambiguous):
        """Verifies CSV with ambiguous entries correctly identifies and excludes them."""
        csv_path = tmp_path / "shares.csv"
        csv_path.write_text(sample_csv_with_ambiguous, encoding="utf-8")

        distilled_path = tmp_path / "ai_distilled_dividends.json"
        distilled_path.write_text("{}", encoding="utf-8")

        input_obj = EpfrSharePayoutExportInput(
            output_dir=str(tmp_path),
            input_filename="ai_distilled_dividends.json",
            output_filename="share_payouts_by_unp.json",
            shares_csv_path=str(csv_path),
        )

        result = await scan_share_payout_export(input_obj)

        # Ambiguous entries should not be in index
        assert "100325912|preferred" not in result.csv_index
        assert result.csv_stats["ambiguous_share_kind"] == 2


# =============================================================================
# TestProcessSharePayoutMatching - Activity 2 Tests
# =============================================================================


class TestProcessSharePayoutMatching:
    """Test Activity 2: process_share_payout_matching.

    This activity matches dividends against the share reference CSV index.
    """

    @pytest.mark.anyio
    async def test_matches_dividends_against_csv_index(self, tmp_path):
        """Verifies dividend matching logic."""
        csv_index = {
            "100000001|common": "uuid-common-1",
            "100000001|preferred": "uuid-pref-1",
        }
        csv_stats = {
            "ambiguous_share_kind": 0,
            "known_unps": {"100000001"},
            "ambiguous_keys": set(),
        }

        distilled_data = {
            "100000001": {
                "files": [
                    {
                        "id": 101,
                        "dividends": [
                            {
                                "share_type": "common",
                                "period_year": 2026,
                                "period_type": "annual",
                                "period_number": 1,
                                "amount_per_share": "0.50",
                                "decision_date": "2026-01-15",
                                "record_date": "2026-01-10",
                                "payment_date": "2026-02-15",
                            },
                            {
                                "share_type": "preferred",
                                "period_year": 2026,
                                "period_type": "annual",
                                "period_number": 1,
                                "amount_per_share": "0.75",
                                "decision_date": "2026-01-15",
                                "record_date": "2026-01-10",
                                "payment_date": "2026-02-15",
                            },
                        ],
                        "autofilled_fields": [],
                    },
                ],
            },
        }

        scan_result = SharePayoutScanResult(
            csv_path=str(tmp_path / "shares.csv"),
            csv_index=csv_index,
            csv_stats=csv_stats,
            distilled_path=str(tmp_path / "ai_distilled_dividends.json"),
            distilled_data=distilled_data,
            output_dir=str(tmp_path),
            output_filename="share_payouts_by_unp.json",
        )

        result = await process_share_payout_matching(scan_result)

        assert isinstance(result, SharePayoutProcessResult)
        assert result.matched_count == 2
        assert result.skipped_file_errors == 0
        assert "100000001" in result.export_data
        assert len(result.export_data["100000001"]) == 2

    @pytest.mark.anyio
    async def test_tracks_all_unmatched_categories(self, tmp_path):
        """Verifies counting of missing_csv_unp, missing_share_kind, etc."""
        csv_index = {
            "100000001|common": "uuid-common-1",
        }
        csv_stats = {
            "ambiguous_share_kind": 0,
            "known_unps": {"100000001"},
            "ambiguous_keys": set(),
        }

        distilled_data = {
            "100000001": {
                "files": [
                    {
                        "id": 101,
                        "dividends": [
                            {
                                "share_type": "common",
                                "period_year": 2026,
                                "period_type": "annual",
                                "period_number": 1,
                                "amount_per_share": "0.50",
                                "decision_date": "2026-01-15",
                                "record_date": "2026-01-10",
                                "payment_date": "2026-02-15",
                            },
                        ],
                        "autofilled_fields": [],
                    },
                ],
            },
            "999999999": {  # Unknown UNP
                "files": [
                    {
                        "id": 999,
                        "dividends": [
                            {
                                "share_type": "common",
                                "period_year": 2026,
                                "period_type": "annual",
                                "period_number": 1,
                                "amount_per_share": "0.50",
                                "decision_date": "2026-01-15",
                                "record_date": "2026-01-10",
                                "payment_date": "2026-02-15",
                            },
                        ],
                        "autofilled_fields": [],
                    },
                ],
            },
        }

        scan_result = SharePayoutScanResult(
            csv_path=str(tmp_path / "shares.csv"),
            csv_index=csv_index,
            csv_stats=csv_stats,
            distilled_path=str(tmp_path / "ai_distilled_dividends.json"),
            distilled_data=distilled_data,
            output_dir=str(tmp_path),
            output_filename="share_payouts_by_unp.json",
        )

        result = await process_share_payout_matching(scan_result)

        assert result.matched_count == 1  # Only the known UNP with matching share_type
        assert result.missing_csv_unp == 1  # 999999999 is unknown

    @pytest.mark.anyio
    async def test_collects_samples_for_debugging(self, tmp_path):
        """Verifies samples are collected for each category."""
        csv_index = {
            "100000001|common": "uuid-common-1",
        }
        csv_stats = {
            "ambiguous_share_kind": 0,
            "known_unps": {"100000001"},
            "ambiguous_keys": set(),
        }

        # Create 15 dividends from unknown UNP to trigger sample collection
        distilled_data = {
            "999999999": {  # Unknown UNP - will generate samples
                "files": [
                    {
                        "id": i,
                        "dividends": [
                            {
                                "share_type": "common",
                                "period_year": 2026,
                                "period_type": "annual",
                                "period_number": 1,
                                "amount_per_share": "0.50",
                                "decision_date": "2026-01-15",
                                "record_date": "2026-01-10",
                                "payment_date": "2026-02-15",
                            },
                        ],
                        "autofilled_fields": [],
                    }
                    for i in range(15)  # More than 10 to test sample limit
                ],
            },
        }

        scan_result = SharePayoutScanResult(
            csv_path=str(tmp_path / "shares.csv"),
            csv_index=csv_index,
            csv_stats=csv_stats,
            distilled_path=str(tmp_path / "ai_distilled_dividends.json"),
            distilled_data=distilled_data,
            output_dir=str(tmp_path),
            output_filename="share_payouts_by_unp.json",
        )

        result = await process_share_payout_matching(scan_result)

        # Should have collected samples, limited to 10
        assert len(result.samples["missing_csv_unp"]) <= 10
        assert len(result.samples["missing_csv_unp"]) > 0  # At least some samples collected
        assert result.missing_csv_unp == 15  # All 15 are from unknown UNP

    @pytest.mark.anyio
    async def test_skips_file_errors_correctly(self, tmp_path, sample_distilled_data_with_errors):
        """Verifies file error entries are skipped and counted."""
        csv_index = {
            "100000001|common": "uuid-common-1",
        }
        csv_stats = {
            "ambiguous_share_kind": 0,
            "known_unps": {"100000001"},
            "ambiguous_keys": set(),
        }

        scan_result = SharePayoutScanResult(
            csv_path=str(tmp_path / "shares.csv"),
            csv_index=csv_index,
            csv_stats=csv_stats,
            distilled_path=str(tmp_path / "ai_distilled_dividends.json"),
            distilled_data=sample_distilled_data_with_errors,
            output_dir=str(tmp_path),
            output_filename="share_payouts_by_unp.json",
        )

        result = await process_share_payout_matching(scan_result)

        # One file has error, it has 1 dividend that should be counted as skipped
        assert result.skipped_file_errors == 1
        assert len(result.samples["skipped_file_errors"]) == 1
        assert result.matched_count == 1  # Only the non-error file's dividend

    @pytest.mark.anyio
    async def test_handles_autofilled_share_type(self, tmp_path):
        """Verifies autofilled share_type entries are skipped and counted."""
        csv_index = {
            "100000001|common": "uuid-common-1",
        }
        csv_stats = {
            "ambiguous_share_kind": 0,
            "known_unps": {"100000001"},
            "ambiguous_keys": set(),
        }

        distilled_data = {
            "100000001": {
                "files": [
                    {
                        "id": 101,
                        "dividends": [
                            {
                                "share_type": "common",
                                "period_year": 2026,
                                "period_type": "annual",
                                "period_number": 1,
                                "amount_per_share": "0.50",
                                "decision_date": "2026-01-15",
                                "record_date": "2026-01-10",
                                "payment_date": "2026-02-15",
                            },
                        ],
                        "autofilled_fields": ["share_type"],  # share_type was autofilled
                    },
                ],
            },
        }

        scan_result = SharePayoutScanResult(
            csv_path=str(tmp_path / "shares.csv"),
            csv_index=csv_index,
            csv_stats=csv_stats,
            distilled_path=str(tmp_path / "ai_distilled_dividends.json"),
            distilled_data=distilled_data,
            output_dir=str(tmp_path),
            output_filename="share_payouts_by_unp.json",
        )

        result = await process_share_payout_matching(scan_result)

        assert result.autofilled_share_type == 1
        assert result.matched_count == 0  # Autofilled entries are skipped

    @pytest.mark.anyio
    async def test_handles_missing_share_kind(self, tmp_path):
        """Verifies entries with share_kind not in CSV are counted."""
        csv_index = {
            "100000001|common": "uuid-common-1",
        }
        csv_stats = {
            "ambiguous_share_kind": 0,
            "known_unps": {"100000001"},
            "ambiguous_keys": set(),
        }

        distilled_data = {
            "100000001": {
                "files": [
                    {
                        "id": 101,
                        "dividends": [
                            {
                                "share_type": "common",
                                "period_year": 2026,
                                "period_type": "annual",
                                "period_number": 1,
                                "amount_per_share": "0.50",
                                "decision_date": "2026-01-15",
                                "record_date": "2026-01-10",
                                "payment_date": "2026-02-15",
                            },
                            {
                                "share_type": "special",  # Not in CSV
                                "period_year": 2026,
                                "period_type": "annual",
                                "period_number": 1,
                                "amount_per_share": "0.75",
                                "decision_date": "2026-01-15",
                                "record_date": "2026-01-10",
                                "payment_date": "2026-02-15",
                            },
                        ],
                        "autofilled_fields": [],
                    },
                ],
            },
        }

        scan_result = SharePayoutScanResult(
            csv_path=str(tmp_path / "shares.csv"),
            csv_index=csv_index,
            csv_stats=csv_stats,
            distilled_path=str(tmp_path / "ai_distilled_dividends.json"),
            distilled_data=distilled_data,
            output_dir=str(tmp_path),
            output_filename="share_payouts_by_unp.json",
        )

        result = await process_share_payout_matching(scan_result)

        assert result.matched_count == 1  # Only common matched
        assert result.missing_share_kind == 1  # special is missing

    @pytest.mark.anyio
    async def test_handles_ambiguous_share_kind(self, tmp_path):
        """Verifies ambiguous share_kind entries are counted."""
        csv_index = {
            "100000001|common": "uuid-common-1",
        }
        ambiguous_keys = {"100000001|preferred"}  # Marked as ambiguous
        csv_stats = {
            "ambiguous_share_kind": 0,
            "known_unps": {"100000001"},
            "ambiguous_keys": ambiguous_keys,
        }

        distilled_data = {
            "100000001": {
                "files": [
                    {
                        "id": 101,
                        "dividends": [
                            {
                                "share_type": "preferred",  # Ambiguous
                                "period_year": 2026,
                                "period_type": "annual",
                                "period_number": 1,
                                "amount_per_share": "0.75",
                                "decision_date": "2026-01-15",
                                "record_date": "2026-01-10",
                                "payment_date": "2026-02-15",
                            },
                        ],
                        "autofilled_fields": [],
                    },
                ],
            },
        }

        scan_result = SharePayoutScanResult(
            csv_path=str(tmp_path / "shares.csv"),
            csv_index=csv_index,
            csv_stats=csv_stats,
            distilled_path=str(tmp_path / "ai_distilled_dividends.json"),
            distilled_data=distilled_data,
            output_dir=str(tmp_path),
            output_filename="share_payouts_by_unp.json",
        )

        result = await process_share_payout_matching(scan_result)

        assert result.matched_count == 0
        assert result.ambiguous_share_kind == 1


# =============================================================================
# TestFinalizeSharePayoutExport - Activity 3 Tests
# =============================================================================


class TestFinalizeSharePayoutExport:
    """Test Activity 3: finalize_share_payout_export.

    This activity performs the atomic write of the export JSON output.
    """

    @pytest.mark.anyio
    async def test_writes_atomic_output_file(self, tmp_path):
        """Verifies tempfile.mkstemp + os.replace pattern is used."""
        output_dir = tmp_path / "output"
        output_dir.mkdir(parents=True, exist_ok=True)

        scan_result = SharePayoutScanResult(
            csv_path=str(tmp_path / "shares.csv"),
            csv_index={},
            csv_stats={"ambiguous_share_kind": 0, "known_unps": set(), "ambiguous_keys": set()},
            distilled_path=str(tmp_path / "ai_distilled_dividends.json"),
            distilled_data={},
            output_dir=str(output_dir),
            output_filename="share_payouts_by_unp.json",
        )

        process_result = SharePayoutProcessResult(
            export_data={
                "100000001": [
                    {
                        "share_uuid": "uuid-123",
                        "period_year": 2026,
                        "period_type": "annual",
                        "period_number": 1,
                        "amount_per_share": "0.50",
                        "decision_date": "2026-01-15",
                        "record_date": "2026-01-10",
                        "payment_date": "2026-02-15",
                    },
                ],
            },
            matched_count=1,
            skipped_file_errors=0,
            autofilled_share_type=0,
            missing_csv_unp=0,
            missing_share_kind=0,
            ambiguous_share_kind=0,
            samples={
                "missing_csv_unp": [],
                "missing_share_kind": [],
                "ambiguous_share_kind": [],
                "autofilled_share_type": [],
                "skipped_file_errors": [],
            },
        )

        await finalize_share_payout_export(scan_result, process_result)

        # Verify output file was created
        output_path = output_dir / "share_payouts_by_unp.json"
        assert output_path.exists()

        # Verify content
        content = json.loads(output_path.read_text(encoding="utf-8"))
        assert "100000001" in content
        assert len(content["100000001"]) == 1

    @pytest.mark.anyio
    async def test_calculates_total_unmatched_correctly(self, tmp_path):
        """Verifies total_unmatched sum is correct."""
        output_dir = tmp_path / "output"
        output_dir.mkdir(parents=True, exist_ok=True)

        scan_result = SharePayoutScanResult(
            csv_path=str(tmp_path / "shares.csv"),
            csv_index={},
            csv_stats={"ambiguous_share_kind": 5, "known_unps": set(), "ambiguous_keys": set()},
            distilled_path=str(tmp_path / "ai_distilled_dividends.json"),
            distilled_data={},
            output_dir=str(output_dir),
            output_filename="share_payouts_by_unp.json",
        )

        process_result = SharePayoutProcessResult(
            export_data={},
            matched_count=10,
            skipped_file_errors=2,
            autofilled_share_type=3,
            missing_csv_unp=4,
            missing_share_kind=5,
            ambiguous_share_kind=6,
            samples={
                "missing_csv_unp": [],
                "missing_share_kind": [],
                "ambiguous_share_kind": [],
                "autofilled_share_type": [],
                "skipped_file_errors": [],
            },
        )

        stats = await finalize_share_payout_export(scan_result, process_result)

        # total_unmatched = sum of all unmatched categories
        expected_unmatched = 2 + 3 + 4 + 5 + 6
        assert stats["unmatched_payouts"] == expected_unmatched

    @pytest.mark.anyio
    async def test_builds_final_stats_dict(self, tmp_path):
        """Verifies stats dict has all expected keys."""
        output_dir = tmp_path / "output"
        output_dir.mkdir(parents=True, exist_ok=True)

        scan_result = SharePayoutScanResult(
            csv_path=str(tmp_path / "shares.csv"),
            csv_index={},
            csv_stats={"ambiguous_share_kind": 0, "known_unps": set(), "ambiguous_keys": set()},
            distilled_path=str(tmp_path / "ai_distilled_dividends.json"),
            distilled_data={},
            output_dir=str(output_dir),
            output_filename="share_payouts_by_unp.json",
        )

        process_result = SharePayoutProcessResult(
            export_data={"100000001": []},
            matched_count=5,
            skipped_file_errors=1,
            autofilled_share_type=2,
            missing_csv_unp=3,
            missing_share_kind=4,
            ambiguous_share_kind=5,
            samples={
                "missing_csv_unp": [],
                "missing_share_kind": [],
                "ambiguous_share_kind": [],
                "autofilled_share_type": [],
                "skipped_file_errors": [],
            },
        )

        stats = await finalize_share_payout_export(scan_result, process_result)

        # Verify all expected keys are present
        expected_keys = [
            "output_path",
            "matched_payouts",
            "unmatched_payouts",
            "missing_csv_unp",
            "missing_share_kind",
            "ambiguous_share_kind",
            "csv_ambiguous_share_kind",
            "autofilled_share_type",
            "skipped_file_errors",
            "total_companies_exported",
            "unmatched_samples",
        ]
        for key in expected_keys:
            assert key in stats, f"Missing key: {key}"

        assert stats["matched_payouts"] == 5
        assert stats["total_companies_exported"] == 1

    @pytest.mark.anyio
    async def test_handles_empty_export_data(self, tmp_path):
        """Verifies graceful handling of empty export data."""
        output_dir = tmp_path / "output"
        output_dir.mkdir(parents=True, exist_ok=True)

        scan_result = SharePayoutScanResult(
            csv_path=str(tmp_path / "shares.csv"),
            csv_index={},
            csv_stats={"ambiguous_share_kind": 0, "known_unps": set(), "ambiguous_keys": set()},
            distilled_path=str(tmp_path / "ai_distilled_dividends.json"),
            distilled_data={},
            output_dir=str(output_dir),
            output_filename="share_payouts_by_unp.json",
        )

        process_result = SharePayoutProcessResult(
            export_data={},
            matched_count=0,
            skipped_file_errors=0,
            autofilled_share_type=0,
            missing_csv_unp=0,
            missing_share_kind=0,
            ambiguous_share_kind=0,
            samples={
                "missing_csv_unp": [],
                "missing_share_kind": [],
                "ambiguous_share_kind": [],
                "autofilled_share_type": [],
                "skipped_file_errors": [],
            },
        )

        stats = await finalize_share_payout_export(scan_result, process_result)

        assert stats["matched_payouts"] == 0
        assert stats["total_companies_exported"] == 0

        # Output file should exist but be empty JSON
        output_path = output_dir / "share_payouts_by_unp.json"
        assert output_path.exists()
        content = json.loads(output_path.read_text(encoding="utf-8"))
        assert content == {}

    @pytest.mark.anyio
    async def test_creates_parent_directories(self, tmp_path):
        """Verifies parent directories are created if needed."""
        output_dir = tmp_path / "output" / "nested" / "dir"

        scan_result = SharePayoutScanResult(
            csv_path=str(tmp_path / "shares.csv"),
            csv_index={},
            csv_stats={"ambiguous_share_kind": 0, "known_unps": set(), "ambiguous_keys": set()},
            distilled_path=str(tmp_path / "ai_distilled_dividends.json"),
            distilled_data={},
            output_dir=str(output_dir),
            output_filename="share_payouts_by_unp.json",
        )

        process_result = SharePayoutProcessResult(
            export_data={"100000001": []},
            matched_count=1,
            skipped_file_errors=0,
            autofilled_share_type=0,
            missing_csv_unp=0,
            missing_share_kind=0,
            ambiguous_share_kind=0,
            samples={
                "missing_csv_unp": [],
                "missing_share_kind": [],
                "ambiguous_share_kind": [],
                "autofilled_share_type": [],
                "skipped_file_errors": [],
            },
        )

        await finalize_share_payout_export(scan_result, process_result)

        # Verify nested directories were created
        assert output_dir.exists()
        output_path = output_dir / "share_payouts_by_unp.json"
        assert output_path.exists()

    @pytest.mark.anyio
    async def test_handles_cleanup_on_write_failure(self, tmp_path, monkeypatch):
        """Verifies tmp file is cleaned up on exception."""
        output_dir = tmp_path / "output"
        output_dir.mkdir(parents=True, exist_ok=True)

        scan_result = SharePayoutScanResult(
            csv_path=str(tmp_path / "shares.csv"),
            csv_index={},
            csv_stats={"ambiguous_share_kind": 0, "known_unps": set(), "ambiguous_keys": set()},
            distilled_path=str(tmp_path / "ai_distilled_dividends.json"),
            distilled_data={},
            output_dir=str(output_dir),
            output_filename="share_payouts_by_unp.json",
        )

        process_result = SharePayoutProcessResult(
            export_data={},
            matched_count=0,
            skipped_file_errors=0,
            autofilled_share_type=0,
            missing_csv_unp=0,
            missing_share_kind=0,
            ambiguous_share_kind=0,
            samples={
                "missing_csv_unp": [],
                "missing_share_kind": [],
                "ambiguous_share_kind": [],
                "autofilled_share_type": [],
                "skipped_file_errors": [],
            },
        )

        # Mock os.replace to raise an error
        def failing_replace(src, dst):
            raise OSError("Simulated write failure")

        with (
            patch("workflows.epfr.share_payout_exporter_workflow.os.replace", failing_replace),
            patch("workflows.epfr.share_payout_exporter_workflow.os.path.exists", return_value=True),
        ):
            with pytest.raises(RuntimeError) as exc_info:
                await finalize_share_payout_export(scan_result, process_result)

            assert "Failed to save export" in str(exc_info.value)


# =============================================================================
# TestGenerateSharePayoutSql - Activity 4 Tests
# =============================================================================


def _write_export_json(tmp_path, data):
    json_path = tmp_path / "share_payouts_by_unp.json"
    json_path.write_text(json.dumps(data), encoding="utf-8")
    return json_path


def _make_scan_result(tmp_path):
    return SharePayoutScanResult(
        csv_path=str(tmp_path / "shares.csv"),
        csv_index={},
        csv_stats={"ambiguous_share_kind": 0, "known_unps": set(), "ambiguous_keys": set()},
        distilled_path=str(tmp_path / "ai_distilled_dividends.json"),
        distilled_data={},
        output_dir=str(tmp_path),
        output_filename="share_payouts_by_unp.json",
    )


class TestGenerateSharePayoutSql:
    """Test Activity 4: generate_share_payout_sql.

    This activity generates SQL INSERT statements from the exported JSON.
    """

    @pytest.mark.anyio
    async def test_generates_sql_from_export_json(self, tmp_path):
        export_data = {
            "100000001": [
                {
                    "share_uuid": "uuid-common-1",
                    "period_year": 2026,
                    "period_type": "annual",
                    "period_number": 1,
                    "amount_per_share": "0.50",
                    "decision_date": "2026-01-15",
                    "record_date": "2026-01-10",
                    "payment_date": "2026-02-15",
                },
            ],
        }
        _write_export_json(tmp_path, export_data)
        scan_result = _make_scan_result(tmp_path)

        result = await generate_share_payout_sql(scan_result, {})

        assert "sql_path" in result
        assert "sql_records" in result
        assert result["sql_records"] == 1

        sql_path = tmp_path / "share_dividends_insert.sql"
        assert sql_path.exists()

        sql_text = sql_path.read_text(encoding="utf-8")
        assert "INSERT INTO public.share_dividend" in sql_text
        assert "uuid-common-1" in sql_text
        assert "ON CONFLICT DO NOTHING" in sql_text

    @pytest.mark.anyio
    async def test_sql_values_row_format(self, tmp_path):
        export_data = {
            "100000001": [
                {
                    "share_uuid": "uuid-abc",
                    "period_year": 2026,
                    "period_type": "annual",
                    "period_number": 1,
                    "amount_per_share": "0.50",
                    "decision_date": "2026-01-15",
                    "record_date": "2026-01-10",
                    "payment_date": "2026-02-15",
                },
            ],
        }
        _write_export_json(tmp_path, export_data)
        scan_result = _make_scan_result(tmp_path)

        await generate_share_payout_sql(scan_result, {})

        sql_text = (tmp_path / "share_dividends_insert.sql").read_text(encoding="utf-8")
        values_line = [line for line in sql_text.splitlines() if "uuid-abc" in line][0]

        assert "'uuid-abc'" in values_line
        assert "'annual'" in values_line
        assert "'2026-01-15'" in values_line
        assert "'2026-01-10'" in values_line
        assert "'2026-02-15'" in values_line
        assert "NOW(), NOW(), TRUE)" in values_line

    @pytest.mark.anyio
    async def test_deduplicates_identical_records(self, tmp_path):
        record = {
            "share_uuid": "uuid-dup",
            "period_year": 2026,
            "period_type": "annual",
            "period_number": 1,
            "amount_per_share": "0.50",
            "decision_date": "2026-01-15",
            "record_date": "2026-01-10",
            "payment_date": "2026-02-15",
        }
        export_data = {"100000001": [record], "200000002": [record]}
        _write_export_json(tmp_path, export_data)
        scan_result = _make_scan_result(tmp_path)

        result = await generate_share_payout_sql(scan_result, {})

        assert result["sql_records"] == 1

        sql_text = (tmp_path / "share_dividends_insert.sql").read_text(encoding="utf-8")
        values_lines = [line for line in sql_text.splitlines() if "uuid-dup" in line]
        assert len(values_lines) == 1

    @pytest.mark.anyio
    async def test_keeps_different_records(self, tmp_path):
        export_data = {
            "100000001": [
                {
                    "share_uuid": "uuid-a",
                    "period_year": 2026,
                    "period_type": "annual",
                    "period_number": 1,
                    "amount_per_share": "0.50",
                    "decision_date": "2026-01-15",
                    "record_date": "2026-01-10",
                    "payment_date": "2026-02-15",
                },
            ],
            "200000002": [
                {
                    "share_uuid": "uuid-b",
                    "period_year": 2025,
                    "period_type": "quarterly",
                    "period_number": 2,
                    "amount_per_share": "1.00",
                    "decision_date": "2025-06-01",
                    "record_date": "2025-05-15",
                    "payment_date": "2025-07-01",
                },
            ],
        }
        _write_export_json(tmp_path, export_data)
        scan_result = _make_scan_result(tmp_path)

        result = await generate_share_payout_sql(scan_result, {})

        assert result["sql_records"] == 2

        sql_text = (tmp_path / "share_dividends_insert.sql").read_text(encoding="utf-8")
        assert "uuid-a" in sql_text
        assert "uuid-b" in sql_text

    @pytest.mark.anyio
    async def test_calculates_missing_record_date_for_legacy_exports(self, tmp_path):
        export_data = {
            "100000001": [
                {
                    "share_uuid": "uuid-null",
                    "period_year": 2026,
                    "period_type": "annual",
                    "period_number": 1,
                    "amount_per_share": "0.50",
                    "decision_date": "2026-01-15",
                    "record_date": None,
                    "payment_date": "2026-02-15",
                },
            ],
        }
        _write_export_json(tmp_path, export_data)
        scan_result = _make_scan_result(tmp_path)

        result = await generate_share_payout_sql(scan_result, {})

        assert result["sql_records"] == 1

        sql_text = (tmp_path / "share_dividends_insert.sql").read_text(encoding="utf-8")
        values_line = [line for line in sql_text.splitlines() if "uuid-null" in line][0]
        assert "NULL" not in values_line
        assert "'2025-12-15'" in values_line

    @pytest.mark.anyio
    async def test_handles_empty_export_json(self, tmp_path):
        _write_export_json(tmp_path, {})
        scan_result = _make_scan_result(tmp_path)

        result = await generate_share_payout_sql(scan_result, {})

        assert result["sql_records"] == 0

        sql_path = tmp_path / "share_dividends_insert.sql"
        assert sql_path.exists()

        sql_text = sql_path.read_text(encoding="utf-8")
        assert "INSERT INTO public.share_dividend" in sql_text
        assert "ON CONFLICT DO NOTHING" in sql_text

        values_lines = [line for line in sql_text.splitlines() if line.strip().startswith("(")]
        assert len(values_lines) == 0

    @pytest.mark.anyio
    async def test_sql_file_written_atomically(self, tmp_path):
        export_data = {
            "100000001": [
                {
                    "share_uuid": "uuid-atomic",
                    "period_year": 2026,
                    "period_type": "annual",
                    "period_number": 1,
                    "amount_per_share": "0.50",
                    "decision_date": "2026-01-15",
                    "record_date": "2026-01-10",
                    "payment_date": "2026-02-15",
                },
            ],
        }
        _write_export_json(tmp_path, export_data)
        scan_result = _make_scan_result(tmp_path)

        await generate_share_payout_sql(scan_result, {})

        temp_files = [f for f in tmp_path.iterdir() if f.suffix == ".sql" and f.name.startswith("tmp")]
        assert len(temp_files) == 0

        sql_path = tmp_path / "share_dividends_insert.sql"
        assert sql_path.exists()

    @pytest.mark.anyio
    async def test_raises_file_not_found_for_missing_json(self, tmp_path):
        scan_result = _make_scan_result(tmp_path)

        with pytest.raises(FileNotFoundError) as exc_info:
            await generate_share_payout_sql(scan_result, {})

        assert "JSON file not found" in str(exc_info.value)

    @pytest.mark.anyio
    async def test_handles_write_failure(self, tmp_path):
        export_data = {
            "100000001": [
                {
                    "share_uuid": "uuid-fail",
                    "period_year": 2026,
                    "period_type": "annual",
                    "period_number": 1,
                    "amount_per_share": "0.50",
                    "decision_date": "2026-01-15",
                    "record_date": "2026-01-10",
                    "payment_date": "2026-02-15",
                },
            ],
        }
        _write_export_json(tmp_path, export_data)
        scan_result = _make_scan_result(tmp_path)

        def failing_replace(src, dst):
            raise OSError("Simulated write failure")

        with (
            patch("workflows.epfr.share_payout_exporter_workflow.os.replace", failing_replace),
            patch("workflows.epfr.share_payout_exporter_workflow.os.path.exists", return_value=True),
        ):
            with pytest.raises(RuntimeError) as exc_info:
                await generate_share_payout_sql(scan_result, {})

            assert "Failed to write SQL file" in str(exc_info.value)

    @pytest.mark.anyio
    async def test_sql_header_comments(self, tmp_path):
        export_data = {
            "100000001": [
                {
                    "share_uuid": "uuid-hdr",
                    "period_year": 2026,
                    "period_type": "annual",
                    "period_number": 1,
                    "amount_per_share": "0.50",
                    "decision_date": "2026-01-15",
                    "record_date": "2026-01-10",
                    "payment_date": "2026-02-15",
                },
            ],
        }
        _write_export_json(tmp_path, export_data)
        scan_result = _make_scan_result(tmp_path)

        await generate_share_payout_sql(scan_result, {})

        sql_text = (tmp_path / "share_dividends_insert.sql").read_text(encoding="utf-8")
        lines = sql_text.splitlines()
        assert lines[0] == "-- Generated from share_payouts_by_unp.json"
        assert lines[1] == "-- Total: 1 unique records"

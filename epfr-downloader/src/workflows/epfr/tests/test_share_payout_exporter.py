"""Tests for share payout export workflow: config, CSV loading, export logic, and discovery."""

# ruff: noqa: D102

import json
from pathlib import Path

from .. import config
from ..models import EpfrSharePayoutExportInput
from ..share_payout_exporter import load_share_reference_index, run_share_payout_export


class TestExportConfigDefaults:
    """Validate export-specific constants defined in config.py."""

    def test_workflow_name(self):
        assert config.SHARE_PAYOUT_EXPORT_WORKFLOW_NAME == "epfr-share-payout-exporter"

    def test_export_filename(self):
        assert config.SHARE_PAYOUT_EXPORT_FILENAME == "share_payouts_by_unp.json"

    def test_source_data_csv_is_path(self):
        assert isinstance(config.SHARES_SOURCE_DATA_CSV, Path)

    def test_source_data_csv_filename(self):
        assert config.SHARES_SOURCE_DATA_CSV.name == "shares_source_data.csv"

    def test_source_data_csv_parent_is_repo_root(self):
        assert config.SHARES_SOURCE_DATA_CSV.parent.name == "workflows"


class TestLoadShareReferenceIndex:
    """Tests for CSV loading and ambiguity handling."""

    def test_happy_path_single_match(self, tmp_path):
        """UNP with one common row resolves to its instrument_uuid."""
        csv_path = tmp_path / "shares.csv"
        csv_path.write_text(
            "name,unp,instrument_uuid,ticker,share_kind\nTestCo,600073968,uuid-common-1,T1715,common\n",
            encoding="utf-8",
        )
        index, stats = load_share_reference_index(csv_path)
        assert index[("600073968", "common")] == "uuid-common-1"
        assert stats["ambiguous_share_kind"] == 0

    def test_unp_with_common_and_preferred(self, tmp_path):
        """UNP with both common and preferred rows resolves both."""
        csv_path = tmp_path / "shares.csv"
        csv_path.write_text(
            "name,unp,instrument_uuid,ticker,share_kind\n"
            "TestCo,600073968,uuid-common-1,T1715,common\n"
            "TestCo,600073968,uuid-pref-1,T1715P,preferred\n",
            encoding="utf-8",
        )
        index, stats = load_share_reference_index(csv_path)
        assert index[("600073968", "common")] == "uuid-common-1"
        assert index[("600073968", "preferred")] == "uuid-pref-1"
        assert stats["ambiguous_share_kind"] == 0

    def test_ambiguous_preferred_skipped(self, tmp_path):
        """UNP with two preferred rows → key excluded, counted as ambiguous."""
        csv_path = tmp_path / "shares.csv"
        csv_path.write_text(
            "name,unp,instrument_uuid,ticker,share_kind\n"
            "Bank,100325912,uuid-pref-a,T001,preferred\n"
            "Bank,100325912,uuid-pref-b,T002,preferred\n",
            encoding="utf-8",
        )
        index, stats = load_share_reference_index(csv_path)
        assert ("100325912", "preferred") not in index
        assert stats["ambiguous_share_kind"] == 2

    def test_ambiguous_common_plus_preferred_with_duplicate(self, tmp_path):
        """UNP with common + 2 preferred: common resolves, preferred is ambiguous."""
        csv_path = tmp_path / "shares.csv"
        csv_path.write_text(
            "name,unp,instrument_uuid,ticker,share_kind\n"
            "Bank,100325912,uuid-common-1,T001,common\n"
            "Bank,100325912,uuid-pref-a,T002,preferred\n"
            "Bank,100325912,uuid-pref-b,T003,preferred\n",
            encoding="utf-8",
        )
        index, stats = load_share_reference_index(csv_path)
        assert index[("100325912", "common")] == "uuid-common-1"
        assert ("100325912", "preferred") not in index
        assert stats["ambiguous_share_kind"] == 2

    def test_empty_csv(self, tmp_path):
        """Empty CSV (header only) produces empty index."""
        csv_path = tmp_path / "shares.csv"
        csv_path.write_text("name,unp,instrument_uuid,ticker,share_kind\n", encoding="utf-8")
        index, stats = load_share_reference_index(csv_path)
        assert index == {}
        assert stats["ambiguous_share_kind"] == 0


def _make_distilled_json(companies: dict) -> str:
    """Build a minimal ai_distilled_dividends.json structure."""
    result = {}
    for unp, company in companies.items():
        result[unp] = {
            "company_name": company.get("company_name", f"Co-{unp}"),
            "unp": unp,
            "holder_id": company.get("holder_id", 1),
            "files": company["files"],
        }
    return json.dumps(result, ensure_ascii=False)


def _write_csv(tmp_path: Path, rows: list[str]) -> Path:
    csv_path = tmp_path / "shares.csv"
    header = "name,unp,instrument_uuid,ticker,share_kind\n"
    csv_path.write_text(header + "\n".join(rows) + "\n", encoding="utf-8")
    return csv_path


def _make_dividend(
    share_type: str = "common",
    period_year: int = 2026,
    period_type: str = "quarterly",
    period_number: int = 1,
    amount_per_share: str = "46.73",
    decision_date: str = "2026-05-04",
    record_date: str = "2026-04-04",
    payment_date: str = "2026-06-10",
) -> dict:
    return {
        "share_type": share_type,
        "period_year": period_year,
        "period_type": period_type,
        "period_number": period_number,
        "amount_per_share": amount_per_share,
        "decision_date": decision_date,
        "record_date": record_date,
        "payment_date": payment_date,
    }


def _make_file(
    dividends: list[dict] | None = None,
    autofilled_fields: list[str] | None = None,
    error: str | None = None,
    file_id: int = 100,
) -> dict:
    return {
        "id": file_id,
        "filename": f"{file_id}.md",
        "dividends": dividends or [],
        "autofilled_fields": autofilled_fields or [],
        "error": error,
    }


def _setup_export(tmp_path: Path, companies: dict, csv_rows: list[str]) -> tuple[Path, EpfrSharePayoutExportInput]:
    distilled_path = tmp_path / "ai_distilled_dividends.json"
    distilled_path.write_text(_make_distilled_json(companies), encoding="utf-8")
    csv_path = _write_csv(tmp_path, csv_rows)
    inp = EpfrSharePayoutExportInput(
        output_dir=str(tmp_path),
        input_filename="ai_distilled_dividends.json",
        output_filename="share_payouts_by_unp.json",
        shares_csv_path=str(csv_path),
    )
    return tmp_path, inp


class TestRunSharePayoutExport:
    def test_happy_path_common_match(self, tmp_path):
        _, inp = _setup_export(
            tmp_path,
            {"600073968": {"files": [_make_file(dividends=[_make_dividend()])]}},
            ["TestCo,600073968,uuid-common-1,T1715,common"],
        )
        stats = run_share_payout_export(inp)
        export = json.loads((tmp_path / "share_payouts_by_unp.json").read_text(encoding="utf-8"))
        assert len(export["600073968"]) == 1
        assert export["600073968"][0]["share_uuid"] == "uuid-common-1"
        assert stats["matched_payouts"] == 1

    def test_zero_amount_preserved(self, tmp_path):
        _, inp = _setup_export(
            tmp_path,
            {"600073968": {"files": [_make_file(dividends=[_make_dividend(amount_per_share="0")])]}},
            ["TestCo,600073968,uuid-common-1,T1715,common"],
        )
        stats = run_share_payout_export(inp)
        export = json.loads((tmp_path / "share_payouts_by_unp.json").read_text(encoding="utf-8"))
        assert export["600073968"][0]["amount_per_share"] == "0"
        assert stats["matched_payouts"] == 1

    def test_preferred_match(self, tmp_path):
        _, inp = _setup_export(
            tmp_path,
            {"100325912": {"files": [_make_file(dividends=[_make_dividend(share_type="preferred")])]}},
            ["Bank,100325912,uuid-pref-1,T001,preferred"],
        )
        stats = run_share_payout_export(inp)
        export = json.loads((tmp_path / "share_payouts_by_unp.json").read_text(encoding="utf-8"))
        assert export["100325912"][0]["share_uuid"] == "uuid-pref-1"
        assert stats["matched_payouts"] == 1

    def test_autofilled_share_type_skipped(self, tmp_path):
        _, inp = _setup_export(
            tmp_path,
            {"600073968": {"files": [_make_file(dividends=[_make_dividend()], autofilled_fields=["share_type"])]}},
            ["TestCo,600073968,uuid-common-1,T1715,common"],
        )
        stats = run_share_payout_export(inp)
        assert stats["autofilled_share_type"] == 1
        assert stats["matched_payouts"] == 0
        assert stats["unmatched_samples"]["autofilled_share_type"][0]["unp"] == "600073968"

    def test_file_error_skipped(self, tmp_path):
        _, inp = _setup_export(
            tmp_path,
            {"600073968": {"files": [_make_file(dividends=[_make_dividend()], error="parse failed")]}},
            ["TestCo,600073968,uuid-common-1,T1715,common"],
        )
        stats = run_share_payout_export(inp)
        assert stats["skipped_file_errors"] == 1
        assert stats["matched_payouts"] == 0

    def test_missing_share_kind(self, tmp_path):
        _, inp = _setup_export(
            tmp_path,
            {"600073968": {"files": [_make_file(dividends=[_make_dividend(share_type="preferred")])]}},
            ["TestCo,600073968,uuid-common-1,T1715,common"],
        )
        stats = run_share_payout_export(inp)
        assert stats["missing_share_kind"] == 1
        assert stats["missing_csv_unp"] == 0
        assert stats["matched_payouts"] == 0

    def test_ambiguous_share_kind_in_export(self, tmp_path):
        _, inp = _setup_export(
            tmp_path,
            {"100325912": {"files": [_make_file(dividends=[_make_dividend(share_type="preferred")])]}},
            [
                "Bank,100325912,uuid-common-1,T001,common",
                "Bank,100325912,uuid-pref-a,T002,preferred",
                "Bank,100325912,uuid-pref-b,T003,preferred",
            ],
        )
        stats = run_share_payout_export(inp)
        assert stats["ambiguous_share_kind"] == 1
        assert stats["missing_share_kind"] == 0
        assert stats["missing_csv_unp"] == 0

    def test_file_error_counts_dividends_not_files(self, tmp_path):
        _, inp = _setup_export(
            tmp_path,
            {
                "600073968": {
                    "files": [
                        _make_file(
                            dividends=[
                                _make_dividend(),
                                _make_dividend(amount_per_share="1.5"),
                                _make_dividend(amount_per_share="2.0"),
                            ],
                            error="parse failed",
                        )
                    ]
                }
            },
            ["TestCo,600073968,uuid-common-1,T1715,common"],
        )
        stats = run_share_payout_export(inp)
        assert stats["skipped_file_errors"] == 3
        assert stats["matched_payouts"] == 0

    def test_missing_csv_unp_skipped(self, tmp_path):
        _, inp = _setup_export(
            tmp_path,
            {"999999999": {"files": [_make_file(dividends=[_make_dividend()])]}},
            ["TestCo,600073968,uuid-common-1,T1715,common"],
        )
        stats = run_share_payout_export(inp)
        assert stats["missing_csv_unp"] == 1
        assert stats["missing_share_kind"] == 0
        assert stats["matched_payouts"] == 0
        assert stats["unmatched_samples"]["missing_csv_unp"][0]["unp"] == "999999999"

    def test_multiple_unps(self, tmp_path):
        _, inp = _setup_export(
            tmp_path,
            {
                "600073968": {"files": [_make_file(dividends=[_make_dividend()])]},
                "100325912": {"files": [_make_file(dividends=[_make_dividend(share_type="preferred")])]},
            },
            [
                "TestCo,600073968,uuid-common-1,T1715,common",
                "Bank,100325912,uuid-pref-1,T001,preferred",
            ],
        )
        stats = run_share_payout_export(inp)
        export = json.loads((tmp_path / "share_payouts_by_unp.json").read_text(encoding="utf-8"))
        assert "600073968" in export
        assert "100325912" in export
        assert stats["total_companies_exported"] == 2

    def test_export_shape_is_pure(self, tmp_path):
        _, inp = _setup_export(
            tmp_path,
            {"600073968": {"files": [_make_file(dividends=[_make_dividend()])]}},
            ["TestCo,600073968,uuid-common-1,T1715,common"],
        )
        run_share_payout_export(inp)
        export = json.loads((tmp_path / "share_payouts_by_unp.json").read_text(encoding="utf-8"))
        row = export["600073968"][0]
        for forbidden in ("company_name", "holder_id", "files", "stats", "unp"):
            assert forbidden not in row
        assert set(row.keys()) == {
            "share_uuid",
            "period_year",
            "period_type",
            "period_number",
            "amount_per_share",
            "decision_date",
            "record_date",
            "payment_date",
        }

    def test_only_unps_with_matches_in_export(self, tmp_path):
        _, inp = _setup_export(
            tmp_path,
            {
                "600073968": {"files": [_make_file(dividends=[_make_dividend()])]},
                "999999999": {"files": [_make_file(dividends=[_make_dividend()])]},
            },
            ["TestCo,600073968,uuid-common-1,T1715,common"],
        )
        run_share_payout_export(inp)
        export = json.loads((tmp_path / "share_payouts_by_unp.json").read_text(encoding="utf-8"))
        assert "600073968" in export
        assert "999999999" not in export

    def test_output_file_written_atomically(self, tmp_path):
        _, inp = _setup_export(
            tmp_path,
            {"600073968": {"files": [_make_file(dividends=[_make_dividend()])]}},
            ["TestCo,600073968,uuid-common-1,T1715,common"],
        )
        stats = run_share_payout_export(inp)
        output_path = tmp_path / "share_payouts_by_unp.json"
        assert output_path.exists()
        assert stats["output_path"] == str(output_path)

    def test_custom_csv_path_override(self, tmp_path):
        distilled_path = tmp_path / "ai_distilled_dividends.json"
        distilled_path.write_text(
            _make_distilled_json({"600073968": {"files": [_make_file(dividends=[_make_dividend()])]}}),
            encoding="utf-8",
        )
        custom_csv = tmp_path / "custom.csv"
        custom_csv.write_text(
            "name,unp,instrument_uuid,ticker,share_kind\nCustom,600073968,uuid-custom,T999,common\n",
            encoding="utf-8",
        )
        inp = EpfrSharePayoutExportInput(
            output_dir=str(tmp_path),
            input_filename="ai_distilled_dividends.json",
            output_filename="share_payouts_by_unp.json",
            shares_csv_path=str(custom_csv),
        )
        run_share_payout_export(inp)
        export = json.loads((tmp_path / "share_payouts_by_unp.json").read_text(encoding="utf-8"))
        assert export["600073968"][0]["share_uuid"] == "uuid-custom"

    def test_mixed_autofilled_and_matched_files(self, tmp_path):
        """Same UNP with 2 files: one autofilled share_type (skipped), one normal (matched)."""
        _, inp = _setup_export(
            tmp_path,
            {
                "600073968": {
                    "files": [
                        _make_file(
                            dividends=[_make_dividend()],
                            autofilled_fields=["share_type"],
                            file_id=100,
                        ),
                        _make_file(
                            dividends=[_make_dividend()],
                            autofilled_fields=[],
                            file_id=200,
                        ),
                    ]
                }
            },
            ["TestCo,600073968,uuid-common-1,T1715,common"],
        )
        stats = run_share_payout_export(inp)
        assert stats["matched_payouts"] == 1
        assert stats["autofilled_share_type"] == 1

    def test_default_csv_path(self, tmp_path):
        """When shares_csv_path is empty, exporter falls back to SHARES_SOURCE_DATA_CSV."""
        from unittest.mock import patch

        distilled_path = tmp_path / "ai_distilled_dividends.json"
        distilled_path.write_text(
            _make_distilled_json({"600073968": {"files": [_make_file(dividends=[_make_dividend()])]}}),
            encoding="utf-8",
        )
        inp = EpfrSharePayoutExportInput(
            output_dir=str(tmp_path),
            input_filename="ai_distilled_dividends.json",
            output_filename="share_payouts_by_unp.json",
            shares_csv_path="",
        )
        with patch("workflows.epfr.share_payout_exporter.load_share_reference_index") as mock_load:
            mock_load.return_value = (
                {("600073968", "common"): "uuid-common-1"},
                {"known_unps": {"600073968"}, "ambiguous_keys": set(), "ambiguous_share_kind": 0},
            )
            stats = run_share_payout_export(inp)
        mock_load.assert_called_once_with(config.SHARES_SOURCE_DATA_CSV)
        assert stats["matched_payouts"] == 1


class TestWorkflowDiscovery:
    """Verify the new workflow is auto-discovered."""

    def test_workflow_name_in_discovery(self):
        """The share payout exporter workflow name appears in discovered workflows."""
        import os

        os.environ.pop("AGENT", None)
        from mistralai.workflows.core.definition.workflow_definition import get_workflow_definition

        from discover import discover_workflows

        names = sorted(get_workflow_definition(wf).name for wf in discover_workflows())
        assert "epfr-share-payout-exporter" in names

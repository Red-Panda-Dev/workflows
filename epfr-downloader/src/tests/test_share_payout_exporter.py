"""Tests for share payout export workflow: config, CSV loading, export logic, and discovery."""

# ruff: noqa: D102

import json
import os
from pathlib import Path
from unittest.mock import patch

os.environ.pop("AGENT", None)

import pytest

from workflows.epfr import config
from workflows.epfr.models import EpfrSharePayoutExportInput
from workflows.epfr.share_payout_exporter import load_share_reference_index, run_share_payout_export
from workflows.epfr.share_payout_exporter_workflow import EpfrSharePayoutExporterWorkflow, export_share_payouts


class TestExportConfigDefaults:
    """Validate export-specific constants defined in config.py."""

    def test_workflow_name(self):
        assert config.SHARE_PAYOUT_EXPORT_WORKFLOW_NAME == "epfr-share-payout-exporter"

    def test_export_filename(self):
        assert config.SHARE_PAYOUT_EXPORT_FILENAME == "share_payouts_by_unp.json"

    def test_source_data_csv_is_path(self):
        assert isinstance(config.get_shares_source_data_csv(), Path)

    def test_source_data_csv_filename(self):
        assert config.get_shares_source_data_csv().name == "shares_source_data.csv"

    def test_source_data_csv_parent_is_repo_root(self):
        assert config.get_shares_source_data_csv().parent.name == "workflows"


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
        mock_load.assert_called_once_with(config.get_shares_source_data_csv())
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


_EXPECTED_CSV_ROWS = [
    "ЭНЭФ,600073968,76e720e1,T1715,common",
    "Победа,200100116,c16b0227,T1716,common",
    "Первомайск-агро,591867699,1ced04f8,T1717,common",
    "Круглянская Искра,700107602,f1e583c8,T1718,common",
    "Мстиславчанка,700160772,f6ba014b,T1719,common",
    "Хальч,400053193,c4bdbe76,T1720,common",
    "Витебские ковры,300082076,c72add2d,T1721,common",
    "ЦБТ,101349508,5a34e878,T1722,common",
    "Маяк Высокое,300009076,f06e7226,T1723,common",
    "Слуцкая фабрика,600154407,793a4216,T1724,common",
    "ХЦ-ПОЛИНОВОТЕХ,100152790,1e2f04dd,T1725,common",
]


def _setup_real_fixture_export(
    tmp_path: Path,
    distilled_data: dict,
    csv_rows: list[str] | None = None,
) -> EpfrSharePayoutExportInput:
    """Write distilled JSON + CSV to tmp_path and return export input."""
    distilled_path = tmp_path / "ai_distilled_dividends.json"
    distilled_path.write_text(json.dumps(distilled_data, ensure_ascii=False), encoding="utf-8")
    rows = csv_rows if csv_rows is not None else _EXPECTED_CSV_ROWS
    csv_path = _write_csv(tmp_path, rows)
    return EpfrSharePayoutExportInput(
        output_dir=str(tmp_path),
        input_filename="ai_distilled_dividends.json",
        output_filename="share_payouts_by_unp.json",
        shares_csv_path=str(csv_path),
    )


class TestRealFixtureExport:
    """Export tests using committed ai_distilled_dividends.json and share_payouts_by_unp.json fixtures."""

    def test_expected_unp_keys_present(self, tmp_path, load_epfr_fixture_json):
        """All UNP keys from the expected output fixture appear in the generated export."""
        distilled = load_epfr_fixture_json("ai_distilled_dividends.json")
        expected = load_epfr_fixture_json("share_payouts_by_unp.json")

        inp = _setup_real_fixture_export(tmp_path, distilled)
        run_share_payout_export(inp)

        export = json.loads((tmp_path / "share_payouts_by_unp.json").read_text(encoding="utf-8"))
        for unp_key in expected:
            assert unp_key in export, f"Expected UNP key {unp_key!r} missing from export"

    def test_payout_count_per_unp(self, tmp_path, load_epfr_fixture_json):
        """Number of payout rows per UNP matches the expected fixture."""
        distilled = load_epfr_fixture_json("ai_distilled_dividends.json")
        expected = load_epfr_fixture_json("share_payouts_by_unp.json")

        inp = _setup_real_fixture_export(tmp_path, distilled)
        run_share_payout_export(inp)

        export = json.loads((tmp_path / "share_payouts_by_unp.json").read_text(encoding="utf-8"))
        for unp_key, expected_rows in expected.items():
            assert len(export[unp_key]) == len(expected_rows), (
                f"UNP {unp_key}: expected {len(expected_rows)} payouts, got {len(export[unp_key])}"
            )

    def test_200100116_exact_fields(self, tmp_path, load_epfr_fixture_json):
        """UNP 200100116 (Победа): exact field match for the single payout entry."""
        distilled = load_epfr_fixture_json("ai_distilled_dividends.json")
        expected = load_epfr_fixture_json("share_payouts_by_unp.json")

        inp = _setup_real_fixture_export(tmp_path, distilled)
        run_share_payout_export(inp)

        export = json.loads((tmp_path / "share_payouts_by_unp.json").read_text(encoding="utf-8"))
        actual = export["200100116"][0]
        expected_row = expected["200100116"][0]
        assert actual["share_uuid"] == expected_row["share_uuid"]
        assert actual["period_year"] == expected_row["period_year"]
        assert actual["period_type"] == expected_row["period_type"]
        assert actual["period_number"] == expected_row["period_number"]
        assert actual["amount_per_share"] == expected_row["amount_per_share"]
        assert actual["decision_date"] == expected_row["decision_date"]
        assert actual["record_date"] == expected_row["record_date"]
        assert actual["payment_date"] == expected_row["payment_date"]

    def test_101349508_exact_fields(self, tmp_path, load_epfr_fixture_json):
        """UNP 101349508 (ЦБТ): exact field match for the single payout entry."""
        distilled = load_epfr_fixture_json("ai_distilled_dividends.json")
        expected = load_epfr_fixture_json("share_payouts_by_unp.json")

        inp = _setup_real_fixture_export(tmp_path, distilled)
        run_share_payout_export(inp)

        export = json.loads((tmp_path / "share_payouts_by_unp.json").read_text(encoding="utf-8"))
        actual = export["101349508"][0]
        expected_row = expected["101349508"][0]
        assert actual["share_uuid"] == expected_row["share_uuid"]
        assert actual["amount_per_share"] == expected_row["amount_per_share"]
        assert actual["decision_date"] == expected_row["decision_date"]
        assert actual["payment_date"] == expected_row["payment_date"]

    def test_100152790_exact_fields(self, tmp_path, load_epfr_fixture_json):
        """UNP 100152790 (ХЦ-ПОЛИНОВОТЕХ): exact field match for the single payout entry."""
        distilled = load_epfr_fixture_json("ai_distilled_dividends.json")
        expected = load_epfr_fixture_json("share_payouts_by_unp.json")

        inp = _setup_real_fixture_export(tmp_path, distilled)
        run_share_payout_export(inp)

        export = json.loads((tmp_path / "share_payouts_by_unp.json").read_text(encoding="utf-8"))
        actual = export["100152790"][0]
        expected_row = expected["100152790"][0]
        assert actual["share_uuid"] == expected_row["share_uuid"]
        assert actual["amount_per_share"] == expected_row["amount_per_share"]
        assert actual["period_year"] == expected_row["period_year"]
        assert actual["payment_date"] == expected_row["payment_date"]

    def test_700160772_duplicate_file_produces_two_rows(self, tmp_path, load_epfr_fixture_json):
        """UNP 700160772 has two identical file entries → export has two payout rows."""
        distilled = load_epfr_fixture_json("ai_distilled_dividends.json")

        inp = _setup_real_fixture_export(tmp_path, distilled)
        run_share_payout_export(inp)

        export = json.loads((tmp_path / "share_payouts_by_unp.json").read_text(encoding="utf-8"))
        assert len(export["700160772"]) == 2
        assert export["700160772"][0]["share_uuid"] == "f6ba014b"
        assert export["700160772"][1]["share_uuid"] == "f6ba014b"

    def test_share_uuid_from_csv_not_distilled(self, tmp_path, load_epfr_fixture_json):
        """share_uuid comes from the CSV index, not from the distilled data."""
        distilled = load_epfr_fixture_json("ai_distilled_dividends.json")

        csv_rows = [r for r in _EXPECTED_CSV_ROWS if not r.startswith("Победа,200100116")]
        csv_rows.append("Победа,200100116,custom-uuid-xyz,T1716,common")

        inp = _setup_real_fixture_export(tmp_path, distilled, csv_rows=csv_rows)
        run_share_payout_export(inp)

        export = json.loads((tmp_path / "share_payouts_by_unp.json").read_text(encoding="utf-8"))
        assert export["200100116"][0]["share_uuid"] == "custom-uuid-xyz"

    def test_total_companies_exported(self, tmp_path, load_epfr_fixture_json):
        """Stats report the correct number of exported companies."""
        distilled = load_epfr_fixture_json("ai_distilled_dividends.json")
        expected = load_epfr_fixture_json("share_payouts_by_unp.json")

        inp = _setup_real_fixture_export(tmp_path, distilled)
        stats = run_share_payout_export(inp)

        export = json.loads((tmp_path / "share_payouts_by_unp.json").read_text(encoding="utf-8"))
        assert stats["total_companies_exported"] == len(export)
        assert stats["total_companies_exported"] >= len(expected)

    def test_export_row_shape_matches_schema(self, tmp_path, load_epfr_fixture_json):
        """Every exported row has exactly the fields defined by EpfrSharePayoutExportRow."""
        distilled = load_epfr_fixture_json("ai_distilled_dividends.json")

        inp = _setup_real_fixture_export(tmp_path, distilled)
        run_share_payout_export(inp)

        export = json.loads((tmp_path / "share_payouts_by_unp.json").read_text(encoding="utf-8"))
        expected_keys = {
            "share_uuid",
            "period_year",
            "period_type",
            "period_number",
            "amount_per_share",
            "decision_date",
            "record_date",
            "payment_date",
        }
        for unp_key, rows in export.items():
            for row in rows:
                assert set(row.keys()) == expected_keys, (
                    f"UNP {unp_key}: unexpected keys {set(row.keys()) - expected_keys}"
                )


class TestRealFixtureEdgeCases:
    """Edge-case tests derived from the real fixture structure."""

    def test_empty_dividend_list_produces_no_output(self, tmp_path, load_epfr_fixture_json):
        """A UNP whose files have empty dividend lists does not appear in export."""
        distilled = load_epfr_fixture_json("ai_distilled_dividends.json")
        distilled["200100116"]["files"][0]["dividends"] = []

        inp = _setup_real_fixture_export(tmp_path, distilled)
        stats = run_share_payout_export(inp)

        export = json.loads((tmp_path / "share_payouts_by_unp.json").read_text(encoding="utf-8"))
        assert "200100116" not in export
        assert stats["matched_payouts"] == 0 or "200100116" not in [
            r.get("unp", "") for r in stats.get("unmatched_samples", {}).get("missing_csv_unp", [])
        ]

    def test_unknown_unp_skipped(self, tmp_path, load_epfr_fixture_json):
        """A UNP not present in the CSV index is skipped (missing_csv_unp)."""
        distilled = load_epfr_fixture_json("ai_distilled_dividends.json")
        distilled["999999999"] = {
            "company_name": "Unknown Co",
            "unp": "999999999",
            "holder_id": 0,
            "files": [
                {
                    "id": 99999,
                    "filename": "99999.md",
                    "dividends": [_make_dividend()],
                    "autofilled_fields": [],
                    "error": None,
                }
            ],
        }

        inp = _setup_real_fixture_export(tmp_path, distilled)
        stats = run_share_payout_export(inp)

        export = json.loads((tmp_path / "share_payouts_by_unp.json").read_text(encoding="utf-8"))
        assert "999999999" not in export
        assert stats["missing_csv_unp"] >= 1

    def test_no_mistral_api_key_required(self, tmp_path, load_epfr_fixture_json):
        """Export runs successfully without MISTRAL_API_KEY in the environment."""
        import os

        key_backup = os.environ.pop("MISTRAL_API_KEY", None)
        try:
            distilled = load_epfr_fixture_json("ai_distilled_dividends.json")
            inp = _setup_real_fixture_export(tmp_path, distilled)
            stats = run_share_payout_export(inp)
            assert stats["matched_payouts"] > 0
        finally:
            if key_backup is not None:
                os.environ["MISTRAL_API_KEY"] = key_backup

    def test_file_with_error_skipped(self, tmp_path, load_epfr_fixture_json):
        """A file with a non-null error field has its dividends skipped."""
        distilled = load_epfr_fixture_json("ai_distilled_dividends.json")
        distilled["200100116"]["files"][0]["error"] = "ValidationError: something"

        inp = _setup_real_fixture_export(tmp_path, distilled)
        stats = run_share_payout_export(inp)

        export = json.loads((tmp_path / "share_payouts_by_unp.json").read_text(encoding="utf-8"))
        assert "200100116" not in export
        assert stats["skipped_file_errors"] >= 1

    def test_autofilled_share_type_skipped(self, tmp_path, load_epfr_fixture_json):
        """Dividends from files where share_type was autofilled are skipped."""
        distilled = load_epfr_fixture_json("ai_distilled_dividends.json")
        distilled["200100116"]["files"][0]["autofilled_fields"] = ["share_type"]

        inp = _setup_real_fixture_export(tmp_path, distilled)
        stats = run_share_payout_export(inp)

        export = json.loads((tmp_path / "share_payouts_by_unp.json").read_text(encoding="utf-8"))
        assert "200100116" not in export
        assert stats["autofilled_share_type"] >= 1


class TestExportSharePayoutsActivity:
    """Tests for the export_share_payouts activity wrapper."""

    @pytest.mark.anyio
    async def test_happy_path_with_fixture(self, tmp_path, load_epfr_fixture_json):
        """Activity resolves input and delegates to run_share_payout_export."""
        distilled = load_epfr_fixture_json("ai_distilled_dividends.json")
        inp = _setup_real_fixture_export(tmp_path, distilled)

        result = await export_share_payouts(inp)

        assert result["matched_payouts"] > 0
        assert (tmp_path / "share_payouts_by_unp.json").exists()

    @pytest.mark.anyio
    async def test_preserves_shares_csv_path(self, tmp_path, load_epfr_fixture_json):
        """Activity passes shares_csv_path through even after resolving other fields."""
        distilled = load_epfr_fixture_json("ai_distilled_dividends.json")
        inp = _setup_real_fixture_export(tmp_path, distilled)
        custom_csv = str(config.get_shares_source_data_csv())
        inp = EpfrSharePayoutExportInput(
            output_dir=inp.output_dir,
            input_filename=inp.input_filename,
            output_filename=inp.output_filename,
            shares_csv_path=custom_csv,
        )

        with patch(
            "workflows.epfr.share_payout_exporter_workflow.run_share_payout_export",
            return_value={
                "matched_payouts": 1,
                "unmatched_payouts": 0,
                "total_companies_exported": 1,
                "output_path": "/tmp/x",
            },
        ) as mock_run:
            await export_share_payouts(inp)

        passed_input = mock_run.call_args[0][0]
        assert passed_input.shares_csv_path == custom_csv

    @pytest.mark.anyio
    async def test_resolves_none_fields_from_config(self, tmp_path, load_epfr_fixture_json):
        """Activity calls resolve_share_payout_export_input for None fields."""
        distilled = load_epfr_fixture_json("ai_distilled_dividends.json")
        inp = _setup_real_fixture_export(tmp_path, distilled)
        inp = EpfrSharePayoutExportInput(
            output_dir=None,
            input_filename=None,
            output_filename=None,
            shares_csv_path=inp.shares_csv_path,
        )

        with patch(
            "workflows.epfr.share_payout_exporter_workflow.run_share_payout_export",
            return_value={
                "matched_payouts": 0,
                "unmatched_payouts": 0,
                "total_companies_exported": 0,
                "output_path": "",
            },
        ) as mock_run:
            await export_share_payouts(inp)

        passed_input = mock_run.call_args[0][0]
        assert passed_input.output_dir is not None
        assert passed_input.input_filename is not None
        assert passed_input.output_filename is not None


class TestEpfrSharePayoutExporterWorkflowRun:
    """Tests for EpfrSharePayoutExporterWorkflow.run entrypoint."""

    @pytest.mark.anyio
    async def test_returns_dict_with_expected_keys(self):
        """run() returns a dict with all EpfrSharePayoutExportOutput fields."""
        fake_stats = {
            "output_path": "/tmp/out.json",
            "matched_payouts": 5,
            "unmatched_payouts": 3,
            "total_companies_exported": 2,
        }
        with patch("workflows.epfr.share_payout_exporter_workflow.export_share_payouts", return_value=fake_stats):
            wf = EpfrSharePayoutExporterWorkflow()
            result = await wf.run(EpfrSharePayoutExportInput())

        assert isinstance(result, dict)
        assert "output_path" in result
        assert "total_companies" in result
        assert "total_payouts" in result
        assert "matched_payouts" in result
        assert "unmatched_payouts" in result
        assert "stats" in result

    @pytest.mark.anyio
    async def test_total_payouts_is_sum(self):
        """total_payouts equals matched_payouts + unmatched_payouts."""
        fake_stats = {
            "output_path": "/tmp/out.json",
            "matched_payouts": 7,
            "unmatched_payouts": 4,
            "total_companies_exported": 3,
        }
        with patch("workflows.epfr.share_payout_exporter_workflow.export_share_payouts", return_value=fake_stats):
            wf = EpfrSharePayoutExporterWorkflow()
            result = await wf.run(EpfrSharePayoutExportInput())

        assert result["total_payouts"] == 11
        assert result["matched_payouts"] == 7
        assert result["unmatched_payouts"] == 4

    @pytest.mark.anyio
    async def test_handles_empty_stats(self):
        """run() handles missing keys gracefully via .get() defaults."""
        fake_stats = {}
        with patch("workflows.epfr.share_payout_exporter_workflow.export_share_payouts", return_value=fake_stats):
            wf = EpfrSharePayoutExporterWorkflow()
            result = await wf.run(EpfrSharePayoutExportInput())

        assert result["total_payouts"] == 0
        assert result["total_companies"] == 0
        assert result["output_path"] == ""

    @pytest.mark.anyio
    async def test_stats_dict_attached(self):
        """The raw stats dict is preserved in the output."""
        fake_stats = {
            "output_path": "/tmp/out.json",
            "matched_payouts": 2,
            "unmatched_payouts": 1,
            "total_companies_exported": 1,
            "extra_key": "extra_value",
        }
        with patch("workflows.epfr.share_payout_exporter_workflow.export_share_payouts", return_value=fake_stats):
            wf = EpfrSharePayoutExporterWorkflow()
            result = await wf.run(EpfrSharePayoutExportInput())

        assert result["stats"] == fake_stats
        assert result["stats"]["extra_key"] == "extra_value"

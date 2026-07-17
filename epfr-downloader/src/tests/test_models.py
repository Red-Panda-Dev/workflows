"""Tests for EPFR Pydantic models and JSON fixture contracts."""

# ruff: noqa: D102

from datetime import date
from decimal import Decimal

from pydantic import ValidationError
import pytest

from workflows.epfr.models import (
    CompanyFiles,
    EpfrAiDistilledCompany,
    EpfrAiDistilledFile,
    EpfrAiDistillerInput,
    EpfrApiResponse,
    EpfrDividendEntry,
    EpfrFileRecord,
    EpfrRecord,
    EpfrSharePayoutExportInput,
    EpfrSharePayoutExportOutput,
    EpfrSharePayoutExportRow,
    EpfrWorkflowInput,
    EpfrWorkflowOutput,
)


SAMPLE_API_RESPONSE = {
    "content": [
        {
            "id": 141278,
            "name": "Выплата дивидендов за I кв. 2026г.",
            "storageType": "FILE_SYSTEM",
            "realUploadDate": "2026-05-04 00:00",
            "uploadDate": "2026-05-04 08:54",
            "user": {
                "id": 13258,
                "surname": "КОЖЕМЯКИНА",
                "name": "НАТАЛЬЯ",
                "patronymic": "ПЕТРОВНА",
                "login": "enef@enef.by",
                "email": "enef@enef.by",
                "certificateId": None,
                "roles": [],
                "state": True,
                "phoneNumber": None,
            },
            "holder": {
                "id": 3044,
                "title": 'Открытое акционерное общество "ЭНЭФ"',
                "unp": "600073968",
            },
            "subCategoryType": "ANY",
        },
        {
            "id": 141054,
            "name": "Победа ОАО 200100116 Информация о выплате дивидендов за 2025г.",
            "storageType": "FILE_SYSTEM",
            "realUploadDate": "2026-04-30 00:00",
            "uploadDate": "2026-04-30 07:10",
            "user": {
                "id": 1260,
                "surname": "Якубовский",
                "name": "Сергей",
                "patronymic": "Александрович",
                "login": "avest-200062076-3170366c030pb7",
                "email": "-",
                "certificateId": None,
                "roles": [],
                "state": True,
                "phoneNumber": None,
            },
            "holder": {
                "id": 9899,
                "title": 'Открытое акционерное общество "Победа"',
                "unp": "200100116",
            },
            "subCategoryType": "ANY",
        },
    ],
    "pageable": {
        "sort": {"empty": False, "sorted": True, "unsorted": False},
        "offset": 0,
        "pageNumber": 0,
        "pageSize": 14,
        "paged": True,
        "unpaged": False,
    },
    "last": False,
    "totalPages": 50,
    "totalElements": 692,
    "size": 14,
    "number": 0,
    "sort": {"empty": False, "sorted": True, "unsorted": False},
    "first": True,
    "numberOfElements": 2,
    "empty": False,
}


class TestEpfrApiResponse:
    """Tests for parsing the full API response."""

    def test_parse_sample_response(self):
        response = EpfrApiResponse.model_validate(SAMPLE_API_RESPONSE)
        assert len(response.content) == 2
        assert response.total_pages == 50
        assert response.last is False

    def test_first_record_fields(self):
        response = EpfrApiResponse.model_validate(SAMPLE_API_RESPONSE)
        rec = response.content[0]

        assert rec.id == 141278
        assert rec.name == "Выплата дивидендов за I кв. 2026г."
        assert rec.real_upload_date == "2026-05-04 00:00"

    def test_first_record_holder(self):
        response = EpfrApiResponse.model_validate(SAMPLE_API_RESPONSE)
        holder = response.content[0].holder

        assert holder is not None
        assert holder.id == 3044
        assert holder.unp == "600073968"
        assert "ЭНЭФ" in holder.title

    def test_second_record_different_holder(self):
        response = EpfrApiResponse.model_validate(SAMPLE_API_RESPONSE)
        rec = response.content[1]

        assert rec.id == 141054
        assert rec.holder is not None
        assert rec.holder.unp == "200100116"
        assert "Победа" in rec.holder.title

    def test_empty_response(self):
        data = {
            "content": [],
            "last": True,
            "totalPages": 0,
        }
        response = EpfrApiResponse.model_validate(data)
        assert response.content == []
        assert response.last is True
        assert response.total_pages == 0

    def test_null_sub_category_type_is_ignored(self, load_epfr_fixture_json):
        """Regression: API returns subCategoryType=null — must not raise ValidationError."""
        data = load_epfr_fixture_json("epfr_api_response_null_subcategory.json")

        response = EpfrApiResponse.model_validate(data)

        assert len(response.content) == 14
        assert response.last is False
        assert response.total_pages == 257

        # last record has subCategoryType: null in the raw payload
        last_rec = response.content[13]
        assert last_rec.id == 136558
        assert last_rec.holder is not None
        assert last_rec.holder.unp == "200166738"
        assert "subCategoryType" not in last_rec.model_dump()


class TestEpfrFixtureContracts:
    """Tests for committed JSON fixtures using production model contracts."""

    def test_unp_file_mapping_fixture_parses_through_company_files(self, load_epfr_fixture_json):
        data = load_epfr_fixture_json("unp_file_mapping.json")

        companies = {unp: CompanyFiles.model_validate(company) for unp, company in data.items()}

        assert set(companies) == set(data)
        assert len(companies) == 20
        assert companies["600073968"].title == 'Открытое акционерное общество "ЭНЭФ"'
        assert companies["600073968"].files[0].converted_from == "141278.pdf"
        assert companies["200100116"].files[0].extracted_from == "141054.zip"
        assert companies["200100116"].files[0].converted_from == "141054.xlsx"
        assert companies["100104781"].files[0].original_name == (
            'Информация о выплате дивидендов за 2025 год ОАО "Дрожжевой комбинат"'
        )
        assert companies["700161222"].title == 'Открытое акционерное общество "Агросервис", г.Чаусы'

    def test_ai_distilled_dividends_fixture_parses_through_models(self, load_epfr_fixture_json):
        data = load_epfr_fixture_json("ai_distilled_dividends.json")

        companies = {unp: EpfrAiDistilledCompany.model_validate(company) for unp, company in data.items()}

        assert set(companies) == set(data)
        assert len(companies) == 20
        first_company = companies["600073968"]
        first_file = first_company.files[0]
        assert first_company.company_name == 'Открытое акционерное общество "ЭНЭФ"'
        assert first_file.file_path == "output/600073968/141278.md"
        assert first_file.has_dividends is True
        assert first_file.autofilled_fields == ["payment_date", "record_date"]
        assert first_file.dividends[0].amount_per_share == Decimal("46.73")
        assert first_file.dividends[0].decision_date == date(2026, 5, 4)
        assert "Дрожжевой комбинат" in companies["100104781"].company_name
        assert companies["100104781"].files[0].original_name.startswith("Информация о выплате дивидендов")
        assert companies["700161222"].files[0].original_name == (
            "Агросервис, г. Чаусы ОАО 700161222 Информация о выплате дивидендов за 2025г."
        )

    def test_share_payouts_by_unp_fixture_parses_through_row_model(self, load_epfr_fixture_json):
        data = load_epfr_fixture_json("share_payouts_by_unp.json")

        rows_by_unp = {
            unp: sorted((EpfrSharePayoutExportRow.model_validate(row) for row in rows), key=lambda row: row.share_uuid)
            for unp, rows in data.items()
        }

        assert set(rows_by_unp) == set(data)
        assert len(rows_by_unp) == 11
        row = rows_by_unp["600073968"][0]
        assert row.share_uuid == "76e720e1"
        assert row.amount_per_share == Decimal("46.73")
        assert row.payment_date == date(2026, 7, 10)
        assert row.model_dump(mode="json") == {
            "share_uuid": "76e720e1",
            "period_year": 2026,
            "period_type": "quarterly",
            "period_number": 1,
            "amount_per_share": "46.73",
            "decision_date": "2026-05-04",
            "record_date": "2026-04-04",
            "payment_date": "2026-07-10",
        }
        assert rows_by_unp["100152790"][0].amount_per_share == Decimal("20.48")
        assert len(rows_by_unp["700160772"]) == 2
        assert rows_by_unp["700160772"][0].share_uuid == rows_by_unp["700160772"][1].share_uuid

    def test_fixture_unicode_survives_loading(self, load_epfr_fixture_json):
        mapping = load_epfr_fixture_json("unp_file_mapping.json")
        distilled = load_epfr_fixture_json("ai_distilled_dividends.json")
        api_response = load_epfr_fixture_json("epfr_api_response_null_subcategory.json")

        assert "ЭНЭФ" in mapping["600073968"]["title"]
        assert "Дрожжевой комбинат" in mapping["100104781"]["files"][0]["original_name"]
        assert "Первомайск-агро" in distilled["591867699"]["company_name"]
        assert "БЕЛЭНЕРГОРЕМНАЛАДКА" in api_response["content"][2]["holder"]["title"]


class TestEpfrOptionalFieldContracts:
    """Tests for minimal payloads and missing optional fields."""

    def test_minimal_epfr_record_defaults_optional_fields(self):
        record = EpfrRecord.model_validate({"id": 1, "name": "Минимальный документ"})

        assert record.real_upload_date == ""
        assert record.holder is None

    def test_minimal_company_files_defaults_empty_file_list(self):
        company = CompanyFiles.model_validate({"title": "Компания", "holder_id": 42})

        assert company.files == []

    def test_minimal_ai_distilled_file_defaults_optional_fields(self):
        file_data = EpfrAiDistilledFile.model_validate(
            {
                "id": 1,
                "file_path": "output/1/1.md",
                "filename": "1.md",
                "original_name": "Документ",
            }
        )

        assert file_data.extracted_from is None
        assert file_data.converted_from is None
        assert file_data.error is None
        assert file_data.autofilled_fields == []
        assert file_data.dividends == []


class TestEpfrWorkflowInput:
    """Tests for workflow input defaults and validation."""

    def test_defaults(self):
        inp = EpfrWorkflowInput()
        assert inp.max_pages is None
        assert inp.date_from is None
        assert inp.timeout is None
        assert inp.output_dir is None

    def test_custom_values(self):
        inp = EpfrWorkflowInput(max_pages=5, date_from="2026-01-01", timeout=30)
        assert inp.max_pages == 5
        assert inp.date_from == "2026-01-01"
        assert inp.timeout == 30

    def test_max_pages_must_be_positive(self):
        for value in (0, -1):
            with pytest.raises(ValidationError):
                EpfrWorkflowInput(max_pages=value)

    def test_timeout_must_be_positive(self):
        for value in (0, -1):
            with pytest.raises(ValidationError):
                EpfrWorkflowInput(timeout=value)


class TestEpfrAiDistillerInput:
    """Tests for AI distiller input validation."""

    def test_temperature_must_be_non_negative(self):
        with pytest.raises(ValidationError):
            EpfrAiDistillerInput(temperature=-0.1)

    def test_max_retries_must_be_non_negative(self):
        with pytest.raises(ValidationError):
            EpfrAiDistillerInput(max_retries=-1)


class TestEpfrWorkflowOutput:
    """Tests for workflow output model."""

    def test_defaults(self):
        out = EpfrWorkflowOutput()
        assert out.total_records == 0
        assert out.total_files_downloaded == 0
        assert out.total_companies == 0
        assert out.mapping_path == ""

    def test_with_values(self):
        out = EpfrWorkflowOutput(
            total_records=100,
            total_files_downloaded=95,
            total_companies=10,
            mapping_path="/tmp/output/unp_file_mapping.json",
            stats={"download_failed": 5},
        )
        assert out.total_records == 100
        assert out.stats["download_failed"] == 5


class TestEpfrFileRecord:
    """Tests for the simplified file record model."""

    def test_create(self):
        rec = EpfrFileRecord(
            id=141278,
            filename="141278.pdf",
            original_name="Выплата дивидендов",
            upload_date="2026-05-04 00:00",
        )
        assert rec.id == 141278
        assert rec.filename == "141278.pdf"
        assert rec.original_name == "Выплата дивидендов"

    def test_serialization(self):
        rec = EpfrFileRecord(
            id=141278,
            filename="141278.pdf",
            original_name="Test",
        )
        data = rec.model_dump()
        assert data["id"] == 141278
        assert data["filename"] == "141278.pdf"


class TestEpfrDividendEntry:
    """Tests for dividend entry schema validation."""

    def test_accepts_common_share_type(self):
        entry = EpfrDividendEntry(
            share_type="common",
            period_year=2025,
            period_type="annual",
            period_number=1,
            amount_per_share=Decimal("0.1"),
            decision_date=date(2025, 5, 1),
            record_date=date(2025, 4, 1),
            payment_date=date(2025, 6, 1),
        )
        assert entry.share_type == "common"

    def test_rejects_unspecified_share_type(self):
        with pytest.raises(Exception):  # noqa: B017
            EpfrDividendEntry.model_validate(
                {
                    "share_type": "unspecified",
                    "period_year": 2025,
                    "period_type": "annual",
                    "period_number": 1,
                    "amount_per_share": "0.1",
                    "decision_date": "2025-05-01",
                    "record_date": "2025-04-01",
                    "payment_date": "2025-06-01",
                }
            )

    def test_rounds_amount_per_share_to_8_decimal_places(self):
        entry = EpfrDividendEntry.model_validate(
            {
                "share_type": "common",
                "period_year": 2025,
                "period_type": "annual",
                "period_number": 1,
                "amount_per_share": "0.0679270871",
                "decision_date": "2025-05-01",
                "record_date": "2025-04-01",
                "payment_date": "2025-06-01",
            }
        )

        assert entry.amount_per_share == Decimal("0.06792709")


class TestEpfrSharePayoutExportRow:
    """Tests for the DB-ready export row model."""

    def _make_row(self, **overrides):
        defaults = {
            "share_uuid": "abc-123",
            "period_year": 2026,
            "period_type": "quarterly",
            "period_number": 1,
            "amount_per_share": Decimal("46.73"),
            "decision_date": date(2026, 5, 4),
            "record_date": date(2026, 5, 3),
            "payment_date": date(2026, 5, 10),
        }
        defaults.update(overrides)
        return EpfrSharePayoutExportRow.model_validate(defaults)

    def test_valid_export_row_creation(self):
        row = self._make_row()
        assert row.share_uuid == "abc-123"
        assert row.period_year == 2026
        assert row.period_type == "quarterly"
        assert row.period_number == 1
        assert row.amount_per_share == Decimal("46.73")
        assert row.decision_date == date(2026, 5, 4)
        assert row.record_date == date(2026, 5, 3)
        assert row.payment_date == date(2026, 5, 10)

    def test_serialization_json_keys(self):
        row = self._make_row()
        data = row.model_dump(mode="json")
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
        assert set(data.keys()) == expected_keys
        assert "unp" not in data

    def test_amount_per_share_serializes_as_string(self):
        row = self._make_row(amount_per_share=Decimal("46.73"))
        data = row.model_dump(mode="json")
        assert data["amount_per_share"] == "46.73"
        assert isinstance(data["amount_per_share"], str)

    def test_zero_amount_preserved(self):
        row = self._make_row(amount_per_share=Decimal("0"))
        data = row.model_dump(mode="json")
        assert data["amount_per_share"] == "0"

    def test_high_precision_amount_is_rounded_and_serialized_without_scientific_notation(self):
        row = self._make_row(amount_per_share=Decimal("0.0679270871"))

        assert row.amount_per_share == Decimal("0.06792709")
        assert row.model_dump(mode="json")["amount_per_share"] == "0.06792709"

    def test_dates_serialize_as_iso_strings(self):
        row = self._make_row(
            decision_date=date(2026, 5, 4),
            record_date=date(2026, 5, 3),
            payment_date=date(2026, 5, 10),
        )
        data = row.model_dump(mode="json")
        assert data["decision_date"] == "2026-05-04"
        assert data["record_date"] == "2026-05-03"
        assert data["payment_date"] == "2026-05-10"

    def test_invalid_period_type_rejected(self):
        with pytest.raises(ValidationError):
            self._make_row(period_type="monthly")

    def test_invalid_period_number_rejected(self):
        with pytest.raises(ValidationError):
            self._make_row(period_type="halfyear", period_number=3)

    def test_invalid_date_order_rejected(self):
        with pytest.raises(ValidationError):
            self._make_row(
                decision_date=date(2026, 5, 1),
                record_date=date(2026, 5, 10),
                payment_date=date(2026, 5, 15),
            )


def test_dividend_and_export_rows_preserve_unknown_dates_as_null():
    """Unknown source dates must remain nullable through the export contract."""
    dividend = EpfrDividendEntry(
        share_type="common",
        period_year=2025,
        period_type="annual",
        period_number=1,
        amount_per_share=Decimal("0.5"),
    )
    row = EpfrSharePayoutExportRow(
        share_uuid="share-1",
        period_year=dividend.period_year,
        period_type=dividend.period_type,
        period_number=dividend.period_number,
        amount_per_share=dividend.amount_per_share,
        decision_date=dividend.decision_date,
        record_date=dividend.record_date,
        payment_date=dividend.payment_date,
    )

    assert row.model_dump(mode="json")["decision_date"] is None
    assert row.model_dump(mode="json")["record_date"] is None
    assert row.model_dump(mode="json")["payment_date"] is None


class TestEpfrSharePayoutExportInput:
    """Tests for the share payout export workflow input model."""

    def test_defaults(self):
        inp = EpfrSharePayoutExportInput()
        assert inp.output_dir is None
        assert inp.input_filename is None
        assert inp.output_filename is None
        assert inp.shares_csv_path is None

    def test_custom_values(self):
        inp = EpfrSharePayoutExportInput(
            output_dir="/data",
            input_filename="custom_in.json",
            output_filename="custom_out.json",
            shares_csv_path="/path/to/shares.csv",
        )
        assert inp.output_dir == "/data"
        assert inp.input_filename == "custom_in.json"
        assert inp.output_filename == "custom_out.json"
        assert inp.shares_csv_path == "/path/to/shares.csv"


class TestEpfrSharePayoutExportOutput:
    """Tests for the share payout export workflow output model."""

    def test_defaults(self):
        out = EpfrSharePayoutExportOutput()
        assert out.output_path == ""
        assert out.total_companies == 0
        assert out.total_payouts == 0
        assert out.matched_payouts == 0
        assert out.unmatched_payouts == 0
        assert out.stats == {}

    def test_with_values(self):
        out = EpfrSharePayoutExportOutput(
            output_path="/tmp/share_payouts_by_unp.json",
            total_companies=5,
            total_payouts=20,
            matched_payouts=18,
            unmatched_payouts=2,
            stats={"by_period": {"annual": 10, "quarterly": 10}},
        )
        assert out.output_path == "/tmp/share_payouts_by_unp.json"
        assert out.total_companies == 5
        assert out.total_payouts == 20
        assert out.matched_payouts == 18
        assert out.unmatched_payouts == 2
        assert out.stats["by_period"]["annual"] == 10

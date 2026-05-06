"""Tests for EPFR Pydantic models using sample API response."""

# ruff: noqa: D102

import pytest

from ..models import (
    EpfrApiResponse,
    EpfrFileRecord,
    EpfrWorkflowInput,
    EpfrWorkflowOutput,
)

SAMPLE_API_RESPONSE = {
    "content": [
        {
            "id": 141278,
            "name": "Выплата дивидендов за I кв. 2026г.",
            "description": "Выплата дивидендов за 1 квартал 2026 года",
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
                "organization": {
                    "id": 3044,
                    "title": 'Открытое акционерное общество "ЭНЭФ"',
                    "unp": "600073968",
                    "shortName": "",
                    "address": "",
                    "labels": [{"id": 1, "name": "Эмитент"}],
                    "state": None,
                },
                "certificateId": None,
                "roles": [],
                "state": True,
                "phoneNumber": None,
            },
            "organization": {
                "id": 3044,
                "title": 'Открытое акционерное общество "ЭНЭФ"',
                "unp": "600073968",
                "shortName": "",
            },
            "holder": {
                "id": 3044,
                "title": 'Открытое акционерное общество "ЭНЭФ"',
                "unp": "600073968",
                "shortName": "",
            },
            "subCategoryType": "ANY",
        },
        {
            "id": 141054,
            "name": "Победа ОАО 200100116 Информация о выплате дивидендов за 2025г.",
            "description": "Документ содержит информацию о выплате дивидендов за 2025г.",
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
                "organization": {
                    "id": 1080,
                    "title": 'Закрытое акционерное общество "Траст-Запад"',
                    "unp": "200062076",
                    "shortName": "",
                    "address": "",
                    "labels": [
                        {"id": 1, "name": "Эмитент"},
                        {"id": 2, "name": "Профучастник"},
                    ],
                    "state": None,
                },
                "certificateId": None,
                "roles": [],
                "state": True,
                "phoneNumber": None,
            },
            "organization": {
                "id": 1080,
                "title": 'Закрытое акционерное общество "Траст-Запад"',
                "unp": "200062076",
                "shortName": "",
            },
            "holder": {
                "id": 9899,
                "title": 'Открытое акционерное общество "Победа"',
                "unp": "200100116",
                "shortName": 'ОАО "Победа"',
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
        assert response.total_elements == 692
        assert response.last is False
        assert response.first is True
        assert response.empty is False
        assert response.number == 0
        assert response.size == 14
        assert response.number_of_elements == 2

    def test_first_record_fields(self):
        response = EpfrApiResponse.model_validate(SAMPLE_API_RESPONSE)
        rec = response.content[0]

        assert rec.id == 141278
        assert rec.name == "Выплата дивидендов за I кв. 2026г."
        assert rec.description == "Выплата дивидендов за 1 квартал 2026 года"
        assert rec.storage_type == "FILE_SYSTEM"
        assert rec.real_upload_date == "2026-05-04 00:00"
        assert rec.upload_date == "2026-05-04 08:54"
        assert rec.sub_category_type == "ANY"

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
        assert rec.holder.short_name == 'ОАО "Победа"'

    def test_second_record_user_org(self):
        response = EpfrApiResponse.model_validate(SAMPLE_API_RESPONSE)
        user = response.content[1].user

        assert user is not None
        assert user.surname == "Якубовский"
        assert user.organization is not None
        assert user.organization.unp == "200062076"

    def test_pageable(self):
        response = EpfrApiResponse.model_validate(SAMPLE_API_RESPONSE)

        assert response.pageable.page_number == 0
        assert response.pageable.page_size == 14
        assert response.pageable.offset == 0
        assert response.pageable.paged is True
        assert response.pageable.sort.sorted is True

    def test_empty_response(self):
        data = {
            "content": [],
            "pageable": {
                "sort": {"empty": True, "sorted": False, "unsorted": True},
                "offset": 0,
                "pageNumber": 0,
                "pageSize": 14,
                "paged": True,
                "unpaged": False,
            },
            "last": True,
            "totalPages": 0,
            "totalElements": 0,
            "size": 14,
            "number": 0,
            "sort": {"empty": True, "sorted": False, "unsorted": True},
            "first": True,
            "numberOfElements": 0,
            "empty": True,
        }
        response = EpfrApiResponse.model_validate(data)
        assert response.content == []
        assert response.last is True
        assert response.empty is True
        assert response.total_elements == 0


class TestEpfrWorkflowInput:
    """Tests for workflow input defaults and validation."""

    def test_defaults(self):
        inp = EpfrWorkflowInput()
        assert inp.max_pages == 10
        assert inp.date_from == "2026-03-01"
        assert inp.timeout == 60
        assert inp.output_dir == "output"

    def test_custom_values(self):
        inp = EpfrWorkflowInput(max_pages=5, date_from="2026-01-01", timeout=30)
        assert inp.max_pages == 5
        assert inp.date_from == "2026-01-01"
        assert inp.timeout == 30

    def test_max_pages_validation(self):
        with pytest.raises(Exception):  # noqa: B017
            EpfrWorkflowInput(max_pages=0)
        with pytest.raises(Exception):  # noqa: B017
            EpfrWorkflowInput(max_pages=101)


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

"""Tests for EPFR AI distiller normalization and workflow helpers."""

import asyncio
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from workflows.epfr import ai_distiller
from workflows.epfr.ai_distiller import (
    AIDistiller,
    _RawDividendEntry,
    _RawExtraction,
    _load_prompt_template,
    normalize_and_fill_dividend,
)
from workflows.epfr.models import EpfrAiDistillerInput, EpfrDividendEntry


def test_normalize_and_fill_dividend_autofills_dates_and_period_fields():
    raw = _RawDividendEntry(amount_per_share="0.1234")

    normalized, autofilled = normalize_and_fill_dividend(raw, upload_date="2026-05-04")

    assert isinstance(normalized, EpfrDividendEntry)
    assert normalized.decision_date == date(2026, 4, 4)
    assert normalized.record_date == date(2026, 3, 4)
    assert normalized.payment_date == date(2026, 6, 4)
    assert normalized.share_type == "common"
    assert normalized.period_type == "annual"
    assert normalized.period_number == 1
    assert normalized.period_year == 2025
    assert "decision_date" in autofilled
    assert "record_date" in autofilled
    assert "payment_date" in autofilled
    assert "share_type" in autofilled


def test_normalize_and_fill_dividend_keeps_provided_dates():
    raw = _RawDividendEntry(
        share_type="preferred",
        period_year=2025,
        period_type="quarterly",
        period_number=2,
        amount_per_share="1.001",
        decision_date="2025-04-10",
        record_date="2025-04-05",
        payment_date="2025-06-10",
    )

    normalized, autofilled = normalize_and_fill_dividend(raw, upload_date="2026-05-04")

    assert normalized.period_type == "quarterly"
    assert normalized.share_type == "preferred"
    assert normalized.period_number == 2
    assert normalized.period_year == 2025
    assert normalized.decision_date == date(2025, 4, 10)
    assert normalized.record_date == date(2025, 4, 5)
    assert normalized.payment_date == date(2025, 6, 10)
    assert autofilled == []


def test_normalize_and_fill_dividend_rounds_high_precision_amount_to_model_precision():
    raw = _RawDividendEntry(
        share_type="common",
        period_year=2025,
        period_type="quarterly",
        period_number=1,
        amount_per_share="0.0679270871",
        decision_date="2025-04-10",
        record_date="2025-04-05",
        payment_date="2025-05-10",
    )

    normalized, autofilled = normalize_and_fill_dividend(raw, upload_date="2026-05-04")

    assert normalized.amount_per_share == Decimal("0.06792709")
    assert autofilled == []


def test_normalize_and_fill_dividend_sets_payment_date_plus_one_day_for_zero_amount():
    raw = _RawDividendEntry(
        share_type="common",
        amount_per_share="0",
        decision_date="2025-03-28",
        record_date="2025-03-20",
    )

    normalized, autofilled = normalize_and_fill_dividend(raw, upload_date="2026-05-04")

    assert normalized.payment_date == date(2026, 3, 29)
    assert "payment_date" in autofilled


def test_dividend_entry_rejects_invalid_date_ordering():
    with pytest.raises(ValueError, match="payment_date must be greater than decision_date"):
        EpfrDividendEntry(
            share_type="common",
            period_year=2025,
            period_type="annual",
            period_number=1,
            amount_per_share=Decimal("0.1"),
            decision_date=date(2025, 5, 1),
            record_date=date(2025, 4, 1),
            payment_date=date(2025, 5, 1),
        )


def test_normalize_and_fill_dividend_corrects_record_date_when_farther_than_6_months():
    raw = _RawDividendEntry(
        period_year=2024,
        period_type="quarterly",
        period_number=1,
        amount_per_share="1.0",
        decision_date="2025-06-15",
        record_date="2024-01-10",
        payment_date="2025-08-15",
    )

    normalized, autofilled = normalize_and_fill_dividend(raw, upload_date="2026-05-04")

    assert normalized.record_date == date(2025, 5, 15)
    assert "record_date_corrected" in autofilled


def test_normalize_and_fill_dividend_keeps_record_date_when_within_6_months():
    raw = _RawDividendEntry(
        period_year=2024,
        period_type="quarterly",
        period_number=1,
        amount_per_share="1.0",
        decision_date="2025-06-15",
        record_date="2025-03-15",
        payment_date="2025-08-15",
    )

    normalized, autofilled = normalize_and_fill_dividend(raw, upload_date="2026-05-04")

    assert normalized.record_date == date(2025, 3, 15)
    assert "record_date_corrected" not in autofilled


def test_normalize_and_fill_dividend_corrects_payment_date_when_farther_than_year():
    raw = _RawDividendEntry(
        period_year=2024,
        period_type="quarterly",
        period_number=1,
        amount_per_share="1.0",
        decision_date="2025-03-01",
        record_date="2025-02-01",
        payment_date="2027-01-01",
    )

    normalized, autofilled = normalize_and_fill_dividend(raw, upload_date="2026-05-04")

    assert normalized.payment_date == date(2025, 5, 1)
    assert "payment_date_corrected" in autofilled


def test_normalize_and_fill_dividend_keeps_payment_date_when_within_year():
    raw = _RawDividendEntry(
        period_year=2024,
        period_type="quarterly",
        period_number=1,
        amount_per_share="1.0",
        decision_date="2025-03-01",
        record_date="2025-02-01",
        payment_date="2025-09-01",
    )

    normalized, autofilled = normalize_and_fill_dividend(raw, upload_date="2026-05-04")

    assert normalized.payment_date == date(2025, 9, 1)
    assert "payment_date_corrected" not in autofilled


def test_normalize_and_fill_dividend_annual_same_year_bumps_dates_when_not_future(monkeypatch):
    class _FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 6, 1, tzinfo=UTC)

    monkeypatch.setattr(ai_distiller, "datetime", _FixedDateTime)

    raw = _RawDividendEntry(
        period_year=2025,
        period_type="annual",
        period_number=1,
        amount_per_share="1.0",
        decision_date="2025-04-10",
        record_date="2025-04-05",
        payment_date="2025-06-10",
    )

    normalized, autofilled = normalize_and_fill_dividend(raw, upload_date="2026-05-04")

    assert normalized.period_year == 2025
    assert normalized.decision_date == date(2026, 4, 10)
    assert normalized.record_date == date(2026, 4, 5)
    assert normalized.payment_date == date(2026, 6, 10)
    assert "dates_year_corrected" in autofilled


def test_normalize_and_fill_dividend_annual_same_year_decrements_period_year_when_future(monkeypatch):
    class _FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 6, 1, tzinfo=UTC)

    monkeypatch.setattr(ai_distiller, "datetime", _FixedDateTime)

    raw = _RawDividendEntry(
        period_year=2026,
        period_type="annual",
        period_number=1,
        amount_per_share="1.0",
        decision_date="2026-04-10",
        record_date="2026-04-05",
        payment_date="2026-06-10",
    )

    normalized, autofilled = normalize_and_fill_dividend(raw, upload_date="2026-05-04")

    assert normalized.period_year == 2025
    assert normalized.decision_date == date(2026, 4, 10)
    assert "period_year_corrected" in autofilled


def test_normalize_and_fill_dividend_non_annual_skips_period_year_validation(monkeypatch):
    class _FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 6, 1, tzinfo=UTC)

    monkeypatch.setattr(ai_distiller, "datetime", _FixedDateTime)

    raw = _RawDividendEntry(
        period_year=2026,
        period_type="quarterly",
        period_number=2,
        amount_per_share="1.0",
        decision_date="2026-04-10",
        record_date="2026-04-05",
        payment_date="2026-06-10",
    )

    normalized, autofilled = normalize_and_fill_dividend(raw, upload_date="2026-05-04")

    assert normalized.period_year == 2026
    assert normalized.decision_date == date(2026, 4, 10)
    assert "period_year_corrected" not in autofilled
    assert "dates_year_corrected" not in autofilled


def test_normalize_and_fill_dividend_handles_leap_day_when_bumping_year(monkeypatch):
    class _FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2025, 6, 1, tzinfo=UTC)

    monkeypatch.setattr(ai_distiller, "datetime", _FixedDateTime)

    raw = _RawDividendEntry(
        period_year=2024,
        period_type="annual",
        period_number=1,
        amount_per_share="1.0",
        decision_date="2024-02-29",
        record_date="2024-02-28",
        payment_date="2024-03-29",
    )

    normalized, autofilled = normalize_and_fill_dividend(raw, upload_date="2026-05-04")

    assert normalized.decision_date == date(2025, 2, 28)
    assert normalized.record_date == date(2025, 2, 28)
    assert normalized.payment_date == date(2025, 3, 29)
    assert "dates_year_corrected" in autofilled


def test_normalize_and_fill_dividend_corrects_payment_date_when_same_as_decision_date():
    raw = _RawDividendEntry(
        period_year=2025,
        period_type="quarterly",
        period_number=1,
        amount_per_share="1.0",
        decision_date="2025-04-10",
        record_date="2025-04-05",
        payment_date="2025-04-10",
    )

    normalized, autofilled = normalize_and_fill_dividend(raw, upload_date="2026-05-04")

    assert normalized.payment_date == date(2025, 4, 11)
    assert "payment_date_corrected" in autofilled


def test_normalize_and_fill_dividend_corrects_payment_date_when_before_decision_date():
    raw = _RawDividendEntry(
        period_year=2025,
        period_type="quarterly",
        period_number=1,
        amount_per_share="1.0",
        decision_date="2025-04-10",
        record_date="2025-04-05",
        payment_date="2025-03-01",
    )

    normalized, autofilled = normalize_and_fill_dividend(raw, upload_date="2026-05-04")

    assert normalized.payment_date == date(2025, 4, 11)
    assert "payment_date_corrected" in autofilled


def test_normalize_and_fill_dividend_corrects_record_date_when_after_decision_date():
    raw = _RawDividendEntry(
        period_year=2025,
        period_type="quarterly",
        period_number=1,
        amount_per_share="1.0",
        decision_date="2025-04-10",
        record_date="2025-05-20",
        payment_date="2025-06-10",
    )

    normalized, autofilled = normalize_and_fill_dividend(raw, upload_date="2026-05-04")

    assert normalized.record_date == date(2025, 4, 10)
    assert "record_date_corrected" in autofilled


class _FixedNowDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 6, 1, tzinfo=UTC)


def test_load_prompt_template_reads_from_disk_and_caches(monkeypatch, tmp_path):
    prompt_dir = tmp_path / "prompts"
    prompt_dir.mkdir()
    prompt_path = prompt_dir / "dividends_parsing.md"
    prompt_path.write_text("Prompt {{REFERENCE_DATE}}", encoding="utf-8")

    monkeypatch.setattr(ai_distiller, "__file__", str(tmp_path / "ai_distiller.py"))
    monkeypatch.setattr(ai_distiller, "_PROMPT_TEMPLATE", None)

    first = _load_prompt_template()
    prompt_path.write_text("Changed prompt", encoding="utf-8")
    second = _load_prompt_template()

    assert first == "Prompt {{REFERENCE_DATE}}"
    assert second == "Prompt {{REFERENCE_DATE}}"


def test_load_prompt_template_raises_clear_error_when_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(ai_distiller, "__file__", str(tmp_path / "ai_distiller.py"))
    monkeypatch.setattr(ai_distiller, "_PROMPT_TEMPLATE", None)

    with pytest.raises(FileNotFoundError, match="Prompt template not found"):
        _load_prompt_template()


def test_compute_retry_wait_seconds_uses_expected_caps_and_jitter(monkeypatch):
    monkeypatch.setattr(
        ai_distiller,
        "load_epfr_config",
        lambda: SimpleNamespace(
            ai_retry_backoff_base=2,
            ai_retry_backoff_max=10,
            ai_retry_backoff_max_429=30,
            ai_retry_jitter_ratio=0.5,
        ),
    )
    monkeypatch.setattr(ai_distiller.random, "uniform", lambda low, high: high)

    assert ai_distiller._compute_retry_wait_seconds(0, is_rate_limited=False) == 3.0
    assert ai_distiller._compute_retry_wait_seconds(4, is_rate_limited=True) == 45.0


def test_extract_with_retry_retries_once_then_succeeds(monkeypatch, tmp_path):
    distiller = AIDistiller.__new__(AIDistiller)
    attempts: list[str] = []
    waits: list[float] = []
    expected = _RawExtraction(has_dividends=True, ai_comment="ok")

    async def fake_extract(markdown_text: str) -> _RawExtraction:
        attempts.append(markdown_text)
        if len(attempts) == 1:
            raise RuntimeError("503 temporary overload")
        return expected

    async def fake_sleep(wait: float) -> None:
        waits.append(wait)

    distiller.extract = fake_extract
    monkeypatch.setattr(ai_distiller, "_compute_retry_wait_seconds", lambda attempt, *, is_rate_limited: 1.25)
    monkeypatch.setattr(ai_distiller.asyncio, "sleep", fake_sleep)

    result = asyncio.run(distiller.extract_with_retry("markdown body", max_retries=3, file_path=tmp_path / "140911.md"))

    assert result == expected
    assert attempts == ["markdown body", "markdown body"]
    assert waits == [1.25]


def test_extract_with_retry_raises_runtime_error_after_exhaustion(monkeypatch, tmp_path):
    distiller = AIDistiller.__new__(AIDistiller)
    attempts: list[str] = []
    waits: list[float] = []

    async def fake_extract(markdown_text: str) -> _RawExtraction:
        attempts.append(markdown_text)
        raise TimeoutError("timeout while parsing")

    async def fake_sleep(wait: float) -> None:
        waits.append(wait)

    distiller.extract = fake_extract
    monkeypatch.setattr(ai_distiller, "_compute_retry_wait_seconds", lambda attempt, *, is_rate_limited: 0.75)
    monkeypatch.setattr(ai_distiller.asyncio, "sleep", fake_sleep)

    with pytest.raises(RuntimeError, match="AI extraction failed"):
        asyncio.run(distiller.extract_with_retry("markdown body", max_retries=3, file_path=tmp_path / "140297.md"))

    assert attempts == ["markdown body", "markdown body", "markdown body"]
    assert waits == [0.75, 0.75]


def test_run_ai_distillation_uses_fixtures_without_network(
    monkeypatch,
    tmp_path,
    load_epfr_fixture_json,
    load_epfr_fixture_text,
):
    mapping_fixture = load_epfr_fixture_json("unp_file_mapping.json")
    distilled_fixture = load_epfr_fixture_json("ai_distilled_dividends.json")
    selected_unps = ["100104781", "999140297"]
    staged_mapping = {
        "100104781": mapping_fixture["100104781"],
        "999140297": {
            "title": "Открытое акционерное общество «Сороги-Агро»",
            "holder_id": 140297,
            "files": [
                {
                    "id": 140297,
                    "filename": "140297.md",
                    "original_name": "О выплате дивидендов по акциям за 2025 год",
                    "upload_date": "2026-04-25",
                    "extracted_from": None,
                    "converted_from": "140297.pdf",
                }
            ],
        },
    }

    (tmp_path / "100104781").mkdir()
    (tmp_path / "999140297").mkdir()
    (tmp_path / "100104781" / "140911.md").write_text(load_epfr_fixture_text("140911.md"), encoding="utf-8")
    (tmp_path / "999140297" / "140297.md").write_text(load_epfr_fixture_text("140297.md"), encoding="utf-8")
    (tmp_path / "unp_file_mapping.json").write_text(
        json.dumps(staged_mapping, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    fixture_raw = {
        "140911.md": _RawExtraction(
            has_dividends=True,
            ai_comment="fixture 140911",
            dividends=[
                ai_distiller._RawDividendEntry(
                    share_type="common",
                    period_year=2025,
                    period_type="annual",
                    period_number=1,
                    amount_per_share="0.007337",
                    decision_date="2025-03-31",
                    payment_date="2027-04-22",
                )
            ],
        ),
        "140297.md": _RawExtraction(
            has_dividends=True,
            ai_comment="fixture 140297",
            dividends=[
                ai_distiller._RawDividendEntry(
                    share_type="common",
                    period_year=2025,
                    period_type="annual",
                    period_number=1,
                    amount_per_share="0.0124",
                    decision_date="2025-03-30",
                    payment_date="2025-04-22",
                )
            ],
        ),
    }

    class FakeDistiller:
        def __init__(self, model_name: str, temperature: float, reference_date: str) -> None:
            self.model_name = model_name
            self.temperature = temperature
            self.reference_date = reference_date

        async def extract_with_retry(self, markdown_text: str, max_retries: int, file_path: Path) -> _RawExtraction:
            assert max_retries == 2
            assert markdown_text.strip()
            return fixture_raw[file_path.name]

    monkeypatch.setattr(ai_distiller, "AIDistiller", FakeDistiller)
    monkeypatch.setattr(ai_distiller, "datetime", _FixedNowDateTime)

    stats = asyncio.run(
        ai_distiller.run_ai_distillation(
            EpfrAiDistillerInput(
                output_dir=str(tmp_path),
                mapping_filename="unp_file_mapping.json",
                output_filename="ai_distilled_dividends.json",
                model_name="fake-model",
                temperature=0.0,
                max_retries=2,
                file_delay_seconds=0.0,
                unps=selected_unps,
            )
        )
    )

    output = json.loads((tmp_path / "ai_distilled_dividends.json").read_text(encoding="utf-8"))
    expected_semantics = {
        "100104781": {
            "company_name": distilled_fixture["100104781"]["company_name"],
            "file_count": 1,
            "has_dividends": distilled_fixture["100104781"]["files"][0]["has_dividends"],
            "dividends": [
                {
                    "amount_per_share": entry["amount_per_share"],
                    "decision_date": entry["decision_date"],
                    "record_date": entry["record_date"],
                    "payment_date": entry["payment_date"],
                }
                for entry in distilled_fixture["100104781"]["files"][0]["dividends"]
            ],
        },
        "999140297": {
            "company_name": "Открытое акционерное общество «Сороги-Агро»",
            "file_count": 1,
            "has_dividends": True,
            "dividends": [
                {
                    "amount_per_share": "0.0124",
                    "decision_date": "2026-03-30",
                    "record_date": "2026-02-28",
                    "payment_date": "2026-04-22",
                }
            ],
        },
    }

    actual_semantics = {
        unp: {
            "company_name": company["company_name"],
            "file_count": len(company["files"]),
            "has_dividends": company["files"][0]["has_dividends"],
            "dividends": [
                {
                    "amount_per_share": entry["amount_per_share"],
                    "decision_date": entry["decision_date"],
                    "record_date": entry["record_date"],
                    "payment_date": entry["payment_date"],
                }
                for entry in company["files"][0]["dividends"]
            ],
        }
        for unp, company in output.items()
    }

    assert set(output) == set(selected_unps)
    assert stats["total_companies"] == 2
    assert stats["total_files"] == 2
    assert stats["successful"] == 2
    assert stats["failed"] == 0
    assert actual_semantics == expected_semantics

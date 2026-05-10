"""Tests for EPFR AI distiller normalization and workflow helpers."""

import asyncio
import json
import os
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

os.environ.pop("AGENT", None)

import pytest

from workflows.epfr import ai_distiller, ai_distiller_workflow
from workflows.epfr.ai_distiller import (
    AIDistiller,
    _RawDividendEntry,
    _RawExtraction,
    _load_prompt_template,
    _parse_iso_date,
    _safe_replace_year,
    _shift_months,
    _validate_and_correct_dates,
    normalize_and_fill_dividend,
)
from workflows.epfr.ai_distiller_workflow import (
    EpfrAiDistillerWorkflow,
)
from workflows.epfr.models import EpfrAiDistillerInput, EpfrAiDistillerOutput, EpfrDividendEntry


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


# ==================== Phase 1: Pure Unit Tests for Helpers ====================


class TestParseIsoDate:
    def test_valid_iso_date(self):
        assert _parse_iso_date("2025-03-28") == date(2025, 3, 28)

    def test_none_returns_none(self):
        assert _parse_iso_date(None) is None

    def test_empty_string_returns_none(self):
        assert _parse_iso_date("") is None

    def test_invalid_format_raises(self):
        with pytest.raises(ValueError):
            _parse_iso_date("28-03-2025")

    def test_malformed_month_raises(self):
        with pytest.raises(ValueError):
            _parse_iso_date("2025-13-01")


class TestShiftMonths:
    def test_forward_shift(self):
        assert _shift_months(date(2025, 3, 15), 2) == date(2025, 5, 15)

    def test_backward_shift(self):
        assert _shift_months(date(2025, 5, 15), -2) == date(2025, 3, 15)

    def test_cross_year_forward(self):
        assert _shift_months(date(2025, 11, 15), 3) == date(2026, 2, 15)

    def test_cross_year_backward(self):
        assert _shift_months(date(2025, 2, 15), -3) == date(2024, 11, 15)

    def test_day_clamp_to_30_day_month(self):
        assert _shift_months(date(2025, 1, 31), 1) == date(2025, 2, 28)

    def test_day_clamp_feb_leap_year(self):
        assert _shift_months(date(2024, 1, 29), 1) == date(2024, 2, 29)

    def test_day_clamp_feb_non_leap(self):
        assert _shift_months(date(2025, 1, 29), 1) == date(2025, 2, 28)

    def test_30_day_to_31_day_month(self):
        assert _shift_months(date(2025, 4, 30), 1) == date(2025, 5, 30)

    def test_zero_shift(self):
        assert _shift_months(date(2025, 6, 15), 0) == date(2025, 6, 15)

    def test_large_forward_12_months(self):
        assert _shift_months(date(2025, 6, 15), 12) == date(2026, 6, 15)


class TestSafeReplaceYear:
    def test_normal_replacement(self):
        assert _safe_replace_year(date(2025, 3, 15), 2026) == date(2026, 3, 15)

    def test_leap_day_to_non_leap_year(self):
        assert _safe_replace_year(date(2024, 2, 29), 2025) == date(2025, 2, 28)

    def test_leap_day_to_leap_year(self):
        assert _safe_replace_year(date(2024, 2, 29), 2028) == date(2028, 2, 29)

    def test_non_leap_day_replacement(self):
        assert _safe_replace_year(date(2025, 6, 30), 2026) == date(2026, 6, 30)


class TestValidateAndCorrectDates:
    def test_no_corrections_needed(self):
        af = []
        py, dd, rd, pd = _validate_and_correct_dates(
            period_type="quarterly",
            period_year=2025,
            decision_date=date(2025, 4, 10),
            record_date=date(2025, 4, 5),
            payment_date=date(2025, 6, 10),
            autofilled=af,
        )
        assert py == 2025
        assert dd == date(2025, 4, 10)
        assert rd == date(2025, 4, 5)
        assert pd == date(2025, 6, 10)
        assert af == []

    def test_both_record_and_payment_corrected(self):
        af = []
        py, dd, rd, pd = _validate_and_correct_dates(
            period_type="quarterly",
            period_year=2024,
            decision_date=date(2025, 6, 15),
            record_date=date(2024, 1, 10),
            payment_date=date(2027, 1, 1),
            autofilled=af,
        )
        assert rd == date(2025, 5, 15)
        assert pd == date(2025, 8, 15)
        assert "record_date_corrected" in af
        assert "payment_date_corrected" in af

    def test_non_annual_skips_annual_branch(self):
        af = []
        py, dd, rd, pd = _validate_and_correct_dates(
            period_type="halfyear",
            period_year=2025,
            decision_date=date(2025, 4, 10),
            record_date=date(2025, 4, 5),
            payment_date=date(2025, 6, 10),
            autofilled=af,
        )
        assert py == 2025
        assert "period_year_corrected" not in af
        assert "dates_year_corrected" not in af

    def test_equal_decision_and_record_date_ok(self):
        af = []
        _, _, rd, _ = _validate_and_correct_dates(
            period_type="quarterly",
            period_year=2025,
            decision_date=date(2025, 4, 10),
            record_date=date(2025, 4, 10),
            payment_date=date(2025, 4, 11),
            autofilled=af,
        )
        assert rd == date(2025, 4, 10)
        assert "record_date_corrected" not in af

    def test_payment_one_day_after_decision_ok(self):
        af = []
        _, _, _, pd = _validate_and_correct_dates(
            period_type="quarterly",
            period_year=2025,
            decision_date=date(2025, 4, 10),
            record_date=date(2025, 4, 5),
            payment_date=date(2025, 4, 11),
            autofilled=af,
        )
        assert pd == date(2025, 4, 11)
        assert "payment_date_corrected" not in af


# ==================== Phase 2: normalize_and_fill_dividend Edge Cases ====================


class TestNormalizeAndFillDividendEdgeCases:
    def test_invalid_share_type_defaults_to_common(self):
        raw = _RawDividendEntry(
            share_type="bond",
            period_year=2025,
            period_type="annual",
            period_number=1,
            amount_per_share="1.0",
            decision_date="2025-04-10",
            record_date="2025-04-05",
            payment_date="2025-06-10",
        )
        normalized, af = normalize_and_fill_dividend(raw, upload_date="2026-05-04")
        assert normalized.share_type == "common"
        assert "share_type" in af

    def test_none_amount_defaults_to_zero(self):
        raw = _RawDividendEntry(
            period_year=2025,
            period_type="annual",
            period_number=1,
            decision_date="2025-04-10",
            record_date="2025-04-05",
            payment_date="2025-06-10",
        )
        normalized, af = normalize_and_fill_dividend(raw, upload_date="2026-05-04")
        assert normalized.amount_per_share == Decimal("0")
        assert "amount_per_share" in af

    def test_integer_amount(self):
        raw = _RawDividendEntry(
            period_year=2025,
            period_type="annual",
            period_number=1,
            amount_per_share=5,
            decision_date="2025-04-10",
            record_date="2025-04-05",
            payment_date="2025-06-10",
        )
        normalized, _ = normalize_and_fill_dividend(raw, upload_date="2026-05-04")
        assert normalized.amount_per_share == Decimal("5")

    def test_none_period_type_defaults_to_annual(self):
        raw = _RawDividendEntry(
            period_year=2025,
            period_number=1,
            amount_per_share="1.0",
            decision_date="2025-04-10",
            record_date="2025-04-05",
            payment_date="2025-06-10",
        )
        normalized, af = normalize_and_fill_dividend(raw, upload_date="2026-05-04")
        assert normalized.period_type == "annual"
        assert "period_type" in af

    def test_none_period_number_defaults_to_1(self):
        raw = _RawDividendEntry(
            period_year=2025,
            period_type="annual",
            amount_per_share="1.0",
            decision_date="2025-04-10",
            record_date="2025-04-05",
            payment_date="2025-06-10",
        )
        normalized, af = normalize_and_fill_dividend(raw, upload_date="2026-05-04")
        assert normalized.period_number == 1
        assert "period_number" in af

    def test_none_period_year_defaults_to_decision_year(self):
        raw = _RawDividendEntry(
            period_type="annual",
            period_number=1,
            amount_per_share="1.0",
            decision_date="2025-04-10",
            record_date="2025-04-05",
            payment_date="2025-06-10",
        )
        normalized, af = normalize_and_fill_dividend(raw, upload_date="2026-05-04")
        assert normalized.period_year == 2025
        assert "period_year" in af

    def test_none_decision_date_uses_upload_date(self, monkeypatch):
        raw = _RawDividendEntry(
            period_year=2025,
            period_type="annual",
            period_number=1,
            amount_per_share="1.0",
            record_date="2025-04-05",
            payment_date="2025-06-10",
        )
        monkeypatch.setattr(ai_distiller, "datetime", _FixedNowDateTime)
        normalized, af = normalize_and_fill_dividend(raw, upload_date="2025-05-15")
        assert normalized.decision_date == date(2026, 4, 15)
        assert "decision_date" in af

    def test_none_record_date_shifts_from_decision(self):
        raw = _RawDividendEntry(
            period_year=2025,
            period_type="quarterly",
            period_number=1,
            amount_per_share="1.0",
            decision_date="2025-06-10",
            payment_date="2025-08-10",
        )
        normalized, af = normalize_and_fill_dividend(raw, upload_date="2026-05-04")
        assert normalized.record_date == date(2025, 5, 10)
        assert "record_date" in af

    def test_none_payment_date_positive_amount(self):
        raw = _RawDividendEntry(
            period_year=2025,
            period_type="quarterly",
            period_number=1,
            amount_per_share="1.5",
            decision_date="2025-04-10",
            record_date="2025-04-05",
        )
        normalized, af = normalize_and_fill_dividend(raw, upload_date="2026-05-04")
        assert normalized.payment_date == date(2025, 6, 10)
        assert "payment_date" in af

    def test_none_payment_date_zero_amount(self):
        raw = _RawDividendEntry(
            period_year=2025,
            period_type="quarterly",
            period_number=1,
            amount_per_share="0",
            decision_date="2025-04-10",
            record_date="2025-04-05",
        )
        normalized, af = normalize_and_fill_dividend(raw, upload_date="2026-05-04")
        assert normalized.payment_date == date(2025, 4, 11)
        assert "payment_date" in af

    def test_all_none_fields_autofilled(self):
        raw = _RawDividendEntry(amount_per_share="0.5")
        normalized, af = normalize_and_fill_dividend(raw, upload_date="2025-05-15")
        assert "share_type" in af
        assert "period_type" in af
        assert "period_number" in af
        assert "decision_date" in af
        assert "record_date" in af
        assert "payment_date" in af
        assert isinstance(normalized, EpfrDividendEntry)

    def test_validation_error_recovery_path(self):
        raw = _RawDividendEntry(
            period_year=2025,
            period_type="annual",
            period_number=1,
            amount_per_share="1.0",
            decision_date="2025-06-10",
            record_date="2025-07-15",
            payment_date="2025-06-10",
        )
        normalized, af = normalize_and_fill_dividend(raw, upload_date="2026-05-04")
        assert normalized.record_date <= normalized.decision_date
        assert normalized.payment_date > normalized.decision_date
        assert "record_date_corrected" in af
        assert "payment_date_corrected" in af

    def test_invalid_period_type_defaults_to_annual(self):
        raw = _RawDividendEntry(
            period_year=2025,
            period_type="monthly",
            period_number=1,
            amount_per_share="1.0",
            decision_date="2025-04-10",
            record_date="2025-04-05",
            payment_date="2025-06-10",
        )
        normalized, af = normalize_and_fill_dividend(raw, upload_date="2026-05-04")
        assert normalized.period_type == "annual"


# ==================== Phase 3: AIDistiller Class Tests ====================


class TestAIDistillerInit:
    def test_replaces_reference_date_placeholder(self, monkeypatch, tmp_path):
        prompt_dir = tmp_path / "prompts"
        prompt_dir.mkdir()
        (prompt_dir / "dividends_parsing.md").write_text("System {{REFERENCE_DATE}} end", encoding="utf-8")
        monkeypatch.setattr(ai_distiller, "__file__", str(tmp_path / "ai_distiller.py"))
        monkeypatch.setattr(ai_distiller, "_PROMPT_TEMPLATE", None)

        distiller = AIDistiller(model_name="test-model", temperature=0.5, reference_date="2025-01-01")
        assert "2025-01-01" in distiller.system_instruction
        assert "{{REFERENCE_DATE}}" not in distiller.system_instruction

    def test_sets_max_tokens(self, monkeypatch, tmp_path):
        prompt_dir = tmp_path / "prompts"
        prompt_dir.mkdir()
        (prompt_dir / "dividends_parsing.md").write_text("prompt", encoding="utf-8")
        monkeypatch.setattr(ai_distiller, "__file__", str(tmp_path / "ai_distiller.py"))
        monkeypatch.setattr(ai_distiller, "_PROMPT_TEMPLATE", None)

        distiller = AIDistiller(model_name="test", temperature=0.0, reference_date="2025-01-01")
        assert distiller.max_tokens == 4000

    def test_stores_model_and_temperature(self, monkeypatch, tmp_path):
        prompt_dir = tmp_path / "prompts"
        prompt_dir.mkdir()
        (prompt_dir / "dividends_parsing.md").write_text("prompt", encoding="utf-8")
        monkeypatch.setattr(ai_distiller, "__file__", str(tmp_path / "ai_distiller.py"))
        monkeypatch.setattr(ai_distiller, "_PROMPT_TEMPLATE", None)

        distiller = AIDistiller(model_name="mistral-large", temperature=0.3, reference_date="2025-01-01")
        assert distiller.model_name == "mistral-large"
        assert distiller.temperature == 0.3


class TestAIDistillerExtract:
    def _make_distiller(self):
        d = AIDistiller.__new__(AIDistiller)
        d.model_name = "test"
        d.temperature = 0.0
        d.max_tokens = 4000
        d.system_instruction = "system prompt"
        return d

    def test_successful_extraction(self, monkeypatch):
        distiller = self._make_distiller()
        expected = _RawExtraction(has_dividends=True, ai_comment="test", dividends=[])
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = expected.model_dump_json()

        async def fake_chat_parse(request, response_format):
            return mock_response

        monkeypatch.setattr(ai_distiller, "mistralai_chat_parse", fake_chat_parse)
        result = asyncio.run(distiller.extract("some markdown"))
        assert result.has_dividends is True
        assert result.ai_comment == "test"

    def test_empty_choices_raises(self, monkeypatch):
        distiller = self._make_distiller()
        mock_response = MagicMock()
        mock_response.choices = []

        async def fake_chat_parse(request, response_format):
            return mock_response

        monkeypatch.setattr(ai_distiller, "mistralai_chat_parse", fake_chat_parse)
        with pytest.raises(ValueError, match="No parsed response"):
            asyncio.run(distiller.extract("text"))

    def test_none_message_raises(self, monkeypatch):
        distiller = self._make_distiller()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message = None

        async def fake_chat_parse(request, response_format):
            return mock_response

        monkeypatch.setattr(ai_distiller, "mistralai_chat_parse", fake_chat_parse)
        with pytest.raises(ValueError, match="No parsed response"):
            asyncio.run(distiller.extract("text"))

    def test_empty_string_content_raises(self, monkeypatch):
        distiller = self._make_distiller()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = ""

        async def fake_chat_parse(request, response_format):
            return mock_response

        monkeypatch.setattr(ai_distiller, "mistralai_chat_parse", fake_chat_parse)
        with pytest.raises(ValueError, match="No parsed response"):
            asyncio.run(distiller.extract("text"))

    def test_extracts_dividend_entries(self, monkeypatch):
        distiller = self._make_distiller()
        raw = _RawExtraction(
            has_dividends=True,
            ai_comment="found",
            dividends=[
                _RawDividendEntry(
                    share_type="common",
                    period_year=2025,
                    period_type="annual",
                    period_number=1,
                    amount_per_share="0.5",
                    decision_date="2025-04-10",
                    record_date="2025-04-05",
                    payment_date="2025-06-10",
                )
            ],
        )
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = raw.model_dump_json()

        async def fake_chat_parse(request, response_format):
            return mock_response

        monkeypatch.setattr(ai_distiller, "mistralai_chat_parse", fake_chat_parse)
        result = asyncio.run(distiller.extract("dividend doc"))
        assert len(result.dividends) == 1
        assert result.dividends[0].share_type == "common"


class TestExtractWithRetryAdditional:
    def test_rate_limited_uses_longer_backoff(self, monkeypatch, tmp_path):
        distiller = AIDistiller.__new__(AIDistiller)
        attempts = []
        expected = _RawExtraction(has_dividends=False, ai_comment="rate limited")
        wait_calls = []

        async def fake_extract(text):
            attempts.append(text)
            if len(attempts) == 1:
                raise Exception("429 rate limit exceeded")
            return expected

        async def fake_sleep(w):
            pass

        distiller.extract = fake_extract
        monkeypatch.setattr(
            ai_distiller,
            "_compute_retry_wait_seconds",
            lambda attempt, *, is_rate_limited: (wait_calls.append(is_rate_limited), 5.0)[1],
        )
        monkeypatch.setattr(ai_distiller.asyncio, "sleep", fake_sleep)
        result = asyncio.run(distiller.extract_with_retry("text", max_retries=2, file_path=tmp_path / "f.md"))
        assert result == expected
        assert wait_calls == [True]

    def test_cancelled_error_is_retryable(self, monkeypatch, tmp_path):
        distiller = AIDistiller.__new__(AIDistiller)
        attempts = []
        expected = _RawExtraction(has_dividends=True, ai_comment="ok")

        async def fake_extract(text):
            attempts.append(text)
            if len(attempts) == 1:
                raise asyncio.CancelledError()
            return expected

        async def fake_sleep(w):
            pass

        distiller.extract = fake_extract
        monkeypatch.setattr(ai_distiller, "_compute_retry_wait_seconds", lambda a, *, is_rate_limited: 0.1)
        monkeypatch.setattr(ai_distiller.asyncio, "sleep", fake_sleep)
        result = asyncio.run(distiller.extract_with_retry("text", max_retries=2, file_path=tmp_path / "f.md"))
        assert result == expected
        assert len(attempts) == 2

    def test_non_retryable_error_raises_immediately(self, monkeypatch, tmp_path):
        distiller = AIDistiller.__new__(AIDistiller)

        async def fake_extract(text):
            raise ValueError("bad input data")

        distiller.extract = fake_extract
        with pytest.raises(RuntimeError, match="AI extraction failed"):
            asyncio.run(distiller.extract_with_retry("text", max_retries=3, file_path=tmp_path / "f.md"))

    def test_max_retries_one_succeeds(self, monkeypatch, tmp_path):
        distiller = AIDistiller.__new__(AIDistiller)
        expected = _RawExtraction(has_dividends=True, ai_comment="ok")

        async def fake_extract(text):
            return expected

        distiller.extract = fake_extract
        result = asyncio.run(distiller.extract_with_retry("text", max_retries=1, file_path=tmp_path / "f.md"))
        assert result == expected

    def test_max_retries_zero_raises_on_failure(self, monkeypatch, tmp_path):
        distiller = AIDistiller.__new__(AIDistiller)

        async def fake_extract(text):
            raise RuntimeError("503 overload")

        distiller.extract = fake_extract
        with pytest.raises(RuntimeError, match="Unexpected retry loop completion"):
            asyncio.run(distiller.extract_with_retry("text", max_retries=0, file_path=tmp_path / "f.md"))

    def test_overload_is_retryable(self, monkeypatch, tmp_path):
        distiller = AIDistiller.__new__(AIDistiller)
        attempts = []
        expected = _RawExtraction(has_dividends=False, ai_comment="ok")

        async def fake_extract(text):
            attempts.append(1)
            if len(attempts) == 1:
                raise RuntimeError("502 bad gateway")
            return expected

        async def fake_sleep(w):
            pass

        distiller.extract = fake_extract
        monkeypatch.setattr(ai_distiller, "_compute_retry_wait_seconds", lambda a, *, is_rate_limited: 0.1)
        monkeypatch.setattr(ai_distiller.asyncio, "sleep", fake_sleep)
        result = asyncio.run(distiller.extract_with_retry("text", max_retries=2, file_path=tmp_path / "f.md"))
        assert result == expected


# ==================== Phase 4: _compute_retry_wait_seconds Expanded ====================


class TestComputeRetryWaitAdditional:
    def test_returns_at_least_0_1(self, monkeypatch):
        monkeypatch.setattr(
            ai_distiller,
            "load_epfr_config",
            lambda: SimpleNamespace(
                ai_retry_backoff_base=2,
                ai_retry_backoff_max=10,
                ai_retry_backoff_max_429=30,
                ai_retry_jitter_ratio=1.0,
            ),
        )
        monkeypatch.setattr(ai_distiller.random, "uniform", lambda low, high: -100)
        assert ai_distiller._compute_retry_wait_seconds(0, is_rate_limited=False) == 0.1

    def test_non_rate_limited_capped_at_max(self, monkeypatch):
        monkeypatch.setattr(
            ai_distiller,
            "load_epfr_config",
            lambda: SimpleNamespace(
                ai_retry_backoff_base=2,
                ai_retry_backoff_max=5,
                ai_retry_backoff_max_429=30,
                ai_retry_jitter_ratio=0.0,
            ),
        )
        monkeypatch.setattr(ai_distiller.random, "uniform", lambda low, high: 0)
        result = ai_distiller._compute_retry_wait_seconds(10, is_rate_limited=False)
        assert result <= 5.0

    def test_rate_limited_capped_at_429_max(self, monkeypatch):
        monkeypatch.setattr(
            ai_distiller,
            "load_epfr_config",
            lambda: SimpleNamespace(
                ai_retry_backoff_base=2,
                ai_retry_backoff_max=5,
                ai_retry_backoff_max_429=15,
                ai_retry_jitter_ratio=0.0,
            ),
        )
        monkeypatch.setattr(ai_distiller.random, "uniform", lambda low, high: 0)
        result = ai_distiller._compute_retry_wait_seconds(10, is_rate_limited=True)
        assert result <= 15.0

    def test_result_rounded_to_2_decimal_places(self, monkeypatch):
        monkeypatch.setattr(
            ai_distiller,
            "load_epfr_config",
            lambda: SimpleNamespace(
                ai_retry_backoff_base=3,
                ai_retry_backoff_max=100,
                ai_retry_backoff_max_429=100,
                ai_retry_jitter_ratio=0.1,
            ),
        )
        monkeypatch.setattr(ai_distiller.random, "uniform", lambda low, high: 0.333)
        result = ai_distiller._compute_retry_wait_seconds(0, is_rate_limited=False)
        assert result == round(result, 2)


# ==================== Phase 5: run_ai_distillation Integration Tests ====================


def _make_file_entry(filename, file_id=1, original_name="Test", upload_date="2026-01-15"):
    return {
        "id": file_id,
        "filename": filename,
        "original_name": original_name,
        "upload_date": upload_date,
    }


class TestRunAiDistillationIntegration:
    def _run_with_mapping(self, monkeypatch, tmp_path, mapping, fixture_raw=None, **kwargs):
        if fixture_raw is None:
            fixture_raw = {}

        for unp, data in mapping.items():
            if not isinstance(data, dict):
                continue
            unp_dir = tmp_path / unp
            unp_dir.mkdir(exist_ok=True)
            for f in data.get("files", []):
                if isinstance(f, dict):
                    (unp_dir / f["filename"]).write_text("# markdown", encoding="utf-8")

        (tmp_path / "unp_file_mapping.json").write_text(json.dumps(mapping, ensure_ascii=False), encoding="utf-8")

        class FakeDistiller:
            def __init__(self_inner, model_name, temperature, reference_date):
                pass

            async def extract_with_retry(self_inner, markdown_text, max_retries, file_path):
                if file_path.name in fixture_raw:
                    return fixture_raw[file_path.name]
                return _RawExtraction(has_dividends=False, ai_comment="default")

        monkeypatch.setattr(ai_distiller, "AIDistiller", FakeDistiller)
        monkeypatch.setattr(ai_distiller, "datetime", _FixedNowDateTime)

        defaults = dict(
            output_dir=str(tmp_path),
            mapping_filename="unp_file_mapping.json",
            output_filename="ai_distilled_dividends.json",
            model_name="fake",
            temperature=0.0,
            max_retries=2,
            file_delay_seconds=0.0,
            unps=None,
        )
        defaults.update(kwargs)
        return asyncio.run(ai_distiller.run_ai_distillation(EpfrAiDistillerInput(**defaults)))

    def test_mapping_not_found_raises(self, monkeypatch, tmp_path):
        monkeypatch.setattr(ai_distiller, "datetime", _FixedNowDateTime)
        with pytest.raises(FileNotFoundError, match="Mapping file not found"):
            asyncio.run(
                ai_distiller.run_ai_distillation(
                    EpfrAiDistillerInput(
                        output_dir=str(tmp_path),
                        mapping_filename="missing.json",
                        output_filename="out.json",
                        model_name="m",
                        temperature=0.0,
                        max_retries=1,
                        file_delay_seconds=0.0,
                    )
                )
            )

    def test_unp_filter_skips_non_selected(self, monkeypatch, tmp_path):
        mapping = {
            "111": {"title": "Co A", "holder_id": 1, "files": [_make_file_entry("a.md")]},
            "222": {"title": "Co B", "holder_id": 2, "files": [_make_file_entry("b.md")]},
        }
        stats = self._run_with_mapping(
            monkeypatch,
            tmp_path,
            mapping,
            fixture_raw={"a.md": _RawExtraction(has_dividends=False, ai_comment="no")},
            unps=["111"],
        )
        assert stats["total_companies"] == 1
        output = json.loads((tmp_path / "ai_distilled_dividends.json").read_text())
        assert "111" in output
        assert "222" not in output

    def test_no_unp_filter_processes_all(self, monkeypatch, tmp_path):
        mapping = {
            "111": {"title": "Co A", "holder_id": 1, "files": [_make_file_entry("a.md")]},
            "222": {"title": "Co B", "holder_id": 2, "files": [_make_file_entry("b.md")]},
        }
        stats = self._run_with_mapping(
            monkeypatch,
            tmp_path,
            mapping,
            fixture_raw={
                "a.md": _RawExtraction(has_dividends=False, ai_comment="ok"),
                "b.md": _RawExtraction(has_dividends=False, ai_comment="ok"),
            },
        )
        assert stats["total_companies"] == 2
        assert stats["total_files"] == 2

    def test_non_dict_file_entry_skipped(self, monkeypatch, tmp_path):
        mapping = {"111": {"title": "Co A", "holder_id": 1, "files": ["not_a_dict", 42]}}
        stats = self._run_with_mapping(monkeypatch, tmp_path, mapping)
        assert stats["total_files"] == 0

    def test_non_markdown_file_skipped(self, monkeypatch, tmp_path):
        mapping = {
            "111": {
                "title": "Co A",
                "holder_id": 1,
                "files": [_make_file_entry("report.pdf"), _make_file_entry("data.xlsx")],
            }
        }
        stats = self._run_with_mapping(monkeypatch, tmp_path, mapping)
        assert stats["total_files"] == 0

    def test_missing_md_file_records_error(self, monkeypatch, tmp_path):
        mapping = {"111": {"title": "Co A", "holder_id": 1, "files": [_make_file_entry("missing.md")]}}
        (tmp_path / "111").mkdir()
        (tmp_path / "unp_file_mapping.json").write_text(json.dumps(mapping, ensure_ascii=False), encoding="utf-8")

        class FakeDistiller:
            def __init__(self_inner, *a, **kw):
                pass

            async def extract_with_retry(self_inner, *a, **kw):
                return _RawExtraction()

        monkeypatch.setattr(ai_distiller, "AIDistiller", FakeDistiller)
        monkeypatch.setattr(ai_distiller, "datetime", _FixedNowDateTime)
        stats = asyncio.run(
            ai_distiller.run_ai_distillation(
                EpfrAiDistillerInput(
                    output_dir=str(tmp_path),
                    mapping_filename="unp_file_mapping.json",
                    output_filename="out.json",
                    model_name="m",
                    temperature=0.0,
                    max_retries=1,
                    file_delay_seconds=0.0,
                )
            )
        )
        assert stats["failed"] == 1
        assert stats["successful"] == 0
        assert len(stats["failed_files"]) == 1

    def test_empty_md_file_records_error(self, monkeypatch, tmp_path):
        mapping = {"111": {"title": "Co A", "holder_id": 1, "files": [_make_file_entry("empty.md")]}}
        (tmp_path / "111").mkdir()
        (tmp_path / "111" / "empty.md").write_text("   \n  \n", encoding="utf-8")
        (tmp_path / "unp_file_mapping.json").write_text(json.dumps(mapping, ensure_ascii=False), encoding="utf-8")

        class FakeDistiller:
            def __init__(self_inner, *a, **kw):
                pass

            async def extract_with_retry(self_inner, *a, **kw):
                return _RawExtraction()

        monkeypatch.setattr(ai_distiller, "AIDistiller", FakeDistiller)
        monkeypatch.setattr(ai_distiller, "datetime", _FixedNowDateTime)
        stats = asyncio.run(
            ai_distiller.run_ai_distillation(
                EpfrAiDistillerInput(
                    output_dir=str(tmp_path),
                    mapping_filename="unp_file_mapping.json",
                    output_filename="out.json",
                    model_name="m",
                    temperature=0.0,
                    max_retries=1,
                    file_delay_seconds=0.0,
                )
            )
        )
        assert stats["failed"] == 1

    def test_company_with_no_md_files_excluded(self, monkeypatch, tmp_path):
        mapping = {"111": {"title": "Co A", "holder_id": 1, "files": [_make_file_entry("report.pdf")]}}
        stats = self._run_with_mapping(monkeypatch, tmp_path, mapping)
        assert stats["total_companies"] == 0

    def test_file_delay_sleeps_between_files(self, monkeypatch, tmp_path):
        mapping = {
            "111": {
                "title": "Co A",
                "holder_id": 1,
                "files": [_make_file_entry("a.md"), _make_file_entry("b.md")],
            }
        }
        sleeps = []

        async def fake_sleep(w):
            sleeps.append(w)

        monkeypatch.setattr(ai_distiller.asyncio, "sleep", fake_sleep)
        self._run_with_mapping(
            monkeypatch,
            tmp_path,
            mapping,
            fixture_raw={
                "a.md": _RawExtraction(has_dividends=False, ai_comment="ok"),
                "b.md": _RawExtraction(has_dividends=False, ai_comment="ok"),
            },
            file_delay_seconds=0.5,
        )
        assert sleeps == [0.5, 0.5]

    def test_atomic_write_produces_valid_json(self, monkeypatch, tmp_path):
        mapping = {"111": {"title": "Co A", "holder_id": 1, "files": [_make_file_entry("a.md")]}}
        self._run_with_mapping(
            monkeypatch,
            tmp_path,
            mapping,
            fixture_raw={"a.md": _RawExtraction(has_dividends=True, ai_comment="ok")},
        )
        output = json.loads((tmp_path / "ai_distilled_dividends.json").read_text())
        assert isinstance(output, dict)
        assert "111" in output

    def test_no_temp_files_left(self, monkeypatch, tmp_path):
        mapping = {"111": {"title": "Co A", "holder_id": 1, "files": [_make_file_entry("a.md")]}}
        self._run_with_mapping(
            monkeypatch,
            tmp_path,
            mapping,
            fixture_raw={"a.md": _RawExtraction(has_dividends=False, ai_comment="ok")},
        )
        temp_files = list(tmp_path.glob(".ai_distilled_*"))
        assert temp_files == []

    def test_none_output_dir_raises(self, monkeypatch):
        monkeypatch.setattr(ai_distiller, "datetime", _FixedNowDateTime)
        with pytest.raises(AssertionError):
            asyncio.run(
                ai_distiller.run_ai_distillation(
                    EpfrAiDistillerInput(
                        output_dir=None,
                        mapping_filename="m.json",
                        output_filename="o.json",
                        model_name="m",
                        temperature=0.0,
                        max_retries=1,
                        file_delay_seconds=0.0,
                    )
                )
            )

    def test_non_dict_company_data_graceful(self, monkeypatch, tmp_path):
        mapping = {"111": "not a dict"}
        (tmp_path / "unp_file_mapping.json").write_text(json.dumps(mapping), encoding="utf-8")

        class FakeDistiller:
            def __init__(self_inner, *a, **kw):
                pass

            async def extract_with_retry(self_inner, *a, **kw):
                return _RawExtraction()

        monkeypatch.setattr(ai_distiller, "AIDistiller", FakeDistiller)
        monkeypatch.setattr(ai_distiller, "datetime", _FixedNowDateTime)
        stats = asyncio.run(
            ai_distiller.run_ai_distillation(
                EpfrAiDistillerInput(
                    output_dir=str(tmp_path),
                    mapping_filename="unp_file_mapping.json",
                    output_filename="out.json",
                    model_name="m",
                    temperature=0.0,
                    max_retries=1,
                    file_delay_seconds=0.0,
                )
            )
        )
        assert stats["total_files"] == 0

    def test_non_list_files_graceful(self, monkeypatch, tmp_path):
        mapping = {"111": {"title": "Co", "holder_id": 1, "files": "not a list"}}
        (tmp_path / "unp_file_mapping.json").write_text(json.dumps(mapping), encoding="utf-8")

        class FakeDistiller:
            def __init__(self_inner, *a, **kw):
                pass

            async def extract_with_retry(self_inner, *a, **kw):
                return _RawExtraction()

        monkeypatch.setattr(ai_distiller, "AIDistiller", FakeDistiller)
        monkeypatch.setattr(ai_distiller, "datetime", _FixedNowDateTime)
        stats = asyncio.run(
            ai_distiller.run_ai_distillation(
                EpfrAiDistillerInput(
                    output_dir=str(tmp_path),
                    mapping_filename="unp_file_mapping.json",
                    output_filename="out.json",
                    model_name="m",
                    temperature=0.0,
                    max_retries=1,
                    file_delay_seconds=0.0,
                )
            )
        )
        assert stats["total_files"] == 0

    def test_no_dividends_ai_comment_preserved(self, monkeypatch, tmp_path):
        mapping = {"111": {"title": "Co A", "holder_id": 1, "files": [_make_file_entry("a.md")]}}
        self._run_with_mapping(
            monkeypatch,
            tmp_path,
            mapping,
            fixture_raw={"a.md": _RawExtraction(has_dividends=False, ai_comment="no dividends here")},
        )
        output = json.loads((tmp_path / "ai_distilled_dividends.json").read_text())
        assert output["111"]["files"][0]["ai_comment"] == "no dividends here"
        assert output["111"]["files"][0]["has_dividends"] is False

    def test_multiple_dividends_per_file(self, monkeypatch, tmp_path):
        mapping = {"111": {"title": "Co A", "holder_id": 1, "files": [_make_file_entry("a.md")]}}
        self._run_with_mapping(
            monkeypatch,
            tmp_path,
            mapping,
            fixture_raw={
                "a.md": _RawExtraction(
                    has_dividends=True,
                    ai_comment="both share types",
                    dividends=[
                        _RawDividendEntry(
                            share_type="common",
                            period_year=2025,
                            period_type="annual",
                            period_number=1,
                            amount_per_share="0.5",
                            decision_date="2025-04-10",
                            record_date="2025-04-05",
                            payment_date="2025-06-10",
                        ),
                        _RawDividendEntry(
                            share_type="preferred",
                            period_year=2025,
                            period_type="annual",
                            period_number=1,
                            amount_per_share="0.3",
                            decision_date="2025-04-10",
                            record_date="2025-04-05",
                            payment_date="2025-06-10",
                        ),
                    ],
                )
            },
        )
        output = json.loads((tmp_path / "ai_distilled_dividends.json").read_text())
        assert len(output["111"]["files"][0]["dividends"]) == 2

    def test_autofilled_fields_deduplicated_sorted(self, monkeypatch, tmp_path):
        mapping = {"111": {"title": "Co A", "holder_id": 1, "files": [_make_file_entry("a.md")]}}
        self._run_with_mapping(
            monkeypatch,
            tmp_path,
            mapping,
            fixture_raw={
                "a.md": _RawExtraction(
                    has_dividends=True,
                    ai_comment="ok",
                    dividends=[
                        _RawDividendEntry(share_type="common", amount_per_share="1.0", decision_date="2025-04-10"),
                        _RawDividendEntry(share_type="common", amount_per_share="2.0", decision_date="2025-05-10"),
                    ],
                )
            },
        )
        output = json.loads((tmp_path / "ai_distilled_dividends.json").read_text())
        fields = output["111"]["files"][0]["autofilled_fields"]
        assert fields == sorted(set(fields))

    def test_cyrillic_company_name_preserved(self, monkeypatch, tmp_path):
        mapping = {"111": {"title": "ОАО «Тест-Агро»", "holder_id": 1, "files": [_make_file_entry("a.md")]}}
        self._run_with_mapping(
            monkeypatch,
            tmp_path,
            mapping,
            fixture_raw={"a.md": _RawExtraction(has_dividends=False, ai_comment="ok")},
        )
        output = json.loads((tmp_path / "ai_distilled_dividends.json").read_text())
        assert output["111"]["company_name"] == "ОАО «Тест-Агро»"

    def test_empty_mapping_produces_empty_result(self, monkeypatch, tmp_path):
        (tmp_path / "unp_file_mapping.json").write_text("{}", encoding="utf-8")
        monkeypatch.setattr(ai_distiller, "datetime", _FixedNowDateTime)

        class FakeDistiller:
            def __init__(self_inner, *a, **kw):
                pass

        monkeypatch.setattr(ai_distiller, "AIDistiller", FakeDistiller)
        stats = asyncio.run(
            ai_distiller.run_ai_distillation(
                EpfrAiDistillerInput(
                    output_dir=str(tmp_path),
                    mapping_filename="unp_file_mapping.json",
                    output_filename="out.json",
                    model_name="m",
                    temperature=0.0,
                    max_retries=1,
                    file_delay_seconds=0.0,
                )
            )
        )
        assert stats["total_companies"] == 0
        assert stats["total_files"] == 0

    def test_file_error_does_not_stop_pipeline(self, monkeypatch, tmp_path):
        mapping = {
            "111": {
                "title": "Co A",
                "holder_id": 1,
                "files": [_make_file_entry("bad.md"), _make_file_entry("good.md")],
            }
        }

        class FakeDistiller:
            def __init__(self_inner, *a, **kw):
                pass

            async def extract_with_retry(self_inner, text, max_retries, file_path):
                if "bad" in file_path.name:
                    raise RuntimeError("extraction failed")
                return _RawExtraction(has_dividends=True, ai_comment="ok")

        monkeypatch.setattr(ai_distiller, "AIDistiller", FakeDistiller)
        monkeypatch.setattr(ai_distiller, "datetime", _FixedNowDateTime)

        unp_dir = tmp_path / "111"
        unp_dir.mkdir()
        (unp_dir / "bad.md").write_text("# bad", encoding="utf-8")
        (unp_dir / "good.md").write_text("# good", encoding="utf-8")
        (tmp_path / "unp_file_mapping.json").write_text(json.dumps(mapping, ensure_ascii=False), encoding="utf-8")

        stats = asyncio.run(
            ai_distiller.run_ai_distillation(
                EpfrAiDistillerInput(
                    output_dir=str(tmp_path),
                    mapping_filename="unp_file_mapping.json",
                    output_filename="out.json",
                    model_name="m",
                    temperature=0.0,
                    max_retries=1,
                    file_delay_seconds=0.0,
                )
            )
        )
        assert stats["successful"] == 1
        assert stats["failed"] == 1
        output = json.loads((tmp_path / "out.json").read_text())
        assert len(output["111"]["files"]) == 2
        assert output["111"]["files"][0]["error"] is not None
        assert output["111"]["files"][1]["error"] is None


# ==================== Phase 6: Workflow Wrapper Tests ====================


class TestEpfrAiDistillerWorkflow:
    def _make_wf(self):
        wf = EpfrAiDistillerWorkflow()
        run_unwrapped = EpfrAiDistillerWorkflow.run.__wrapped__.__get__(wf, EpfrAiDistillerWorkflow)
        return run_unwrapped

    def test_run_constructs_output_from_stats(self, monkeypatch, tmp_path):
        """Test workflow run constructs output from activity results."""
        from workflows.epfr.models import AiDistillerScanResult, AiDistillerProcessResult

        # Create a minimal mapping file
        mapping_path = tmp_path / "mapping.json"
        mapping_path.write_text('{"123": {"title": "Test", "holder_id": 1, "files": []}}')

        scan_result = AiDistillerScanResult(
            mapping_path=str(mapping_path),
            total_companies=3,
            total_files=10,
            work_items=[],
            output_dir=str(tmp_path),
            output_filename="o.json",
            model_name="m",
            temperature=0.0,
            max_retries=1,
            file_delay_seconds=0.0,
        )

        process_result = AiDistillerProcessResult(
            results={},
            total_files=10,
            successful=8,
            failed=2,
            failed_files=[],
            total_companies=3,
        )

        stats = {
            "output_path": str(tmp_path / "o.json"),
            "total_companies": 3,
            "total_files": 10,
            "successful": 8,
            "failed": 2,
            "extra_key": "extra_value",
        }

        async def fake_scan(input_obj):
            return scan_result

        async def fake_process(scan_result):
            return process_result

        async def fake_finalize(scan_result, process_result):
            return stats

        monkeypatch.setattr(ai_distiller_workflow, "scan_ai_distiller_files", fake_scan)
        monkeypatch.setattr(ai_distiller_workflow, "process_ai_distillation", fake_process)
        monkeypatch.setattr(ai_distiller_workflow, "finalize_ai_distillation", fake_finalize)

        inp = EpfrAiDistillerInput(
            output_dir=str(tmp_path),
            mapping_filename="mapping.json",
            model_name="m",
            temperature=0.0,
            max_retries=1,
            file_delay_seconds=0.0,
        )
        run_method = self._make_wf()
        result = asyncio.run(run_method(inp))
        assert isinstance(result, EpfrAiDistillerOutput)
        assert result.output_path == str(tmp_path / "o.json")
        assert result.total_companies == 3
        assert result.total_files == 10
        assert result.successful == 8
        assert result.failed == 2
        assert result.stats["extra_key"] == "extra_value"

    def test_run_with_zero_results(self, monkeypatch, tmp_path):
        """Test workflow run with zero files found."""
        from workflows.epfr.models import AiDistillerScanResult

        # Create a minimal mapping file
        mapping_path = tmp_path / "mapping.json"
        mapping_path.write_text('{"123": {"title": "Test", "holder_id": 1, "files": []}}')

        scan_result = AiDistillerScanResult(
            mapping_path=str(mapping_path),
            total_companies=0,
            total_files=0,
            work_items=[],
            output_dir=str(tmp_path),
            output_filename="o.json",
            model_name="m",
            temperature=0.0,
            max_retries=1,
            file_delay_seconds=0.0,
        )

        async def fake_scan(input_obj):
            return scan_result

        monkeypatch.setattr(ai_distiller_workflow, "scan_ai_distiller_files", fake_scan)

        inp = EpfrAiDistillerInput(
            output_dir=str(tmp_path),
            mapping_filename="mapping.json",
            model_name="m",
            temperature=0.0,
            max_retries=1,
            file_delay_seconds=0.0,
        )
        run_method = self._make_wf()
        result = asyncio.run(run_method(inp))
        assert result.total_companies == 0
        assert result.total_files == 0

    def test_run_handles_missing_stats_keys(self, monkeypatch, tmp_path):
        """Test workflow run handles missing stats keys."""
        from workflows.epfr.models import AiDistillerScanResult, AiDistillerProcessResult

        # Create a minimal mapping file
        mapping_path = tmp_path / "mapping.json"
        mapping_path.write_text('{"123": {"title": "Test", "holder_id": 1, "files": []}}')

        scan_result = AiDistillerScanResult(
            mapping_path=str(mapping_path),
            total_companies=0,
            total_files=0,
            work_items=[],
            output_dir=str(tmp_path),
            output_filename="o.json",
            model_name="m",
            temperature=0.0,
            max_retries=1,
            file_delay_seconds=0.0,
        )

        process_result = AiDistillerProcessResult(
            results={},
            total_files=0,
            successful=0,
            failed=0,
            failed_files=[],
            total_companies=0,
        )

        async def fake_scan(input_obj):
            return scan_result

        async def fake_process(scan_result):
            return process_result

        async def fake_finalize(scan_result, process_result):
            return {}

        monkeypatch.setattr(ai_distiller_workflow, "scan_ai_distiller_files", fake_scan)
        monkeypatch.setattr(ai_distiller_workflow, "process_ai_distillation", fake_process)
        monkeypatch.setattr(ai_distiller_workflow, "finalize_ai_distillation", fake_finalize)

        inp = EpfrAiDistillerInput(
            output_dir=str(tmp_path),
            mapping_filename="mapping.json",
            model_name="m",
            temperature=0.0,
            max_retries=1,
            file_delay_seconds=0.0,
        )
        run_method = self._make_wf()
        result = asyncio.run(run_method(inp))
        # When finalize returns empty dict, output_path comes from scan_result
        assert result.output_path == str(tmp_path / "o.json")
        assert result.total_companies == 0

"""Tests for EPFR AI distiller normalization and workflow helpers."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from .. import ai_distiller
from ..ai_distiller import _RawDividendEntry, normalize_and_fill_dividend
from ..models import EpfrDividendEntry


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

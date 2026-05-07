"""Tests for EPFR AI distiller normalization and workflow helpers."""

from datetime import date
from decimal import Decimal

import pytest

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
    assert normalized.period_year == 2026
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

    assert normalized.payment_date == date(2025, 3, 29)
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

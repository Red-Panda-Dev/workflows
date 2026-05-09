"""Tests for markdown cleanup helpers."""

# ruff: noqa: D102

import importlib

from ..markdown_cleanup import clean_markdown_text

pytest = importlib.import_module("pytest")


@pytest.mark.parametrize(
    ("fixture_name", "expected_substrings"),
    [
        (
            "140911.md",
            (
                "Дрожжевой комбинат",
                "Дивиденды, начисленные на одну акцию",
                "0,007337",
                "Принадлежащим г. Минску",
                (
                    "|  Полное наименование акционерного общества | "
                    'Открытое акционерное общество "Дрожжевой комбинат"  |'
                ),
            ),
        ),
        (
            "140297.md",
            (
                "Сороги-Агро",
                "Дивиденды, начисленные на одну простую",
                "0,0124 белорусских рублей",
                "пропорционально долям",
                "|Дивиденды, начисленные на одну простую|0,0124 белорусских рублей",
            ),
        ),
    ],
)
def test_clean_markdown_text_preserves_dividend_content(
    load_epfr_fixture_text,
    fixture_name,
    expected_substrings,
):
    text = load_epfr_fixture_text(fixture_name)

    cleaned = clean_markdown_text(text)

    for substring in expected_substrings:
        assert substring in cleaned

    assert cleaned.count("\n\n") >= 1


@pytest.mark.parametrize(
    ("raw_text", "expected"),
    [
        ("Title\n||||\nBody", "Title\nBody"),
        ("Lead\n![](image.png)\nTail", "Lead\nTail"),
        ("Alpha\n\n\n\nBeta", "Alpha\n\nBeta"),
        ("", ""),
    ],
)
def test_clean_markdown_text_handles_cleanup_edge_cases(raw_text, expected):
    assert clean_markdown_text(raw_text) == expected
